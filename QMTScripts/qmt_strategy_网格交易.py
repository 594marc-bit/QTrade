#coding:gbk
"""
QMT 策略：网格交易（5分钟K线驱动）

基于 Mac 端 src/grid/ 模块的核心逻辑移植，纯价格驱动，不需外部数据。
价格向下穿越网格线→买入，向上穿越网格线→卖出。

使用方式：
  1. QMT 客户端设置主图周期为 5 分钟线
  2. 运行模式选"实盘"（盘中使用，quickTrade=2 立即下单）
     或"回测"（历史回测，quickTrade=0 逐K线撮合）
  3. 修改下方 STOCKS 配置目标股票

注意事项：
  - 第一行 #coding:gbk 为 QMT 强制要求
  - 全局状态用类实例 G() 保存，不能用 ContextInfo 属性（bar 回滚会重置）
  - handlebar 每根 5 分钟 K 线触发一次
"""

import math

# ============================================================
# 用户配置
# ============================================================

STOCKS = [
    "159653.SZ",   # 可替换为你要做网格的股票
    "510010.SH",
]

PRICE_RANGE_PCT = 15     # 价格区间 ±%（以参考价为中心）
GRID_LEVELS = 10         # 网格层数
GRID_MODE = "ratio"      # 间距模式: "equal"=等间距, "ratio"=等比
ORDER_SHARES = 1000      # 每层交易股数
BASE_SHARES = 0          # 初始底仓股数（0=裸网格）

# 是否做回测模式（False=实盘立即下单）
BACKTEST_MODE = True

# ============================================================
# 全局状态（不用 ContextInfo 存）
# ============================================================

class G:
    stocks = []                           # 股票代码列表
    grid_prices = {}                      # ts_code -> [level_0, level_1, ...]
    prev_level = {}                       # ts_code -> 上一根 bar 的网格层级
    active_buy = {}                       # ts_code -> set(已挂买单的层级)
    active_sell = {}                      # ts_code -> set(已挂卖单的层级)
    holdings = {}                         # ts_code -> 当前持仓股数
    account_id = ""                       # 资金账号
    is_live = False                       # True=实盘, False=回测
    init_done = False                     # 初始化完成标志

g = G()


# ============================================================
# 网格计算（等价于 Mac 端 grid_params.py）
# ============================================================

def _compute_grid(center, pct, levels, mode):
    """根据中心价计算网格层级价格列表（低→高）。"""
    half = pct / 100.0
    upper = center * (1 + half)
    lower = center * (1 - half)

    if mode == "equal":
        step = (upper - lower) / (levels - 1)
        return [round(lower + i * step, 3) for i in range(levels)]
    else:  # ratio
        ratio = math.pow(upper / lower, 1.0 / (levels - 1))
        return [round(lower * math.pow(ratio, i), 3) for i in range(levels)]


def _get_nearest_level(price, grid_prices):
    """返回 price 所在的最大网格层级索引（price 不低于该层价格）。-1 表示低于所有层。"""
    for i in range(len(grid_prices) - 1, -1, -1):
        if price >= grid_prices[i]:
            return i
    return -1


# ============================================================
# QMT 生命周期
# ============================================================

def init(C):
    """策略加载时执行：计算网格参数，初始化状态。"""
    g.stocks = STOCKS

    # 获取资金账号（C.accID 在某些 QMT 版本不存在，用全局 account 兜底）
    acc_id = getattr(C, 'accID', '') or ''
    if not acc_id:
        try:
            acc_id = str(account)
        except NameError:
            acc_id = 'test'
    g.account_id = acc_id

    # 判断运行模式
    g.is_live = C.is_last_bar() if hasattr(C, 'is_last_bar') else False

    for stock in g.stocks:
        # 取最近 20 个交易日收盘价，以最新收盘价作为网格中心
        try:
            data = C.get_market_data_ex(
                ['close'], [stock],
                period='1d', count=20,
                dividend_type='front_ratio',
                subscribe=False,
            )
            if data is None or stock not in data or data[stock].empty:
                print(f"[init] {stock} 无日线数据，跳过")
                continue
            ref_price = float(data[stock]['close'].iloc[-1])
        except Exception as e:
            print(f"[init] {stock} 获取日线失败: {e}")
            continue

        # 计算网格价格
        g.grid_prices[stock] = _compute_grid(
            ref_price, PRICE_RANGE_PCT, GRID_LEVELS, GRID_MODE
        )
        g.prev_level[stock] = _get_nearest_level(ref_price, g.grid_prices[stock])
        g.active_buy[stock] = set()
        g.active_sell[stock] = set()

        # 初始持仓
        g.holdings[stock] = BASE_SHARES

        levels_str = ", ".join(f"{p:.3f}" for p in g.grid_prices[stock])
        print(f"[init] {stock}: 参考价={ref_price:.3f}, "
              f"网格区间=[{g.grid_prices[stock][0]:.3f}, {g.grid_prices[stock][-1]:.3f}], "
              f"当前层={g.prev_level[stock]}")
        print(f"  网格价格: {levels_str}")

    g.init_done = True
    print(f"[init] 网格交易策略初始化完成，共 {len(g.grid_prices)} 只股票")


def handlebar(C):
    """每根 5 分钟 K 线触发一次。"""
    if not g.init_done:
        return

    # 实盘模式：只在最后一根 bar 执行（避免历史 bar 重复下单）
    if g.is_live and not C.is_last_bar():
        return

    # 回测模式：检查是否为交易日（跳过非交易时段的第一根 bar）
    bar_time = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    time_str = bar_time[8:14] if len(bar_time) >= 14 else "000000"

    # 盘前/盘后 bar 跳过
    if time_str < "093000" or time_str > "150000":
        return

    # 处理每只股票
    for stock in g.stocks:
        if stock not in g.grid_prices:
            continue

        try:
            _process_stock(C, stock, bar_time, time_str)
        except Exception as e:
            print(f"[handlebar] {stock} 处理失败: {e}")


# ============================================================
# 单股票网格处理
# ============================================================

def _process_stock(C, stock, bar_time, time_str):
    """检查单只股票的当前 bar 是否穿越网格线，并下单。"""
    # 获取当前 bar 收盘价
    bar_data = _get_bar_close(C, stock)
    if bar_data is None:
        return
    current_price = bar_data

    grid_prices = g.grid_prices[stock]
    prev_lv = g.prev_level.get(stock, -1)
    curr_lv = _get_nearest_level(current_price, grid_prices)

    if curr_lv == prev_lv:
        return  # 未穿越网格线

    # ---- 价格向上穿越：SELL ----
    if curr_lv > prev_lv:
        for lv in range(prev_lv + 1, curr_lv + 1):
            gp = grid_prices[lv]
            # 跳过已挂单的层级
            if lv in g.active_sell.get(stock, set()):
                continue
            # 跳过没有持仓可卖的层级
            if g.holdings.get(stock, 0) < ORDER_SHARES:
                continue

            if g.is_live:
                # 实盘模式：立即下限价单
                msg = f"网格:{stock}:L{lv}:SELL"
                passorder(
                    24, 1101, g.account_id, stock,
                    11, gp, ORDER_SHARES,
                    '网格策略', 2, msg, C
                )
                print(f"[{bar_time}] {stock} SELL {ORDER_SHARES}股 @{gp:.3f} (层{lv})")
            else:
                # 回测模式：逐K线撮合
                passorder(
                    24, 1101, g.account_id, stock,
                    11, gp, ORDER_SHARES,
                    '网格策略', 0, f"网格:{stock}:L{lv}:SELL", C
                )

            g.active_sell.setdefault(stock, set()).add(lv)

    # ---- 价格向下穿越：BUY ----
    elif curr_lv < prev_lv:
        for lv in range(prev_lv, curr_lv, -1):
            gp = grid_prices[lv]
            if lv in g.active_buy.get(stock, set()):
                continue

            if g.is_live:
                msg = f"网格:{stock}:L{lv}:BUY"
                passorder(
                    23, 1101, g.account_id, stock,
                    11, gp, ORDER_SHARES,
                    '网格策略', 2, msg, C
                )
                print(f"[{bar_time}] {stock} BUY {ORDER_SHARES}股 @{gp:.3f} (层{lv})")
            else:
                passorder(
                    23, 1101, g.account_id, stock,
                    11, gp, ORDER_SHARES,
                    '网格策略', 0, f"网格:{stock}:L{lv}:BUY", C
                )

            g.active_buy.setdefault(stock, set()).add(lv)

    g.prev_level[stock] = curr_lv


def _get_bar_close(C, stock):
    """获取当前 bar 的收盘价。"""
    try:
        data = C.get_market_data_ex(
            ['close'], [stock],
            period='5m', count=1,
            dividend_type='front_ratio',
            subscribe=False,
        )
        if data and stock in data and not data[stock].empty:
            return float(data[stock]['close'].iloc[-1])
    except Exception:
        pass
    return None


# ============================================================
# 成交回报处理（实盘模式用）
# ============================================================

def _on_deal(C):
    """成交回报回调——清除已成交层级的挂单标记，更新持仓。

    在实盘模式中，可以通过 subscribe 或者定时查成交来触发。
    这里提供逻辑框架，实际使用时在 handlebar 中定期调用。
    """
    try:
        deals = get_trade_detail_data(g.account_id, 'STOCK', 'deal')
    except Exception:
        return

    for deal in deals:
        remark = getattr(deal, 'm_strRemark', '')
        if not remark.startswith('网格:'):
            continue

        stock = getattr(deal, 'm_strInstrumentID', '') + '.' + getattr(deal, 'm_strExchangeID', '')
        vol = int(getattr(deal, 'm_nVolume', 0))
        price = float(getattr(deal, 'm_dPrice', 0))
        direction = 'BUY' if 'BUY' in remark else 'SELL'

        # 解析网格层级
        try:
            lv = int(remark.split(':L')[1].split(':')[0])
        except (IndexError, ValueError):
            lv = -1

        # 更新状态
        if direction == 'BUY':
            g.holdings[stock] = g.holdings.get(stock, 0) + vol
            if lv >= 0:
                g.active_buy.get(stock, set()).discard(lv)
                g.active_sell.get(stock, set()).discard(lv)  # 同一层卖单可重新挂
        else:
            g.holdings[stock] = max(0, g.holdings.get(stock, 0) - vol)
            if lv >= 0:
                g.active_sell.get(stock, set()).discard(lv)
                g.active_buy.get(stock, set()).discard(lv)   # 同一层买单可重新挂

        print(f"[成交] {stock} {direction} {vol}股 @{price:.3f} 持仓={g.holdings.get(stock, 0)}")

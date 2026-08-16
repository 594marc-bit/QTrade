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
    "512480.SH",   # 可替换为你要做网格的股票
    "512290.SH",
]

PRICE_RANGE_PCT = 15     # 价格区间 ±%（以参考价为中心）
GRID_LEVELS = 10         # 网格层数
GRID_MODE = "ratio"      # 间距模式: "equal"=等间距, "ratio"=等比
ORDER_SHARES = 10000      # 每层交易股数
BASE_SHARES = 0          # 初始底仓股数（0=裸网格）
COOLDOWN_SECONDS = 300   # 同层冷却时间（秒），防网格线震荡反复交易

# 是否做回测模式（False=实盘立即下单）
BACKTEST_MODE = False

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
    _last_deal_time = {}                  # (stock, level) -> datetime，同层冷却
    _skip_first_bar = True                # init 后首个实时 bar 跳过，防虚假穿越

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
    # QMT 中: capital > 0 表示回测模式(用户设置了初始资金), capital <= 0 表示实盘/模拟
    if hasattr(C, 'capital') and C.capital > 0:
        g.is_live = False   # 回测模式
        print("[init] 检测到回测模式 (capital > 0)")
    elif BACKTEST_MODE:
        g.is_live = False   # 用户显式配置了回测模式
        print("[init] 使用 BACKTEST_MODE=True (回测模式)")
    else:
        g.is_live = True    # 模拟 / 实盘模式
        print(f"[init] 模拟/实盘模式 (capital={getattr(C, 'capital', 'N/A')})")

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
    # ---- 检查点 1: 初始化完成 ----
    if not g.init_done:
        print("[handlebar] 跳过: init 尚未完成 (g.init_done=False)")
        return

    # ---- 检查点 2: 实盘模式只处理实时 bar ----
    if g.is_live and not C.is_last_bar():
        # 启动时 QMT 会回放全部历史 K 线（可能上万根），全部跳过。
        # 只打印前 3 根，中间静默，避免日志泛滥。
        g._skip_bar_count = getattr(g, '_skip_bar_count', 0) + 1
        if g._skip_bar_count <= 3:
            bar_time = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
            print(f"[handlebar] 跳过历史K线 (bar_time={bar_time}, 累计跳过={g._skip_bar_count})")
        return

    # ---- 检查点 3: 交易时段过滤（bar 时间） ----
    bar_time = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    time_str = bar_time[8:14] if len(bar_time) >= 14 else "000000"

    if time_str < "093000" or time_str > "150000":
        g._skip_time_count = getattr(g, '_skip_time_count', 0) + 1
        if g._skip_time_count <= 3 or g._skip_time_count % 60 == 0:
            print(f"[handlebar] 非交易时段 bar_time={bar_time} time_str={time_str} "
                  f"(累计跳过={g._skip_time_count}次)")
        return

    # ---- 检查点 3.5: 交易时段过滤（wall-clock 时间） ----
    # 防止盘后策略重载 / 参数修改时，is_last_bar 的 bar 被误认为实时 bar 而下单
    if g.is_live and not _is_market_open():
        g._skip_wallclock_count = getattr(g, '_skip_wallclock_count', 0) + 1
        if g._skip_wallclock_count <= 3 or g._skip_wallclock_count % 60 == 0:
            print(f"[handlebar] 跳过: wall-clock 不在交易时段 bar_time={bar_time} "
                  f"(累计跳过={g._skip_wallclock_count}次)")
        return

    # ---- 检查点 3.6: init 后首个实时 bar 跳过 ----
    # 策略刚加载时，prev_level 基于历史收盘价设置，与当前 bar 价可能不同，
    # 首个 bar 立即触发"穿越"会产生虚假信号（盘后重载时尤其明显）
    if g.is_live and g._skip_first_bar:
        g._skip_first_bar = False
        # 用当前实时价格重新校准 prev_level，消除 init 时的偏差
        for stock in g.stocks:
            if stock not in g.grid_prices:
                continue
            live_price = _get_bar_close(C, stock)
            if live_price is not None:
                old_lv = g.prev_level.get(stock, -1)
                new_lv = _get_nearest_level(live_price, g.grid_prices[stock])
                g.prev_level[stock] = new_lv
                print(f"[handlebar] init 首个实时 bar: {stock} 重新校准层级 "
                      f"({old_lv} → {new_lv}, 价格={live_price:.3f})")
        print(f"[handlebar] init 首个实时 bar 已跳过交易，层级已校准")
        return

    # ---- 到达此处 = bar 通过所有检查，重置跳过计数器 ----
    total_skipped = getattr(g, '_skip_bar_count', 0)
    if total_skipped > 0:
        print(f"[handlebar] 历史K线回放完成（共跳过 {total_skipped} 根），开始处理实时bar: bar_time={bar_time}")
    elif getattr(g, '_skip_time_count', 0) > 0:
        print(f"[handlebar] 开始处理实时bar: bar_time={bar_time}")
    g._skip_bar_count = 0
    g._skip_time_count = 0

    # 实盘模式：处理成交回报，清除已成交的挂单标记
    if g.is_live:
        _on_deal(C)

    # 处理每只股票
    for stock in g.stocks:
        if stock not in g.grid_prices:
            continue

        try:
            _process_stock(C, stock, bar_time, time_str)
        except Exception as e:
            print(f"[handlebar] {stock} 处理失败: {e}")


# ============================================================
# 时间守卫（防盘后策略重载触发下单）
# ============================================================

def _is_market_open():
    """检查当前 wall-clock 时间是否在 A 股交易时段内（周一至周五 9:30-15:00）。

    与 bar_time 过滤互补——bar_time 过滤能拦住历史 bar 回放，
    wall-clock 过滤能拦住盘后策略重载/参数修改触发的 bar。
    """
    import datetime as _dt
    now = _dt.datetime.now()
    if now.weekday() >= 5:
        return False
    tm = now.strftime('%H%M%S')
    return '093000' <= tm <= '150000'


def _is_level_cooling(stock, lv):
    """检查 (stock, lv) 是否在冷却期内。True=冷却中，应跳过。"""
    import datetime as _dt
    key = (stock, lv)
    last = g._last_deal_time.get(key)
    if last is None:
        return False
    elapsed = (_dt.datetime.now() - last).total_seconds()
    return elapsed < COOLDOWN_SECONDS


def _process_stock(C, stock, bar_time, time_str):
    """检查单只股票的当前 bar 是否穿越网格线，并下单。"""
    # 获取当前 bar 收盘价
    bar_data = _get_bar_close(C, stock)
    if bar_data is None:
        print(f"[_process_stock] {stock} 跳过: _get_bar_close 返回 None (bar_time={bar_time})")
        return
    current_price = bar_data

    grid_prices = g.grid_prices[stock]
    prev_lv = g.prev_level.get(stock, -1)
    curr_lv = _get_nearest_level(current_price, grid_prices)

    if curr_lv == prev_lv:
        # 心跳日志：每 12 根 bar（约 1 小时）打印一次，确认策略仍在运行
        g._heartbeat_count = getattr(g, '_heartbeat_count', 0) + 1
        if g._heartbeat_count <= 3 or g._heartbeat_count % 12 == 0:
            gp_low = grid_prices[0]
            gp_high = grid_prices[-1]
            print(f"[heartbeat] {stock} 当前价={current_price:.3f} 层级={curr_lv} "
                  f"(网格区间=[{gp_low:.3f}, {gp_high:.3f}]) 无穿越 "
                  f"(心跳#{g._heartbeat_count})")
        return

    # ---- 价格向上穿越：SELL ----
    if curr_lv > prev_lv:
        for lv in range(prev_lv + 1, curr_lv + 1):
            gp = grid_prices[lv]
            # 冷却检查：同层刚成交过，跳过防震荡
            if _is_level_cooling(stock, lv):
                continue
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
            # 冷却检查：同层刚成交过，跳过防震荡
            if _is_level_cooling(stock, lv):
                continue
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
    """获取当前 bar 的股票价格。返回 float 或 None。

    优先用 get_market_data_ex 取 5 分钟线收盘价（回测兼容），
    失败时回退到 get_full_tick 取实时最新价（实盘/模拟模式适用）。
    """
    # 方式 1: 尝试 5 分钟 K 线收盘价（回测 + 已下载数据时有效）
    try:
        data = C.get_market_data_ex(
            ['close'], [stock],
            period='5m', count=1,
            dividend_type='front_ratio',
            subscribe=True,  # 实盘/模拟模式下订阅实时数据
        )
        if data is not None and stock in data and not data[stock].empty:
            return float(data[stock]['close'].iloc[-1])
    except Exception:
        pass  # 回退到方式 2

    # 方式 2: 实时行情快照（实盘/模拟模式下可用）
    try:
        tick = C.get_full_tick([stock])
        if tick is not None and stock in tick:
            last_price = float(tick[stock]['lastPrice'])
            if last_price > 0:
                return last_price
    except Exception:
        pass

    # 方式 3: 再试一次历史数据（回测模式兜底，subscribe=False 避免等待）
    try:
        data = C.get_market_data_ex(
            ['close'], [stock],
            period='1d', count=1,
            dividend_type='front_ratio',
            subscribe=False,
        )
        if data is not None and stock in data and not data[stock].empty:
            price = float(data[stock]['close'].iloc[-1])
            print(f"[_get_bar_close] {stock} 使用日线收盘价={price:.3f} (5m/tick 不可用)")
            return price
    except Exception:
        pass

    print(f"[_get_bar_close] {stock} 所有价格获取方式均失败 (5m/tick/1d)")
    return None


# ============================================================
# 成交回报处理（实盘模式用）
# ============================================================

def _on_deal(C):
    """成交回报处理——清除已成交层级的挂单标记，更新持仓。

    由 handlebar 在每根实时 bar 到达时自动调用（仅实盘/模拟模式）。
    通过 get_trade_detail_data 查询当日成交记录，匹配本策略的订单
    （通过 m_strRemark 前缀 "网格:" 识别），更新：
      - g.holdings: 实际持仓数量
      - g.active_buy / g.active_sell: 清除已成交层级的挂单标记，
        使该层级可以重新挂单
    """
    try:
        deals = get_trade_detail_data(g.account_id, 'STOCK', 'deal')
    except Exception:
        return

    # 初始化成交去重集合：QMT 每次返回当日全部成交，需跳过已处理的记录
    if not hasattr(g, '_processed_deals'):
        g._processed_deals = set()

    for deal in deals:
        remark = getattr(deal, 'm_strRemark', '')
        if not remark.startswith('网格:'):
            continue

        # QMT 成交回报的 exchangeID 可能是 "SH"/"SZ" 或 "XSHG"/"XSHE"，统一格式
        raw_exchange = getattr(deal, 'm_strExchangeID', '')
        exchange = raw_exchange.replace('XSHG', 'SH').replace('XSHE', 'SZ')
        stock = getattr(deal, 'm_strInstrumentID', '') + '.' + exchange
        # 只处理本策略管理的股票
        if stock not in g.stocks:
            continue

        vol = int(getattr(deal, 'm_nVolume', 0))
        price = float(getattr(deal, 'm_dPrice', 0))

        # ---- 去重：跳过已处理的成交记录 ----
        # 用 (remark, 成交时间, 成交量, 价格) 组合作为唯一标识。
        # m_strTradeTime 在部分 QMT 版本中可能不存在，回退为空串。
        trade_time = getattr(deal, 'm_strTradeTime', '') or ''
        deal_key = (remark, trade_time, vol, price)
        if deal_key in g._processed_deals:
            continue
        g._processed_deals.add(deal_key)

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
                # 买入成交后，同一层可挂卖单（有了持仓才能卖）
                g.active_sell.get(stock, set()).discard(lv)
        else:
            g.holdings[stock] = max(0, g.holdings.get(stock, 0) - vol)
            if lv >= 0:
                g.active_sell.get(stock, set()).discard(lv)
                # 卖出成交后，同一层可挂买单（释放了持仓）
                g.active_buy.get(stock, set()).discard(lv)

        # 记录冷却时间戳：同一层在 COOLDOWN_SECONDS 内不允许再次交易
        # 防止价格在网格线附近震荡时反复来回成交（whipsaw）
        import datetime as _dt
        g._last_deal_time[(stock, lv)] = _dt.datetime.now()

        print(f"[成交] {stock} {direction} {vol}股 @{price:.3f} 持仓={g.holdings.get(stock, 0)} "
              f"(层{lv}冷却{COOLDOWN_SECONDS}s)")

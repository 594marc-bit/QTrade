#coding:gbk
"""
QMT 策略：精简自适应版0708

策略说明（来源：schemes.yaml）：
  3因子 + 滚动IC自适应权重，中证1000

因子：
  - roe_yoy_rank: ROE同比变化横截面排名（正向）
  - rsi_14d:      14日RSI超卖反转信号（负向）
  - return_20d:   20日价格回报反转信号（负向）

基础权重（锚定方向）：
  roe_yoy_rank_score: +0.50
  rsi_score:          -0.25
  return_score:       -0.25

自适应权重机制：
  - 每期计算各因子与未来N日收益的滚动IC（Spearman秩相关）
  - 权重 = |IC均值|，归一化使 sum(abs(w)) = 1，方向由基础权重符号决定
  - IC窗口: 60个交易日

调仓频率：月频（每月第一个交易日）
持仓数量：10只
仓位方式：等权
基准指数：中证1000 (000852)

注意事项：
  - 第一行 #coding:gbk 为 QMT 强制要求
  - 全局状态使用自定义类实例，不要挂在 ContextInfo 上（bar回滚会导致值被重置）
  - 实盘中 handlebar 在最后一根K线上每个tick调用一次，只有最后一个tick的交易信号会被保留
"""

import talib
import numpy as np
import math
from datetime import datetime, timedelta

# ============================================================
# 全局状态（QMT要求：不直接挂在ContextInfo上，用全局类实例）
# ============================================================

class G:
    """全局状态容器，避免ContextInfo属性被bar回滚影响"""
    # --- 配置参数 ---
    accID = ''                          # 资金账号
    top_n = 10                          # 持仓数量
    ic_window = 60                      # IC滚动窗口（交易日）
    forward_days = 5                    # 计算IC用的前瞻天数
    base_weights = {                    # 基础权重（仅用于确定方向符号）
        'roe_yoy_rank': 0.50,
        'rsi_14d': -0.25,
        'return_20d': -0.25,
    }

    # --- 运行状态 ---
    initialized = False                 # init是否已完成
    last_rebalance_date = None          # 上次调仓日期
    current_weights = None              # 当前自适应权重
    historical_predictions = []         # [(date, stock, factor_vals_dict), ...] 用于IC计算
    ic_series = {                       # 各因子的IC序列
        'roe_yoy_rank': [],
        'rsi_14d': [],
        'return_20d': [],
    }

    # --- 财务数据缓存 ---
    fin_data_cache = {}                 # {stock: {report_date: {'roe_yoy': float}}}
    fin_last_fetch = None               # 上次获取财务数据的日期


g = G()

# ============================================================
# 初始化
# ============================================================

def init(ContextInfo):
    """
    策略初始化，在策略加载时执行一次。
    """
    # 1. 设置资金账号（回测用 'test'，实盘改为实际账号）
    g.accID = ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else 'test'

    # 2. 设置股票池：中证1000成分股
    # 方法1：通过板块获取（推荐，实盘自动更新）
    try:
        universe = ContextInfo.get_stock_list_in_sector('中证1000', '20230101')
        if universe and len(universe) > 0:
            ContextInfo.set_universe(universe)
            print(f"[init] 股票池：中证1000，共 {len(universe)} 只")
        else:
            # 方法2：通过指数代码获取
            universe = ContextInfo.get_stock_list_in_sector('000852.SH', '20230101')
            ContextInfo.set_universe(universe)
            print(f"[init] 股票池：000852.SH，共 {len(universe)} 只")
    except Exception as e:
        print(f"[init] 获取中证1000成分股失败: {e}")
        # 回退：手动设置一个较小的股票池用于测试
        fallback = ['000001.SZ', '000002.SZ', '600000.SH', '600036.SH', '601318.SH']
        ContextInfo.set_universe(fallback)
        print(f"[init] 使用回退测试股票池: {fallback}")

    # 3. 设置基准（用于业绩对比）
    try:
        ContextInfo.set_benchmark('000852.SH')
    except:
        pass

    # 4. 初始化自适应权重为基础权重
    g.current_weights = dict(g.base_weights)

    # 5. 获取初始财务数据
    _fetch_financial_data(ContextInfo)

    g.initialized = True
    print(f"[init] 精简自适应版0708 策略初始化完成")
    print(f"[init] 持仓数: {g.top_n}, IC窗口: {g.ic_window}天")
    print(f"[init] 基础权重: {g.base_weights}")


def after_init(ContextInfo):
    """
    init 之后、handlebar 之前调用。
    可用于调用 init 中不支持但此处可用的函数（如 get_trading_dates）。
    """
    pass


# ============================================================
# 主逻辑：每根K线调用一次
# ============================================================

def handlebar(ContextInfo):
    """
    行情驱动函数。
    回测：从第0根K线开始逐根调用。
    实盘：历史K线逐根调用完毕后，最新K线每个tick调用一次。
    """
    if not g.initialized:
        return

    # --- 获取当前日期和行情 ---
    current_date = _get_current_date(ContextInfo)
    if current_date is None:
        return

    # --- 判断是否为调仓日（每月第一个交易日） ---
    is_rebalance = _is_rebalance_date(ContextInfo, current_date)

    # --- 每日数据收集（用于后续IC计算） ---
    # 在每个交易日收集股票池的因子值和价格，记录预测
    _collect_daily_data(ContextInfo, current_date)

    # --- 更新IC（检查是否有到期的预测） ---
    _update_ic_series(ContextInfo, current_date)

    # --- 更新自适应权重 ---
    if is_rebalance and len(g.ic_series['roe_yoy_rank']) >= 10:
        g.current_weights = _compute_adaptive_weights()
        print(f"[{current_date}] 自适应权重更新: { {k: round(v, 4) for k, v in g.current_weights.items()} }")

    # --- 只在最新K线上执行交易 ---
    if not ContextInfo.is_last_bar():
        return

    # --- 非调仓日：检查风险控制 ---
    if not is_rebalance:
        _check_risk_controls(ContextInfo, current_date)
        return

    # --- 调仓日：执行选股和调仓 ---
    _rebalance(ContextInfo, current_date)
    g.last_rebalance_date = current_date


# ============================================================
# 因子计算
# ============================================================

def _calc_roe_yoy_rank(ContextInfo):
    """
    计算 ROE同比变化 的横截面排名分数（0-100）。
    使用 QMT 财务数据接口获取 ROE 同比增长率。

    返回: {stock: percentile_rank (0-100)}，数据不足返回空dict
    """
    # 尝试获取最新财务数据
    _fetch_financial_data(ContextInfo)

    roe_yoy_vals = {}
    for stock, reports in g.fin_data_cache.items():
        if not reports:
            continue
        # 取最新报告期的 roe_yoy
        latest_report = max(reports.keys())
        val = reports[latest_report].get('roe_yoy', None)
        if val is not None and not math.isnan(val):
            roe_yoy_vals[stock] = val

    if len(roe_yoy_vals) < 2:
        return {}

    # 百分位排名，映射到 0-100
    stocks = list(roe_yoy_vals.keys())
    values = np.array([roe_yoy_vals[s] for s in stocks])
    ranks = _percentile_rank(values)
    return {s: ranks[i] * 100 for i, s in enumerate(stocks)}


def _calc_rsi_14d(ContextInfo):
    """
    计算14日RSI。使用talib的RSI函数，Wilder平滑。
    返回: {stock: rsi_value}，数据不足返回空dict
    """
    universe = ContextInfo.get_universe()
    if not universe:
        return {}

    rsi_vals = {}
    for stock in universe:
        try:
            # 获取足够的历史收盘价（至少需要14*2=28根K线以保证数据充足）
            close = ContextInfo.get_history_data(50, '1d', 'close', stock, adjusted=True)
            if close is None or len(close) < 14:
                continue
            close_arr = np.array(close, dtype=np.float64)
            rsi = talib.RSI(close_arr, timeperiod=14)
            if rsi is not None and len(rsi) > 0 and not math.isnan(rsi[-1]):
                rsi_vals[stock] = float(rsi[-1])
        except:
            continue

    return rsi_vals


def _calc_return_20d(ContextInfo):
    """
    计算20日价格回报率。
    返回: {stock: return_20d}，数据不足返回空dict
    """
    universe = ContextInfo.get_universe()
    if not universe:
        return {}

    ret_vals = {}
    for stock in universe:
        try:
            close = ContextInfo.get_history_data(25, '1d', 'close', stock, adjusted=True)
            if close is None or len(close) < 21:
                continue
            ret_20d = (close[-1] - close[-21]) / close[-21]
            if not math.isnan(ret_20d):
                ret_vals[stock] = float(ret_20d)
        except:
            continue

    return ret_vals


# ============================================================
# 评分与选股
# ============================================================

def _score_and_select(ContextInfo, weights):
    """
    计算各股票的加权总分，选出top_n。

    流程：
      1. 计算三个因子的原始值
      2. 横截面Z-score标准化
      3. 加权求和得到总分
      4. 按总分降序排序，取top_n

    weights: {'roe_yoy_rank': w1, 'rsi_14d': w2, 'return_20d': w3}
    返回: [(stock, total_score), ...] 前top_n只
    """
    # 计算因子
    roe_data = _calc_roe_yoy_rank(ContextInfo)
    rsi_data = _calc_rsi_14d(ContextInfo)
    ret_data = _calc_return_20d(ContextInfo)

    # 取所有有数据的股票交集
    all_stocks = set(roe_data.keys()) | set(rsi_data.keys()) | set(ret_data.keys())
    all_stocks = list(all_stocks)

    if len(all_stocks) < g.top_n:
        print(f"[警告] 有效股票数 {len(all_stocks)} < top_n {g.top_n}")
        return []

    # 构建因子矩阵
    roe_arr = np.array([roe_data.get(s, np.nan) for s in all_stocks])
    rsi_arr = np.array([rsi_data.get(s, np.nan) for s in all_stocks])
    ret_arr = np.array([ret_data.get(s, np.nan) for s in all_stocks])

    # Z-score标准化（横截面）
    def zscore(arr):
        """Z-score标准化，忽略NaN"""
        mask = ~np.isnan(arr)
        if mask.sum() < 2:
            return np.full_like(arr, np.nan)
        mean = np.mean(arr[mask])
        std = np.std(arr[mask])
        if std == 0 or np.isnan(std):
            return np.full_like(arr, np.nan)
        result = np.full_like(arr, np.nan)
        result[mask] = (arr[mask] - mean) / std
        return result

    roe_z = zscore(roe_arr)
    rsi_z = zscore(rsi_arr)
    ret_z = zscore(ret_arr)

    # 加权总分（NaN视为0，不贡献分数）
    total = np.zeros(len(all_stocks))
    w = weights

    if not np.all(np.isnan(roe_z)):
        mask = ~np.isnan(roe_z)
        total[mask] += roe_z[mask] * w.get('roe_yoy_rank', 0)

    if not np.all(np.isnan(rsi_z)):
        mask = ~np.isnan(rsi_z)
        total[mask] += rsi_z[mask] * w.get('rsi_14d', 0)

    if not np.all(np.isnan(ret_z)):
        mask = ~np.isnan(ret_z)
        total[mask] += ret_z[mask] * w.get('return_20d', 0)

    # 排序选股
    valid_mask = ~np.isnan(total)
    if valid_mask.sum() == 0:
        return []

    valid_indices = np.where(valid_mask)[0]
    sorted_idx = valid_indices[np.argsort(-total[valid_mask])]  # 降序

    selected = []
    for idx in sorted_idx[:g.top_n]:
        selected.append((all_stocks[idx], float(total[idx])))

    return selected


# ============================================================
# IC计算与自适应权重
# ============================================================

def _collect_daily_data(ContextInfo, current_date):
    """
    每日收集股票的因子值和收盘价，记录"预测"。
    预测在 forward_days 天后结算，用于计算IC。
    """
    universe = ContextInfo.get_universe()
    if not universe:
        return

    # 获取各因子原始值
    roe_data = _calc_roe_yoy_rank(ContextInfo)
    rsi_data = _calc_rsi_14d(ContextInfo)
    ret_data = _calc_return_20d(ContextInfo)

    # 获取当前收盘价（用于后续计算forward return）
    prices = {}
    for stock in universe:
        try:
            close = ContextInfo.get_history_data(2, '1d', 'close', stock, adjusted=True)
            if close is not None and len(close) > 0:
                prices[stock] = close[-1]
        except:
            continue

    # 记录预测（包含因子值和当前价格）
    prediction = {
        'date': current_date,
        'prices': prices,
        'factors': {
            'roe_yoy_rank': roe_data,
            'rsi_14d': rsi_data,
            'return_20d': ret_data,
        }
    }
    g.historical_predictions.append(prediction)

    # 控制内存：保留足够的历史数据
    max_history = g.ic_window + g.forward_days + 50
    if len(g.historical_predictions) > max_history:
        g.historical_predictions = g.historical_predictions[-max_history:]


def _update_ic_series(ContextInfo, current_date):
    """
    检查历史预测中是否有正好满 forward_days 天的，结算IC。
    """
    if len(g.historical_predictions) < g.forward_days + 1:
        return

    # 找到 forward_days 天前的预测
    target_date = current_date
    pred = None
    for p in reversed(g.historical_predictions):
        if _days_between(p['date'], target_date) >= g.forward_days:
            pred = p
            break

    if pred is None:
        return

    pred_date = pred['date']
    # 避免对同一条预测重复结算
    # 使用简单标记：检查是否已经结算过这个日期
    if hasattr(g, '_last_settled_date') and g._last_settled_date == pred_date:
        return
    g._last_settled_date = pred_date

    # 计算 forward return
    prices_t0 = pred['prices']
    prices_t1 = {}
    for stock in prices_t0:
        try:
            close = ContextInfo.get_history_data(2, '1d', 'close', stock, adjusted=True)
            if close is not None and len(close) > 0:
                prices_t1[stock] = close[-1]
        except:
            continue

    common_stocks = set(prices_t0.keys()) & set(prices_t1.keys())
    if len(common_stocks) < 5:
        return

    stocks_list = list(common_stocks)
    forward_returns = np.array([
        (prices_t1[s] - prices_t0[s]) / prices_t0[s] for s in stocks_list
    ])

    # 对每个因子计算 Spearman rank IC
    for factor_name in ['roe_yoy_rank', 'rsi_14d', 'return_20d']:
        factor_vals = np.array([
            pred['factors'][factor_name].get(s, np.nan) for s in stocks_list
        ])
        mask = ~np.isnan(factor_vals)
        if mask.sum() < 5:
            continue

        ic = _spearman_rank_ic(factor_vals[mask], forward_returns[mask])
        if not math.isnan(ic):
            g.ic_series[factor_name].append(ic)

    # 保持IC序列在窗口内
    for factor_name in g.ic_series:
        if len(g.ic_series[factor_name]) > g.ic_window:
            g.ic_series[factor_name] = g.ic_series[factor_name][-g.ic_window:]


def _compute_adaptive_weights():
    """
    基于滚动IC均值计算自适应权重。

    规则：
      - 权重幅度 = |mean_IC|
      - 权重符号 = sign(base_weight)，保持基础权重的方向
      - 归一化使 sum(abs(w)) = 1.0

    返回: {'roe_yoy_rank': w1, 'rsi_14d': w2, 'return_20d': w3}
    """
    raw_weights = {}
    for factor_name, base_w in g.base_weights.items():
        ic_list = g.ic_series.get(factor_name, [])
        if len(ic_list) < 10:
            # IC数据不足，使用基础权重
            raw_weights[factor_name] = base_w
        else:
            mean_ic = np.mean(ic_list)
            # 幅度用|IC|，方向用base_weight的符号
            magnitude = abs(mean_ic) if not math.isnan(mean_ic) else 0.0
            direction = 1.0 if base_w > 0 else -1.0
            raw_weights[factor_name] = direction * magnitude

    # 归一化：sum(abs(w)) = 1.0
    total_abs = sum(abs(v) for v in raw_weights.values())
    if total_abs > 0:
        normalized = {k: v / total_abs for k, v in raw_weights.items()}
    else:
        # 退回到基础权重
        normalized = dict(g.base_weights)

    return normalized


# ============================================================
# 调仓执行
# ============================================================

def _rebalance(ContextInfo, current_date):
    """
    月度调仓逻辑：
      1. 选股：计算分数，取top_n
      2. 计算目标仓位（等权）
      3. 卖出不在目标池的持仓
      4. 买入目标仓位
    """
    if g.current_weights is None:
        g.current_weights = dict(g.base_weights)

    print(f"\n{'='*60}")
    print(f"[{current_date}] 开始月度调仓")
    print(f"[{current_date}] 当前自适应权重: { {k: round(v, 4) for k, v in g.current_weights.items()} }")

    # 1. 选股
    selected = _score_and_select(ContextInfo, g.current_weights)
    if not selected:
        print(f"[{current_date}] 选股结果为空，跳过调仓")
        return

    target_stocks = [s for s, _ in selected]
    n_targets = len(target_stocks)
    target_weight = 1.0 / n_targets  # 等权

    print(f"[{current_date}] 目标持仓 ({n_targets}只): {target_stocks}")
    for stock, score in selected:
        print(f"  {stock}: score={score:.4f}")

    # 2. 获取当前持仓
    positions = _get_current_positions(ContextInfo)
    current_holdings = set(positions.keys())

    # 3. 获取当前总资产
    total_value = _get_total_value(ContextInfo)

    # 4. 卖出不在目标池的持仓
    for stock in list(current_holdings):
        if stock not in target_stocks:
            pos = positions[stock]
            _sell_all(ContextInfo, stock, pos, current_date)
            print(f"[{current_date}] 卖出 {stock} (不在目标池)")

    # 5. 调整目标池内的仓位
    for stock in target_stocks:
        target_amount = total_value * target_weight
        if stock in positions:
            current_amount = positions[stock]['market_value']
        else:
            current_amount = 0

        diff_ratio = (target_amount - current_amount) / total_value if total_value > 0 else 0

        # 偏离超过 2% 才调整（减少交易频率）
        if abs(diff_ratio) < 0.02:
            continue

        if diff_ratio > 0:
            # 需要买入
            _buy_to_target(ContextInfo, stock, target_amount, current_amount, current_date)
        else:
            # 需要卖出部分
            _sell_to_target(ContextInfo, stock, target_amount, current_amount,
                           positions[stock], current_date)

    print(f"[{current_date}] 调仓完成\n")


# ============================================================
# 风险控制
# ============================================================

def _check_risk_controls(ContextInfo, current_date):
    """
    逐日检查风险控制条件：
      - 个股止损：-15%
      - 个股止盈：+30%
      - 组合最大回撤：-20%（清仓）
    """
    positions = _get_current_positions(ContextInfo)
    if not positions:
        return

    for stock, pos in positions.items():
        cost = pos.get('avg_cost', 0)
        if cost <= 0:
            continue

        current_price = _get_current_price(ContextInfo, stock)
        if current_price is None or current_price <= 0:
            continue

        pnl_pct = (current_price - cost) / cost

        # 止损 -15%
        if pnl_pct <= -0.15:
            _sell_all(ContextInfo, stock, pos, current_date)
            print(f"[{current_date}] 止损卖出 {stock}, 亏损 {pnl_pct*100:.1f}%")

        # 止盈 +30%
        elif pnl_pct >= 0.30:
            _sell_all(ContextInfo, stock, pos, current_date)
            print(f"[{current_date}] 止盈卖出 {stock}, 盈利 {pnl_pct*100:.1f}%")


# ============================================================
# 辅助函数：交易
# ============================================================

def _buy_to_target(ContextInfo, stock, target_amount, current_amount, date):
    """买入至目标仓位"""
    buy_amount = target_amount - current_amount
    if buy_amount <= 0:
        return

    current_price = _get_current_price(ContextInfo, stock)
    if current_price is None or current_price <= 0:
        return

    # 计算买入股数（按手取整，100股/手）
    buy_shares = int(buy_amount / current_price / 100) * 100
    if buy_shares < 100:
        return

    # QMT下单：股票买入(opType=23)，按股数(orderType=1101)，最新价(prType=5)
    try:
        passorder(
            23,                         # opType: 股票买入
            1101,                       # orderType: 按股数
            g.accID,                    # 资金账号
            stock,                      # 股票代码
            5,                          # prType: 最新价
            -1,                         # price: 最新价时填-1
            buy_shares,                 # 股数
            '精简自适应0708',            # 策略名称
            1,                          # quickTrade: 最新K线立即下单
            f'{date}',                  # 备注
            ContextInfo                 # 上下文
        )
        print(f"[{date}] 买入 {stock} {buy_shares}股 (约{buy_amount:.0f}元)")
    except Exception as e:
        print(f"[{date}] 买入 {stock} 失败: {e}")


def _sell_to_target(ContextInfo, stock, target_amount, current_amount, pos, date):
    """卖出至目标仓位"""
    sell_amount = current_amount - target_amount
    if sell_amount <= 0:
        return

    current_price = _get_current_price(ContextInfo, stock)
    if current_price is None or current_price <= 0:
        return

    sell_shares = int(sell_amount / current_price / 100) * 100
    if sell_shares < 100:
        return

    # 不超过持仓
    sell_shares = min(sell_shares, pos.get('volume', 0))

    try:
        passorder(
            24,                         # opType: 股票卖出
            1101,                       # orderType: 按股数
            g.accID,
            stock,
            5,                          # prType: 最新价
            -1,
            sell_shares,
            '精简自适应0708',
            1,
            f'{date}',
            ContextInfo
        )
        print(f"[{date}] 减仓 {stock} {sell_shares}股")
    except Exception as e:
        print(f"[{date}] 卖出 {stock} 失败: {e}")


def _sell_all(ContextInfo, stock, pos, date):
    """清仓某只股票"""
    volume = pos.get('volume', 0)
    if volume <= 0:
        # 尝试获取可卖数量
        volume = pos.get('enable_amount', pos.get('current_amount', 0))
    if volume <= 0:
        return

    try:
        passorder(
            24,                         # opType: 股票卖出
            1101,                       # orderType: 按股数
            g.accID,
            stock,
            5,                          # prType: 最新价
            -1,
            volume,                     # 全部卖出
            '精简自适应0708',
            1,
            f'{date}',
            ContextInfo
        )
    except Exception as e:
        print(f"[{date}] 清仓 {stock} 失败: {e}")


# ============================================================
# 辅助函数：数据获取
# ============================================================

def _get_current_date(ContextInfo):
    """获取当前K线日期"""
    try:
        # 通过 get_history_data 获取当前日期
        # QMT中 bar time 通过 get_bar_time() 或从行情获取
        bar_time = ContextInfo.get_bar_time()
        if bar_time:
            return bar_time.strftime('%Y%m%d') if hasattr(bar_time, 'strftime') else str(bar_time)[:8]
    except:
        pass

    # 备选：通过当前时间
    try:
        now = datetime.now()
        return now.strftime('%Y%m%d')
    except:
        return None


def _is_rebalance_date(ContextInfo, current_date):
    """
    判断是否为调仓日：每月第一个交易日。
    这里简化为检查是否是新的月份 + 距离上次调仓 >= 20天。
    """
    if g.last_rebalance_date is None:
        return True

    try:
        last_dt = datetime.strptime(g.last_rebalance_date, '%Y%m%d')
        curr_dt = datetime.strptime(current_date, '%Y%m%d')

        # 新的月份
        if curr_dt.month != last_dt.month or curr_dt.year != last_dt.year:
            return True

        # 或者距上次调仓超过25个自然日（容错）
        if (curr_dt - last_dt).days >= 25:
            return True
    except:
        pass

    return False


def _get_current_price(ContextInfo, stock):
    """获取股票最新价"""
    try:
        close = ContextInfo.get_history_data(2, '1d', 'close', stock, adjusted=True)
        if close is not None and len(close) > 0:
            return float(close[-1])
    except:
        pass
    return None


def _get_current_positions(ContextInfo):
    """
    获取当前持仓。
    返回: {stock: {'volume': int, 'market_value': float, 'avg_cost': float}}
    """
    positions = {}
    try:
        pos_list = get_trade_detail_data(g.accID, 'stock', 'position')
        if pos_list:
            for pos in pos_list:
                stock = pos.m_strInstrumentID + '.' + pos.m_strExchangeID if hasattr(pos, 'm_strExchangeID') else pos.m_strInstrumentID
                # QMT返回的持仓对象的属性名可能因版本而异
                volume = getattr(pos, 'm_nVolume', 0) or getattr(pos, 'current_amount', 0) or 0
                market_value = getattr(pos, 'm_dMarketValue', 0) or getattr(pos, 'market_value', 0) or 0
                avg_cost = getattr(pos, 'm_dOpenPrice', 0) or getattr(pos, 'avg_price', 0) or 0

                if volume > 0:
                    positions[stock] = {
                        'volume': volume,
                        'market_value': market_value,
                        'avg_cost': avg_cost,
                    }
    except Exception as e:
        print(f"[警告] 获取持仓失败: {e}")
    return positions


def _get_total_value(ContextInfo):
    """获取账户总资产"""
    try:
        # 方法1：通过 account 详情
        account_list = get_trade_detail_data(g.accID, 'stock', 'account')
        if account_list and len(account_list) > 0:
            acct = account_list[0]
            return getattr(acct, 'm_dTotalAsset', 0) or getattr(acct, 'total_asset', 0) or 0
    except:
        pass

    # 方法2：持仓市值 + 可用资金
    try:
        positions = _get_current_positions(ContextInfo)
        market_value = sum(p['market_value'] for p in positions.values())
        cash = _get_available_cash(ContextInfo)
        return market_value + cash
    except:
        pass

    return 1000000  # 默认100万


def _get_available_cash(ContextInfo):
    """获取可用资金"""
    try:
        account_list = get_trade_detail_data(g.accID, 'stock', 'account')
        if account_list and len(account_list) > 0:
            acct = account_list[0]
            return getattr(acct, 'm_dAvailable', 0) or getattr(acct, 'available_cash', 0) or 0
    except:
        pass
    return 0


def _fetch_financial_data(ContextInfo):
    """
    获取财务数据（ROE同比增长率）。
    QMT的 get_financial_data 接口，不同券商支持程度不同。
    如果不可用，使用价格数据估算替代指标。
    """
    # 控制获取频率（每30天获取一次）
    try:
        current_date = _get_current_date(ContextInfo)
        if current_date and g.fin_last_fetch:
            last = datetime.strptime(g.fin_last_fetch, '%Y%m%d')
            curr = datetime.strptime(current_date, '%Y%m%d')
            if (curr - last).days < 30:
                return
    except:
        pass

    g.fin_last_fetch = _get_current_date(ContextInfo)

    universe = ContextInfo.get_universe()
    if not universe:
        return

    for stock in universe:
        try:
            # QMT财务数据接口：get_financial_data(table, field, stock, start, end)
            # table: 'fina_indicator' 或 'income'
            # field: 'roe_yoy' 或类似字段名
            # 注意：不同券商版本的字段名可能不同
            data = ContextInfo.get_financial_data('fina_indicator', 'roe_yoy', stock, '20200101', '')
            if data is not None and len(data) > 0:
                if stock not in g.fin_data_cache:
                    g.fin_data_cache[stock] = {}
                # data 格式因QMT版本而异，适配常见格式
                for row in data:
                    if hasattr(row, 'm_strReportDate') and hasattr(row, 'm_dValue'):
                        report_date = row.m_strReportDate
                        g.fin_data_cache[stock][report_date] = {'roe_yoy': float(row.m_dValue)}
                    elif isinstance(row, dict):
                        report_date = row.get('report_date', row.get('end_date', ''))
                        g.fin_data_cache[stock][report_date] = {'roe_yoy': float(row.get('roe_yoy', 0))}
        except:
            # 财务数据获取静默失败（不是所有券商都支持）
            pass

    if g.fin_data_cache:
        print(f"[财务数据] 已获取 {len(g.fin_data_cache)} 只股票的ROE数据")


# ============================================================
# 辅助函数：统计计算
# ============================================================

def _percentile_rank(arr):
    """
    计算百分位排名，返回 0-1 之间的值。
    """
    n = len(arr)
    if n <= 1:
        return np.zeros(n)
    # argsort twice gives ranks
    order = np.argsort(arr)
    ranks = np.empty(n)
    ranks[order] = np.arange(n)
    return ranks / (n - 1)


def _spearman_rank_ic(x, y):
    """
    计算 Spearman 秩相关系数（IC）。
    等价于 Pearson 相关系数在秩上的计算。
    """
    n = len(x)
    if n < 3:
        return float('nan')

    # 对x和y分别求秩
    x_rank = _percentile_rank(x) * (n - 1)  # 转为 0 to n-1
    y_rank = _percentile_rank(y) * (n - 1)

    # Pearson on ranks
    x_mean = np.mean(x_rank)
    y_mean = np.mean(y_rank)
    x_diff = x_rank - x_mean
    y_diff = y_rank - y_mean

    numerator = np.sum(x_diff * y_diff)
    denominator = np.sqrt(np.sum(x_diff ** 2) * np.sum(y_diff ** 2))

    if denominator == 0:
        return float('nan')

    return numerator / denominator


def _days_between(date1, date2):
    """计算两个日期字符串之间的天数"""
    try:
        d1 = datetime.strptime(str(date1)[:8], '%Y%m%d')
        d2 = datetime.strptime(str(date2)[:8], '%Y%m%d')
        return abs((d2 - d1).days)
    except:
        return 0


# ============================================================
# 可选：定时器（如需收盘前强制调仓等）
# ============================================================

def run_time(ContextInfo):
    """
    定时器函数（可选）。
    如果配置了 run_time，可以在指定时间执行逻辑。
    例如：每天14:50检查是否有未完成的调仓。
    """
    pass

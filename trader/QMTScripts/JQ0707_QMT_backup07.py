# coding:gbk
"""
策略名称：ETF 核心资产轮动（QMT 版）
逻辑：每天计算 ETF 动量得分（年化收益率*R2），持有得分最高的 1 只 ETF，每日调仓。

回测：K线周期设为"日线"，副图模式运行
实盘：通过 run_time 定时触发，在 adjust_time 附近执行每日调仓
"""

import numpy as np
import pandas as pd
import math
import datetime

# ============================================================
# 调试开关
# ============================================================

DEBUG_MODE = False
# True → 忽略 交易时段 + 11:00窗口 + 每日防重，方便任何时间测试
# 实盘/模拟上线前务必改回 False ！！！

DRY_RUN = False
# True → 只计算得分 & 打印调仓计划，不实际下单
# 适合首次部署时验证数据、行情、计算链路是否正常

# ============================================================
# 全局状态（立即下单模式必须用普通全局变量，不能用 ContextInfo 存）
# ============================================================

class G:
    pass

g = G()

# ============================================================
# 初始化
# ============================================================

def init(ContextInfo):
    # ---------- 参数配置 ----------
    g.stock_sum = 1                         # 持有 ETF 数量
    g.m_days = 25                           # 动量计算天数
    g.adjust_time = "11:00"                 # 每日调仓时间（实盘用）
    g.manual_capital = 50000                # 单只 ETF 最大投入金额（0=不限制）

    # ---------- ETF 池（QMT 格式 .SH / .SZ） ----------
    g.etf_pool = [
        # 进攻性（境外）
        "513100.SH",   # 纳指ETF
        "159509.SZ",   # 纳指科技ETF
        "513520.SH",   # 日经ETF
        "513030.SH",   # 德国ETF
        # 进攻性（国内）
        "513130.SH",   # 恒生科技
        "510180.SH",   # 上证180
        "159915.SZ",   # 创业板ETF
        # 行业ETF
        "510410.SH",   # 资源
        "515650.SH",   # 消费50
        "512290.SH",   # 生物医药
        "588120.SH",   # 科创100
        "515070.SH",   # 人工智能ETF
        "159851.SZ",   # 金融科技
        "159755.SZ",   # 电池ETF
        "516160.SH",   # 新能源
        "513050.SH",   # 中概互联网ETF
        "512710.SH",   # 军工ETF
        "159692.SZ",   # 证券
        "512480.SH",   # 半导体
        # 防守性（商品）
        "518880.SH",   # 黄金ETF
        "159980.SZ",   # 有色ETF
        "159985.SZ",   # 豆粕ETF
        "501018.SH",   # 南方原油
        # 防守性（债券）
        "511090.SH",   # 30年国债ETF
    ]

    # ---------- 账户配置 ----------
    # 优先用 ContextInfo.accID，不可用时尝试 QMT 内置全局 account
    acc_id = getattr(ContextInfo, 'accID', '') or ''
    if not acc_id:
        try:
            acc_id = str(account)  # QMT 内置全局变量
        except:
            pass
    g.accountID = acc_id if acc_id else 'test'
    print(f"【账户配置】accountID = {g.accountID}（ContextInfo.accID={getattr(ContextInfo, 'accID', 'N/A')!r}）")

    # ---------- 今日是否已调仓（实盘防重） ----------
    g.last_rebalance_date = ""

    # ---------- 判断运行模式 ----------
    if ContextInfo.capital > 0:
        # 回测模式：capital 为设置的初始资金
        g.run_mode = "backtest"
        g.money = ContextInfo.capital
        g.capital_init = ContextInfo.capital
        print(f"【初始化】回测模式，初始资金：{g.capital_init:.2f}")
    else:
        # 模拟 / 实盘模式：capital 均为 -1，需从账户查询
        g.run_mode = "live"
        g.money = 0
        g.capital_init = 0

        print(f"【初始化】模拟/实盘模式（capital={ContextInfo.capital}），查询账户资金...")

        # ---- 获取可用资金 ----
        got_money = False

        # 方式1：按 accountID 查账户
        try:
            account_info = get_trade_detail_data(g.accountID, 'STOCK', 'account')
            if account_info is not None and len(account_info) > 0:
                acc = account_info[0]
                g.money = acc.m_dAvailable if hasattr(acc, 'm_dAvailable') else 0
                g.capital_init = g.money
                got_money = True
                print(f"【账户查询-方式1 OK】可用资金：{g.money:.2f}")
        except Exception as e:
            print(f"【账户查询-方式1 FAIL】{type(e).__name__}: {e}")

        # 方式2：不指定 accountID 查
        if not got_money:
            try:
                account_info = get_trade_detail_data('', 'STOCK', 'account')
                if account_info is not None and len(account_info) > 0:
                    acc = account_info[0]
                    g.money = acc.m_dAvailable if hasattr(acc, 'm_dAvailable') else 0
                    g.capital_init = g.money
                    got_money = True
                    print(f"【账户查询-方式2 OK】可用资金：{g.money:.2f}")
            except Exception as e:
                print(f"【账户查询-方式2 FAIL】{type(e).__name__}: {e}")

        if not got_money:
            # 模拟模式 / 账户未登录时，用 manual_capital 兜底
            if g.manual_capital > 0:
                g.money = g.manual_capital
                g.capital_init = g.manual_capital
                print(f"【账户资金-兜底】使用 manual_capital：{g.money:.2f}")
                print("  [!] 模拟模式正常；实盘请确认账户已登录")
            else:
                print("=" * 60)
                print("【严重警告】无法获取账户资金，且 manual_capital 为 0！")
                print("  请检查 QMT 账户登录状态，或设置 manual_capital")
                print("=" * 60)

    # ---------- 注册定时任务（仅实盘） ----------
    if g.run_mode == "live":
        # 动态计算下一个 11:00（确保 startTime 在未来，避免立即触发死循环）
        now = datetime.datetime.now()
        target = now.replace(hour=11, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        next_run = target.strftime('%Y-%m-%d %H:%M:%S')
        ContextInfo.run_time("daily_rebalance", "86400nSecond", next_run)
        print(f"【初始化】run_time 已注册，下次触发：{next_run}")

    print(f"【初始化】ETF 数量：{len(g.etf_pool)}，账户：{g.accountID}，模式：{g.run_mode}，单只上限：{g.manual_capital}")


# ============================================================
# handlebar（回测模式入口）
# ============================================================

def handlebar(ContextInfo):
    # 实盘模式：跳过，由 run_time → daily_rebalance 处理
    if g.run_mode == "live":
        return

    # 回测模式：每个日线 bar 执行一次调仓
    d = ContextInfo.barpos
    try:
        now_date = timetag_to_datetime(ContextInfo.get_bar_timetag(d), '%Y%m%d')
        date_str = str(now_date)
    except:
        date_str = f"Bar{d}"

    print(f"\n========== {date_str} 调仓检查 ==========")
    daily_rebalance(ContextInfo)


# ============================================================
# 每日调仓（实盘由 run_time 回调，回测由 handlebar 调用）
# ============================================================

def daily_rebalance(ContextInfo):
    """每日调仓主流程"""

    # ---- 立即重新注册 run_time（计算下一个 11:00，确保不丢今天） ----
    if g.run_mode == "live":
        now = datetime.datetime.now()
        target = now.replace(hour=11, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        next_run = target.strftime('%Y-%m-%d %H:%M:%S')
        ContextInfo.run_time("daily_rebalance", "86400nSecond", next_run)

    # ---- 实盘：检查交易时间 & 时间窗口 & 防重 ----
    if g.run_mode == "live":
        if not _is_trading_time():
            return

        if not DEBUG_MODE:
            now = datetime.datetime.now()
            now_time = now.strftime('%H%M%S')

            # 仅在调仓时间窗口内执行（11:00-11:30）
            if not ("110000" <= now_time <= "113000"):
                return

            today = now.strftime('%Y%m%d')
            if g.last_rebalance_date == today:
                return  # 今日已调仓

    # ---- 调仓前：持仓健康检查 ----
    if g.run_mode == "live":
        positions = _get_current_positions()
        pool_holdings = {etf: vol for etf, vol in positions.items() if etf in g.etf_pool}
        if len(pool_holdings) > g.stock_sum:
            print("=" * 60)
            print(f"【调仓前告警】当前持仓 {len(pool_holdings)} 只 > stock_sum({g.stock_sum}) 只")
            print(f"  持仓: {list(pool_holdings.keys())}")
            print(f"  可能原因：上一日卖出未成交导致仓位叠加")
            print(f"  本轮调仓将优先清理多余的持仓")
            print("=" * 60)

    # ---- 1. 计算 ETF 得分 ----
    scores_df = calculate_etf_scores(ContextInfo)

    # ---- 2. 选择目标 ETF ----
    if scores_df.empty:
        print("【调仓决策】无满足条件的 ETF，执行清仓")
        targets = {}
    else:
        target_list = scores_df.index.tolist()[:g.stock_sum]
        weight = round(1.0 / len(target_list), 3) if target_list else 0
        targets = {etf: weight for etf in target_list}
        print(f"【调仓决策】目标ETF：{targets}")

    # ---- 3. 执行调仓 ----
    execute_rebalance(ContextInfo, targets, scores_df)

    # ---- 标记已调仓（实盘防重） ----
    if g.run_mode == "live":
        g.last_rebalance_date = datetime.datetime.now().strftime('%Y%m%d')


# ============================================================
# 交易时段判断
# ============================================================

def _is_trading_time():
    """A 股交易时段：周一至周五 9:30-15:00"""
    if DEBUG_MODE:
        return True
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    tm = now.strftime('%H%M%S')
    return "093000" <= tm <= "150000"


# ============================================================
# 计算 ETF 动量得分
# ============================================================

def calculate_etf_scores(ContextInfo):
    """
    计算所有 ETF 的动量得分（年化收益率 * R2）
    返回 DataFrame（index=代码，列：annualized_returns, r2, score）
    过滤：score<=0 或 >=6，或近3天有单日跌幅超5%
    """
    # ---- 获取历史收盘价 ----
    if g.run_mode == "backtest":
        # 回测：用当前 bar 时间作为 end_time，取本地数据
        try:
            bar_date = timetag_to_datetime(
                ContextInfo.get_bar_timetag(ContextInfo.barpos), '%Y%m%d%H%M%S')
        except:
            bar_date = ''
        close_data = ContextInfo.get_market_data_ex(
            ['close'], g.etf_pool, end_time=bar_date,
            period='1d', count=g.m_days + 1, subscribe=False
        )
    else:
        # 实盘：取最新行情
        close_data = ContextInfo.get_market_data_ex(
            ['close'], g.etf_pool, period='1d',
            count=g.m_days + 1, subscribe=True
        )

    # ---- 获取当日最新价格 ----
    current_price = {}
    if g.run_mode == "live":
        # 实盘：用全推行情获当前价
        try:
            tick = ContextInfo.get_full_tick(g.etf_pool)
            for etf in g.etf_pool:
                if etf in tick and 'lastPrice' in tick[etf]:
                    current_price[etf] = tick[etf]['lastPrice']
                else:
                    current_price[etf] = None
        except:
            for etf in g.etf_pool:
                current_price[etf] = None
    else:
        # 回测：取 close_data 中每个 ETF 的最后一个收盘价
        for etf in g.etf_pool:
            if etf in close_data and len(close_data[etf]) > 0:
                current_price[etf] = close_data[etf].iloc[-1, 0]
            else:
                current_price[etf] = None

    # ---- 初始化结果 DataFrame ----
    columns = ['annualized_returns', 'r2', 'score']
    scores_df = pd.DataFrame(index=g.etf_pool, columns=columns)

    for etf in g.etf_pool:
        # 数据不足 → 得分为 0
        if etf not in close_data or len(close_data[etf]) < g.m_days + 1:
            scores_df.loc[etf, columns] = [0, 0, 0]
            continue

        # 历史收盘价（排除最后一根，因为可能含当日）
        hist = close_data[etf].iloc[:, 0].values.astype(float)
        if len(hist) < 2:
            scores_df.loc[etf, columns] = [0, 0, 0]
            continue

        # 合并当日价格
        # 回测：hist 已含当日 bar，无需追加；实盘：hist 为历史收盘，追加实时价
        if g.run_mode == "live" and current_price.get(etf) is not None:
            prices = np.append(hist, current_price[etf])
        else:
            prices = hist

        # ---- 对数收益率线性拟合 ----
        y = np.log(prices)
        x = np.arange(len(y))
        weights = np.linspace(1, 2, len(y))  # 近端加权

        if len(y) < 2:
            scores_df.loc[etf, columns] = [0, 0, 0]
            continue

        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_returns = math.exp(slope * 250) - 1

        # R2
        ss_res = np.sum(weights * (y - (slope * x + intercept)) ** 2)
        ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0

        score = annualized_returns * r2

        # ---- 过滤：近3天有单日跌幅超 5% ----
        if len(prices) >= 4:
            ratios = [
                prices[-1] / prices[-2],
                prices[-2] / prices[-3],
                prices[-3] / prices[-4],
            ]
            if min(ratios) < 0.95:
                score = 0

        scores_df.loc[etf, 'annualized_returns'] = annualized_returns
        scores_df.loc[etf, 'r2'] = r2
        scores_df.loc[etf, 'score'] = score

    # ---- 打印排名 ----
    print("【ETF 得分列表（按 score 降序）】")
    sorted_df = scores_df.sort_values(by='score', ascending=False)
    for etf in sorted_df.index:
        s = sorted_df.loc[etf, 'score']
        try:
            name = ContextInfo.get_stock_name(etf)
        except:
            name = etf
        print(f"  {etf} | {name} | {s:.4f}")

    # 过滤：0 < score < 6
    filtered = scores_df[(scores_df['score'] > 0) & (scores_df['score'] < 6)]
    return filtered.sort_values(by='score', ascending=False)


# ============================================================
# 执行调仓（先卖后买）
# ============================================================

def execute_rebalance(ContextInfo, targets, scores_df):
    """
    差分调仓：只卖出不在目标中的持仓，只买入不在当前持仓中的目标。
    先卖后买，卖出确认成交后再买入，避免叠加仓位。
    targets: dict {etf: weight}
    """

    # ---- 获取当前价格 ----
    price_dict = _get_current_prices(ContextInfo)

    # ---- 获取当前持仓 ----
    positions = _get_current_positions()

    # ---- 当前在 etf_pool 中的持仓（非本策略买进的股票不碰） ----
    pool_holdings = {etf: vol for etf, vol in positions.items() if etf in g.etf_pool}
    current_etfs = set(pool_holdings.keys())
    target_etfs = set(targets.keys())

    # ---- 判断是否需要调仓 ----
    if current_etfs == target_etfs:
        print(f"【调仓决策】目标与持仓一致（{current_etfs or '空仓'}），无需调仓")
        return

    # ---- 差分计算 ----
    to_sell = current_etfs - target_etfs           # 需要清掉的
    to_keep = current_etfs & target_etfs           # 已在目标中，保留不动
    to_buy = target_etfs - current_etfs            # 需要新买入的

    if to_keep:
        print(f"【调仓】保留已有仓位：{to_keep}（不卖出，避免无谓手续费和滑点）")

    print(f"【调仓决策】卖: {to_sell or '无'} → 买: {to_buy or '无'}，开始调仓")

    # ---- 1. 卖出：只卖不在目标中的，确认成交后再继续 ----
    sell_failed = []  # [(etf, reason), ...]

    for etf in to_sell:
        vol = pool_holdings.get(etf, 0)
        if vol <= 0:
            continue

        prev_vol = vol
        sent = sell_position(ContextInfo, etf, vol, "调出", price_dict)
        if not sent:
            sell_failed.append((etf, "委托发送失败"))
            continue

        # 等待成交确认
        success, filled = _wait_for_sell_fill(etf, vol, prev_vol)
        if success:
            print(f"【卖出确认】{etf} 已成交 {filled} 股")
        else:
            print(f"【卖出超时】{etf} 委托 {vol} 股，实际成交 {filled} 股")
            sell_failed.append((etf, f"成交超时（已成交{filled}/{vol}）"))

    # ---- 1.5 异常检测：持仓数量是否超限 ----
    positions_after = _get_current_positions()
    pool_after = {etf: vol for etf, vol in positions_after.items() if etf in g.etf_pool}
    if len(pool_after) > g.stock_sum + len(sell_failed):
        print("=" * 60)
        print(f"【异常告警】当前持仓 {len(pool_after)} 只 > stock_sum({g.stock_sum}) + 卖出失败({len(sell_failed)}) 只")
        print(f"  当前持仓: {list(pool_after.keys())}")
        print(f"  目标持仓: {list(target_etfs)}")
        print(f"  卖出失败: {[e for e, _ in sell_failed]}")
        print("  请手动检查账户！")
        print("=" * 60)

    # ---- 1.6 卖出有失败时，跳过买入防止叠加仓位 ----
    if sell_failed:
        print(f"【调仓中止】以下 ETF 卖出未完成，跳过买入以保护仓位：")
        for etf, reason in sell_failed:
            print(f"  - {etf}: {reason}")
        return

    # ---- 2. 买入：只买不在当前持仓中的目标 ETF ----
    if not to_buy:
        print("【调仓完成】目标 ETF 已全部在持仓中")
        return

    available = _get_available_cash()
    print(f"【资金】卖出确认完成，可用资金：{available:.2f}")

    for etf, weight in targets.items():
        if etf not in to_buy:
            continue

        if etf not in price_dict or price_dict[etf] is None:
            print(f"【买入跳过】{etf} 无价格数据")
            continue

        price = price_dict[etf]

        # 买入金额 = min(可用资金, manual_capital × weight)
        # manual_capital=0 时按全仓买入
        cap = g.manual_capital if g.manual_capital > 0 else available
        buy_value = min(available, cap * weight)

        min_trade = max(500, 100 * price)
        if buy_value < min_trade:
            print(f"【买入跳过】{etf} 可用资金不足（可用{available:.2f}，需{min_trade:.0f}）")
            continue

        buy_position(ContextInfo, etf, buy_value, price_dict)


# ============================================================
# 辅助：获取当前价格
# ============================================================

def _get_current_prices(ContextInfo):
    """获取所有 ETF 的当前价格"""
    price_dict = {}

    if g.run_mode == "live":
        try:
            tick = ContextInfo.get_full_tick(g.etf_pool)
            for etf in g.etf_pool:
                if etf in tick and 'lastPrice' in tick[etf]:
                    price_dict[etf] = tick[etf]['lastPrice']
                else:
                    price_dict[etf] = None
        except:
            pass

    # 回测 / 全推失败：用历史收盘价兜底
    for etf in g.etf_pool:
        if etf not in price_dict or price_dict[etf] is None:
            try:
                data = ContextInfo.get_market_data_ex(
                    ['close'], [etf], period='1d', count=1,
                    subscribe=False
                )
                if etf in data and len(data[etf]) > 0:
                    price_dict[etf] = data[etf].iloc[-1, 0]
                else:
                    price_dict[etf] = None
            except:
                price_dict[etf] = None

    return price_dict


# ============================================================
# 辅助：获取当前持仓
# ============================================================

def _get_current_positions():
    """从 QMT 查询当前持仓，返回 {etf_code: 可用股数}"""
    positions = {}
    try:
        pos_list = get_trade_detail_data(g.accountID, 'STOCK', 'position')
        if pos_list is not None:
            for p in pos_list:
                code = (p.m_strInstrumentID + '.' + p.m_strExchangeID
                        if hasattr(p, 'm_strExchangeID') else p.m_strInstrumentID)
                vol = p.m_nCanUseVolume if hasattr(p, 'm_nCanUseVolume') else 0
                if vol > 0:
                    positions[code] = vol
    except Exception as e:
        print(f"【持仓查询失败】{e}")

    return positions


# ============================================================
# 辅助：获取可用资金
# ============================================================

def _get_available_cash():
    """查询当前可用资金，失败时回退到 g.money"""
    try:
        account_info = get_trade_detail_data(g.accountID, 'STOCK', 'account')
        if account_info and len(account_info) > 0:
            return float(account_info[0].m_dAvailable)
    except:
        pass
    try:
        account_info = get_trade_detail_data('', 'STOCK', 'account')
        if account_info and len(account_info) > 0:
            return float(account_info[0].m_dAvailable)
    except:
        pass
    return g.money


# ============================================================
# 成交确认（实盘轮询，回测直接返回成功）
# ============================================================

def _wait_for_sell_fill(etf_code, expected_vol, prev_vol, timeout_sec=15):
    """
    等待卖出委托成交。回测模式直接返回成功。
    prev_vol: 卖出前的持仓量
    返回 (success: bool, filled_vol: int)
    """
    if g.run_mode == "backtest":
        return True, expected_vol

    import time
    interval = 3
    elapsed = 0
    while elapsed < timeout_sec:
        time.sleep(interval)
        elapsed += interval

        current_pos = _get_current_positions()
        remaining = current_pos.get(etf_code, 0)
        filled = prev_vol - remaining

        if filled >= expected_vol:
            return True, filled

    # 超时：返回实际已成交量
    current_pos = _get_current_positions()
    remaining = current_pos.get(etf_code, 0)
    filled = prev_vol - remaining
    return False, filled


# ============================================================
# 卖出
# ============================================================

def sell_position(ContextInfo, etf_code, volume, reason, price_dict=None):
    """卖出指定数量。返回 True（委托已发送） / False（未发送）"""
    if volume <= 0:
        print(f"【卖出跳过】{etf_code} 持仓量为 0，无需卖出")
        return False

    if price_dict is None:
        price_dict = _get_current_prices(ContextInfo)
    price = price_dict.get(etf_code)
    if price is None:
        print(f"【卖出失败】{etf_code} 无价格数据")
        return False

    # ---- 涨跌停检查 ----
    if g.run_mode == "live":
        try:
            tick = ContextInfo.get_full_tick([etf_code])
            if etf_code in tick:
                high = tick[etf_code].get('highLimit')
                low = tick[etf_code].get('lowLimit')
                if high is not None and low is not None:
                    if price >= high or price <= low:
                        print(f"【卖出跳过】{etf_code} 触及涨跌停，不交易")
                        return False
        except:
            pass

    # 确保是 100 的整数倍
    vol = int(volume // 100) * 100
    if vol < 100:
        print(f"【卖出跳过】{etf_code} 可卖数量 {vol} < 100 股，无法下单")
        return False

    msg = f"调出_{reason}_{etf_code}"

    print(f"【卖出执行】{etf_code}，数量 {vol} 股，价格 {price:.3f}，原因：{reason}")
    if DRY_RUN:
        print(f"  [DRY_RUN] 跳过实际下单")
        return True

    try:
        reqid = passorder(
            24,                     # 卖出
            1101,                   # 按股数
            g.accountID,
            etf_code,
            14,                     # 卖一价（对手价），确保快速成交
            -1,                     # price=-1 自动取对手价
            vol,
            "ETF轮动",
            2,                      # 立即下单
            msg,
            ContextInfo,
        )
        print(f"  passorder 返回 reqid = {reqid}")
        # 注意：passorder 返回 0 不一定代表失败（某些 QMT 版本/券商在立即下单模式下
        # 即使委托已成功发出也可能返回 0）。真正的成败由后续 _wait_for_sell_fill
        # 轮询持仓变化来确认，这里不再仅凭 reqid 判失败。
        if reqid is None or reqid < 0:
            print(f"  【卖出失败】{etf_code} passorder 返回 {reqid}，委托未生成（请检查 accountID={g.accountID!r}）")
            return False
        if reqid == 0:
            print(f"  [注意] reqid=0，但仍尝试等待成交确认（某些版本 reqid=0 不代表失败）")
        else:
            print(f"  卖出委托已发送：{etf_code} {vol}股，reqid={reqid}")
        return True
    except Exception as e:
        print(f"  【卖出失败】{etf_code} passorder 异常：{e}")
        return False


# ============================================================
# 买入
# ============================================================

def buy_position(ContextInfo, etf_code, buy_value, price_dict):
    """买入指定金额"""
    if buy_value <= 0:
        return

    price = price_dict.get(etf_code)
    if price is None or price <= 0:
        print(f"【买入失败】{etf_code} 无有效价格")
        return

    # ---- 涨跌停检查 ----
    if g.run_mode == "live":
        try:
            tick = ContextInfo.get_full_tick([etf_code])
            if etf_code in tick:
                high = tick[etf_code].get('highLimit')
                low = tick[etf_code].get('lowLimit')
                if high is not None and low is not None:
                    if price >= high or price <= low:
                        print(f"【买入跳过】{etf_code} 触及涨跌停，不交易")
                        return
        except:
            pass

    # 计算买入股数（100 的整数倍）
    vol = int(buy_value / price / 100) * 100
    if vol < 100:
        print(f"【买入跳过】{etf_code} 目标市值 {buy_value:.2f}，计算数量 {vol} < 100")
        return

    msg = f"调入_{etf_code}"

    print(f"【买入执行】{etf_code}，数量 {vol} 股，价格 {price:.3f}，目标市值 {buy_value:.2f}")
    if DRY_RUN:
        print(f"  [DRY_RUN] 跳过实际下单")
        return
    reqid = passorder(
        23,                     # 买入
        1101,                   # 按股数
        g.accountID,
        etf_code,
        14,                     # 卖一价（对手价），确保快速成交
        -1,                     # price=-1 自动取对手价
        vol,
        "ETF轮动",
        2,                      # 立即下单
        msg,
        ContextInfo,
    )
    print(f"  passorder 返回 reqid = {reqid}")
    if reqid is None or reqid < 0:
        print(f"  【买入失败】{etf_code} passorder 返回 {reqid}，委托未生成（请检查 accountID={g.accountID!r}）")
        return
    if reqid == 0:
        print(f"  [注意] reqid=0（某些版本不代表失败），委托可能已发出，请观察成交")
    else:
        print(f"  买入委托已发送：{etf_code} {vol}股，reqid={reqid}")

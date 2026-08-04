"""
分钟级因子（基于 5min K线数据）

这些因子通过 Windows QMT API 获取日内 5 分钟 K 线数据，
将微观结构信息聚合为日频因子值，可接入现有日频选股/回测流程。

因子列表：
  - open_effect_5m         开盘效应：前30分钟涨幅 vs 全天涨幅
  - vwap_deviation_5m      VWAP偏离：收盘价相对日内VWAP的偏离度
  - tail_volume_5m         尾盘放量：最后30分钟成交量占比
  - intraday_reversal_5m   日内反转：上午 vs 下午方向一致性
  - volume_concentration_5m 成交量集中度：最大单bar / 平均单bar
  - am_volume_ratio_5m     上午放量：上午成交量占全天比例

日期: 2026-07-23
来源: QMT 5min K线 (qmt_api_server → kline_5m table)
"""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor

# ---------------------------------------------------------------------------
# 分钟数据缓存（模块级，避免重复拉取）
# ---------------------------------------------------------------------------
_MINUTE_CACHE: dict[str, pd.DataFrame] = {}  # key = "ts_code:YYYYMMDD"
_DAILY_FETCHED: set[str] = set()  # 已按日批量预取的日期
_MINUTE_EARLIEST: str | None = None  # 分钟数据最早可用日期（惰性检测）


def _detect_earliest() -> str:
    """Detect the earliest available minute-data date from local+Windows."""
    global _MINUTE_EARLIEST
    if _MINUTE_EARLIEST is not None:
        return _MINUTE_EARLIEST
    # Check local SQLite first
    try:
        from src.data.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT MIN(SUBSTR(bar_time,1,8)) FROM minute_5m"
            ).fetchone()
            if row and row[0]:
                _MINUTE_EARLIEST = row[0]
                return _MINUTE_EARLIEST
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback: Windows API
    try:
        from src.data.qmt_fetcher import get_minute_stats
        stats = get_minute_stats()
        _MINUTE_EARLIEST = stats.get("earliest_date", "20250101")
    except Exception:
        _MINUTE_EARLIEST = "20250101"
    return _MINUTE_EARLIEST


def _ensure_day(trade_date: str) -> None:
    """确保某个交易日的全市场分钟数据已在缓存中。

    优先读本地 SQLite（快速），未命中时从 Windows HTTP 拉取并自动持久化。
    超过数据可用范围的日期直接跳过，不发起无意义的 HTTP 请求。
    """
    if trade_date in _DAILY_FETCHED:
        return

    # Skip dates before earliest available minute data (no point in HTTP)
    earliest = _detect_earliest()
    if trade_date < earliest:
        _DAILY_FETCHED.add(trade_date)
        return

    # 1. Try local SQLite first
    try:
        from src.data.storage import load_minute_daily
        df = load_minute_daily(trade_date)
        if not df.empty:
            df = df[df["is_trading"] == 1].copy()
            df["time_str"] = df["bar_time"].str[8:14]
            for ts_code, group in df.groupby("ts_code"):
                _MINUTE_CACHE[f"{ts_code}:{trade_date}"] = group.copy()
            _DAILY_FETCHED.add(trade_date)
            return
    except Exception:
        pass

    # 2. Fallback: HTTP fetch from Windows (auto-saves to local SQLite)
    from src.data import qmt_fetcher

    df = qmt_fetcher.fetch_minute_daily(trade_date)
    if df.empty:
        _DAILY_FETCHED.add(trade_date)
        return
    df = df[df["is_trading"] == 1].copy()
    df["time_str"] = df["bar_time"].str[8:14]
    for ts_code, group in df.groupby("ts_code"):
        _MINUTE_CACHE[f"{ts_code}:{trade_date}"] = group.copy()
    _DAILY_FETCHED.add(trade_date)


def _clear_cache() -> None:
    """清空分钟数据缓存（内存释放）。"""
    global _MINUTE_EARLIEST
    _MINUTE_CACHE.clear()
    _DAILY_FETCHED.clear()
    _MINUTE_EARLIEST = None


def _get_minute_bars(ts_code: str, trade_date: str) -> pd.DataFrame:
    """获取某只股票某日的 5min K线，带缓存。

    首次访问某日期时自动按日批量预取该日全市场数据。
    """
    _ensure_day(trade_date)  # 整日批量预取
    cache_key = f"{ts_code}:{trade_date}"
    return _MINUTE_CACHE.get(cache_key, pd.DataFrame())


def _compute_daily_factor(
    ts_code: str,
    trade_date: str,
    factor_name: str,
    compute_fn,
) -> float | None:
    """通用模板：拉分钟数据 → 算因子 → 返回标量。

    Returns None if data unavailable.
    """
    df = _get_minute_bars(ts_code, trade_date)
    if df.empty or len(df) < 10:
        return None
    try:
        return compute_fn(df)
    except Exception:
        return None


def _clear_cache():
    """清空缓存（在每批处理完后调用）。"""
    _MINUTE_CACHE.clear()


# ============================================================================
# 因子 1: 开盘效应 Open Effect
# ============================================================================


@register_factor
class OpenEffectFactor(FactorBase):
    """开盘效应：前30分钟涨幅相对于全天涨幅的方向一致性。

    正值 → 开盘方向与全天一致（趋势延续）
    负值 → 开盘方向与全天相反（盘中反转）
    绝对值近 0 → 开盘无方向或全天窄幅震荡

    计算方式：open_30m_return / abs(daily_return) × sign(daily_return)
    然后取滚动窗口均值。
    """

    factor_name = "open_effect_5m"
    description = "Opening 30min return vs full-day return consistency"
    description_cn = "开盘效应（5分钟线·前30分钟/全天一致性）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                # 前 6 根 bar = 开盘 30 min (0935-1000)
                open_bars = mbars.head(6)
                if len(open_bars) < 6:
                    return np.nan
                open_ret = (open_bars.iloc[-1]["close"] / open_bars.iloc[0]["open"] - 1)

                # 全天涨跌幅用日线 close/open
                daily_ret = (mbars.iloc[-1]["close"] / mbars.iloc[0]["open"] - 1)
                if abs(daily_ret) < 0.0005:
                    return 0.0
                return open_ret / daily_ret

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        # 滚动均值
        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df


# ============================================================================
# 因子 2: VWAP 偏离
# ============================================================================


@register_factor
class VWAPDeviationFactor(FactorBase):
    """收盘价相对日内 VWAP（成交量加权均价）的偏离度。

    正值 → 收盘价高于 VWAP（机构可能尾盘推高）
    负值 → 收盘价低于 VWAP（尾盘承压）

    VWAP = Σ(close_i × vol_i) / Σ(vol_i)
    """

    factor_name = "vwap_deviation_5m"
    description = "Close price deviation from intraday VWAP"
    description_cn = "VWAP偏离（5分钟线·收盘/vwap-1）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                total_vol = mbars["vol"].sum()
                if total_vol == 0:
                    return np.nan
                vwap = (mbars["close"] * mbars["vol"]).sum() / total_vol
                close = mbars.iloc[-1]["close"]
                if vwap == 0:
                    return np.nan
                return close / vwap - 1.0

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df


# ============================================================================
# 因子 3: 尾盘放量
# ============================================================================


@register_factor
class TailVolumeFactor(FactorBase):
    """尾盘30分钟成交量占全天总成交量的比例。

    高值 → 尾盘资金活跃（可能抢筹/出货）
    低值 → 尾盘清淡，资金无操作意愿

    正值预期：尾盘放量上涨 → 次日延续；放量下跌 → 次日反转
    （取决于配合方向，单独使用效果有限，建议和 open_effect 配合）
    """

    factor_name = "tail_volume_5m"
    description = "Last 30min volume / total daily volume"
    description_cn = "尾盘放量（5分钟线·最后30分钟量比）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                total_vol = mbars["vol"].sum()
                if total_vol == 0:
                    return np.nan
                # 最后 30 分钟：bar_time ≥ 143000 的 bar（约 6 根）
                tail = mbars[mbars["time_str"] >= "143000"]
                if tail.empty:
                    return 0.0
                return tail["vol"].sum() / total_vol

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df


# ============================================================================
# 因子 4: 日内反转
# ============================================================================


@register_factor
class IntradayReversalFactor(FactorBase):
    """上午 vs 下午涨跌幅的一致性。

    正值 → 上午涨、下午也涨（趋势延续）
    负值 → 上午涨、下午跌 或反之（日内反转）

    计算：am_return × pm_return
    如果同向则正值，反向则负值
    """

    factor_name = "intraday_reversal_5m"
    description = "AM vs PM return consistency (positive=trend, negative=reversal)"
    description_cn = "日内反转（5分钟线·上午×下午方向）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                # 上午：bar_time ≤ 113000
                am = mbars[mbars["time_str"] <= "113000"]
                # 下午：bar_time ≥ 130000
                pm = mbars[mbars["time_str"] >= "130000"]

                if am.empty or pm.empty:
                    return np.nan

                am_open = am.iloc[0]["open"]
                am_close = am.iloc[-1]["close"]
                pm_open = pm.iloc[0]["open"]
                pm_close = pm.iloc[-1]["close"]

                if am_open == 0 or pm_open == 0:
                    return np.nan

                am_ret = am_close / am_open - 1
                pm_ret = pm_close / pm_open - 1

                return am_ret * pm_ret

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df


# ============================================================================
# 因子 5: 成交量集中度
# ============================================================================


@register_factor
class VolumeConcentrationFactor(FactorBase):
    """日内成交量分布的集中程度。

    高值 → 成交量集中在少数几根 K 线上（主力行为明显）
    低值 → 成交量均匀分布（散户行情）

    计算：max(单根bar成交量) / mean(单根bar成交量)
    """

    factor_name = "volume_concentration_5m"
    description = "Max single bar volume / mean bar volume"
    description_cn = "成交量集中度（5分钟线·最大/平均量比）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                mean_vol = mbars["vol"].mean()
                if mean_vol == 0:
                    return np.nan
                return mbars["vol"].max() / mean_vol

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df


# ============================================================================
# 因子 6: 上午放量比
# ============================================================================


@register_factor
class AMVolumeRatioFactor(FactorBase):
    """上午成交量占全天总成交量的比例。

    高值 → 上午交易活跃（早盘博弈激烈）
    低值 → 下午放量（尾盘资金入场）

    A 股规律：上午量通常 > 50%，异常偏离可能提示资金行为变化
    """

    factor_name = "am_volume_ratio_5m"
    description = "AM volume / total daily volume"
    description_cn = "上午放量比（5分钟线·上午量/全天量）"
    category = "量价类·分钟级"

    def __init__(self, window: int = 10):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = np.nan

        for idx in df.index:
            ts_code = df.at[idx, "ts_code"]
            trade_date = df.at[idx, "trade_date"]

            def _calc(mbars: pd.DataFrame) -> float:
                total_vol = mbars["vol"].sum()
                if total_vol == 0:
                    return np.nan
                am = mbars[mbars["time_str"] <= "113000"]
                if am.empty:
                    return np.nan
                return am["vol"].sum() / total_vol

            df.at[idx, self.factor_name] = _compute_daily_factor(
                ts_code, trade_date, self.factor_name, _calc
            )

        df[self.factor_name] = df.groupby("ts_code")[self.factor_name].transform(
            lambda x: x.rolling(self.window, min_periods=3).mean()
        )
        return df

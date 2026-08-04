#!/usr/bin/env python3
"""
因子组合搜索 — 回测优化 v2（单次遍历 + 智能组合）

策略：
  1. 单次遍历所有 stock×date，拉取分钟数据 → 一次计算 6 个因子值
  2. 缓存到 parquet，后续搜索秒级完成
  3. 精选 30-50 个有意义的日级+分钟级组合进行回测

用法：
  python3 scripts/search_best_combo.py
"""

import sys
import time
import itertools
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data import qmt_fetcher
from src.backtest.engine import BacktestEngine
from src.factors.scorer import _factor_to_score_col, zscore_cross_section

# ============================================================
# 配置
# ============================================================
CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

END_DATE = "20260722"
N_STOCKS = 100
N_DAYS = 40
REBALANCE_FREQ = "W"
TOP_N = 10

MINUTE_FACTOR_NAMES = [
    "intraday_reversal_5m",
    "open_effect_5m",
    "vwap_deviation_5m",
    "tail_volume_5m",
    "volume_concentration_5m",
    "am_vol…o_5m",
]

FACTOR_DIRECTION = {
    "roe_yoy_rank": +1,
    "pe_ttm_rank": -1,
    "momentum_20d": -1,
    "trend_60d": -1,
    "volatility_20d": -1,
    "intraday_reversal_5m": +1,
    "open_effect_5m": +1,
    "vwap_deviation_5m": +1,
    "tail_volume_5m": -1,
    "volume_concentration_5m": -1,
    "am_vol…o_5m": -1,
}

print("=" * 70)
print("因子组合搜索 v2 — 单次遍历 + 智能组合")
print(f"  股票池: Top {N_STOCKS} × {N_DAYS} 交易日")
print(f"  调仓: {REBALANCE_FREQ}  |  持仓: {TOP_N} 只")
print("=" * 70)

# ============================================================
# Phase 1: 单次遍历计算所有分钟因子 → 缓存
# ============================================================
CACHE_FILE = CACHE_DIR / f"minute_factors_v2_{END_DATE}_{N_STOCKS}s_{N_DAYS}d.pkl"
CACHE_PARQUET = CACHE_DIR / f"minute_factors_v2_{END_DATE}_{N_STOCKS}s_{N_DAYS}d.parquet"

# Check both formats
if CACHE_FILE.exists():
    print(f"\n✅ 缓存命中: {CACHE_FILE}")
    df_minute = pd.read_pickle(CACHE_FILE)
    stocks_list = sorted(df_minute["ts_code"].unique())
    dates_list = sorted(df_minute["trade_date"].unique())
    print(f"   形状: {df_minute.shape}  |  {len(stocks_list)} 股 × {len(dates_list)} 天")
elif CACHE_PARQUET.exists():
    print(f"\n✅ 缓存命中: {CACHE_PARQUET}")
    df_minute = pd.read_parquet(CACHE_PARQUET)
    stocks_list = sorted(df_minute["ts_code"].unique())
    dates_list = sorted(df_minute["trade_date"].unique())
    print(f"   形状: {df_minute.shape}  |  {len(stocks_list)} 股 × {len(dates_list)} 天")
else:
    print(f"\n[Phase 1] 单次遍历预计算所有分钟因子")

    # 1a: 获取日期
    print("  [1a] 交易日历...")
    td = qmt_fetcher._get_json("/api/trade_dates", {
        "start_date": "20260301", "end_date": END_DATE
    })
    all_dates = sorted([d for d in td.get("trade_dates", []) if d <= END_DATE])
    dates_list = all_dates[-N_DAYS:]
    print(f"    日期: {dates_list[0]} ~ {dates_list[-1]} ({len(dates_list)} 天)")

    # 1b: 选股
    print(f"  [1b] 选 Top {N_STOCKS}...")
    df_all = qmt_fetcher.fetch_all_stocks_for_date(END_DATE)
    if df_all.empty:
        for d in reversed(dates_list):
            df_all = qmt_fetcher.fetch_all_stocks_for_date(d)
            if not df_all.empty:
                break
    df_all = df_all.dropna(subset=["amount"])
    stocks_list = df_all.nlargest(N_STOCKS, "amount")["ts_code"].tolist()

    # 过滤：只保留有分钟数据的（尝试拉一天验证）
    print(f"  [1c] 验证分钟数据覆盖（拉首批5只测试）...")
    valid_codes = []
    for code in stocks_list[:5]:
        try:
            df_test = qmt_fetcher.fetch_minute_kline(code, f"{dates_list[-1]}093000", f"{dates_list[-1]}150000")
            if not df_test.empty and len(df_test) > 5:
                valid_codes.append(code)
        except Exception:
            pass
    # 剩余的假设都有效
    valid_codes.extend(stocks_list[5:])
    stocks_list = valid_codes
    print(f"    有效股票: {len(stocks_list)}")

    # 1d: 单次遍历计算
    print(f"  [1d] 单次遍历: {len(stocks_list)} × {len(dates_list)} = {len(stocks_list)*len(dates_list)} 次请求")
    print(f"    预计耗时: ~{len(stocks_list)*len(dates_list)*0.4/60:.0f} 分钟")

    results = []
    t0 = time.time()
    total_requests = len(stocks_list) * len(dates_list)
    done = 0

    for ts_code in stocks_list:
        for d in dates_list:
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (total_requests - done)
                print(f"    进度: {done}/{total_requests} ({done/total_requests*100:.0f}%)  "
                      f"已用 {elapsed:.0f}s  预计剩余 {eta:.0f}s")

            try:
                df_bar = qmt_fetcher.fetch_minute_kline(
                    ts_code, f"{d}093000", f"{d}150000"
                )
            except Exception:
                continue

            if df_bar.empty or len(df_bar) < 10:
                continue

            df_bar = df_bar[df_bar["is_trading"] == 1].copy()
            df_bar["time_str"] = df_bar["bar_time"].str[8:14]
            total_vol = df_bar["vol"].sum()
            if total_vol == 0:
                continue

            close_price = df_bar.iloc[-1]["close"]
            open_price = df_bar.iloc[0]["open"]

            # --- 日内反转 ---
            am = df_bar[df_bar["time_str"] <= "113000"]
            pm = df_bar[df_bar["time_str"] >= "130000"]
            if not am.empty and not pm.empty:
                am_ret = am.iloc[-1]["close"] / am.iloc[0]["open"] - 1
                pm_ret = pm.iloc[-1]["close"] / pm.iloc[0]["open"] - 1
                intraday_rev = am_ret * pm_ret
            else:
                intraday_rev = np.nan

            # --- 开盘效应 ---
            open_bars = df_bar.head(6)
            if len(open_bars) >= 6:
                open_ret = open_bars.iloc[-1]["close"] / open_bars.iloc[0]["open"] - 1
                daily_ret = close_price / open_price - 1
                if abs(daily_ret) >= 0.0005:
                    open_effect = open_ret / daily_ret
                else:
                    open_effect = 0.0
            else:
                open_effect = np.nan

            # --- VWAP偏离 ---
            vwap = (df_bar["close"] * df_bar["vol"]).sum() / total_vol
            vwap_dev = close_price / vwap - 1 if vwap > 0 else np.nan

            # --- 尾盘放量 ---
            tail = df_bar[df_bar["time_str"] >= "143000"]
            tail_vol = tail["vol"].sum() / total_vol if not tail.empty else 0.0

            # --- 量集中度 ---
            mean_vol = df_bar["vol"].mean()
            vol_conc = df_bar["vol"].max() / mean_vol if mean_vol > 0 else np.nan

            # --- 上午放量比 ---
            am_vol_ratio = am["vol"].sum() / total_vol if not am.empty else np.nan

            results.append({
                "trade_date": d,
                "ts_code": ts_code,
                "intraday_reversal_5m": intraday_rev,
                "open_effect_5m": open_effect,
                "vwap_deviation_5m": vwap_dev,
                "tail_volume_5m": tail_vol,
                "volume_concentration_5m": vol_conc,
                "am_vol…o_5m": am_vol_ratio,
            })

    total_t = time.time() - t0
    print(f"    总耗时: {total_t:.1f}s ({total_t/60:.1f}min)")
    print(f"    结果: {len(results)} 条记录")

    df_minute = pd.DataFrame(results)
    # 滚动平滑（window=5）
    for col in MINUTE_FACTOR_NAMES:
        if col in df_minute.columns:
            df_minute[col] = df_minute.groupby("ts_code")[col].transform(
                lambda x: x.rolling(5, min_periods=3).mean()
            )

    df_minute.to_pickle(CACHE_FILE)
    print(f"    缓存: {CACHE_FILE}")

# ============================================================
# Phase 2: 获取日线 + 计算日级因子
# ============================================================
print(f"\n[Phase 2] 日线数据 + 日级因子...")
t0 = time.time()

# 拉日线
daily_data = {}
for i, d in enumerate(dates_list):
    df = qmt_fetcher.fetch_all_stocks_for_date(d)
    if not df.empty:
        daily_data[d] = df

stock_set = set(stocks_list)
all_rows = []
for d, df_day in daily_data.items():
    for _, row in df_day[df_day["ts_code"].isin(stock_set)].iterrows():
        all_rows.append({
            "trade_date": d,
            "ts_code": row["ts_code"],
            "open": row.get("open", row["close"]),
            "close": row["close"],
            "vol": row.get("vol", 0),
            "amount": row.get("amount", 0),
        })
df_daily = pd.DataFrame(all_rows)
df_daily = df_daily.sort_values(["ts_code", "trade_date"])

# 合并分钟因子
df = df_daily.merge(df_minute, on=["trade_date", "ts_code"], how="inner")
print(f"  合并后: {len(df)} 行  |  {df['ts_code'].nunique()} 股  |  {df['trade_date'].nunique()} 天")

# 计算日级因子
df["is_trading"] = True
df["return_1d"] = df.groupby("ts_code")["close"].pct_change()
df["momentum_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).mean()
)
df["trend_60d"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.pct_change(60)
)
df["volatility_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).std()
)
# PE_TTM proxy（cross-sectional rank of close-to-volume ratio）
df["close_vol_ratio"] = df["close"] / (df["vol"] + 1)
df["pe_ttm_rank"] = df.groupby("trade_date")["close_vol_ratio"].transform(
    lambda x: x.rank(pct=True)
)
df["roe_yoy_rank"] = df.groupby("trade_date")["close"].transform(
    lambda x: x.rank(pct=True)
)

# 清理 (趋势因子放宽窗口)
df["trend_60d"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.pct_change(30)  # 改用30天（数据只有40天）
)
required_cols = ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "trend_60d", "volatility_20d"]
# 只要求日级因子有值，分钟因子允许缺失
minute_cols = [c for c in MINUTE_FACTOR_NAMES if c in df.columns]
df = df.dropna(subset=required_cols, how="any")
df[minute_cols] = df[minute_cols].fillna(0)  # 缺失分钟因子填0 (中性)
print(f"  清洗后: {len(df)} 行  |  {df['ts_code'].nunique()} 股")

print(f"  耗时: {time.time()-t0:.1f}s")

# ============================================================
# Phase 3: 构建精选组合 + 回测
# ============================================================
print(f"\n[Phase 3] 精选组合回测...")

@dataclass
class ComboResult:
    name: str
    factors: list[str]
    weights: dict[str, float]
    sharpe: float = 0
    total_return: float = 0
    max_drawdown: float = 0
    calmar: float = 0
    annual_return: float = 0
    win_rate: float = 0
    n_trades: int = 0


def run_bt(df_base: pd.DataFrame, name: str, factors: list[str],
           weights: dict[str, float]) -> ComboResult | None:
    """对一组因子+权重跑回测。"""
    df_scored = df_base.copy()
    for f in factors:
        if f in df_scored.columns:
            sc = _factor_to_score_col(f)
            try:
                df_scored[sc] = zscore_cross_section(df_scored, f)
            except Exception:
                df_scored[sc] = 0

    df_scored["total_score"] = 0.0
    for f in factors:
        sc = _factor_to_score_col(f)
        if sc in df_scored.columns:
            direction = FACTOR_DIRECTION.get(f, 1)
            df_scored["total_score"] += df_scored[sc].fillna(0) * weights.get(f, 0) * direction

    try:
        bt = engine.run(df_scored)
    except Exception:
        return None

    m = bt.metrics
    return ComboResult(
        name=name, factors=factors, weights=weights,
        sharpe=m.get("sharpe_ratio", 0),
        total_return=m.get("total_return", 0),
        max_drawdown=m.get("max_drawdown", 0),
        calmar=m.get("calmar_ratio", 0),
        annual_return=m.get("annual_return", 0),
        win_rate=m.get("win_rate", 0),
        n_trades=m.get("total_trades", 0),
    )


engine = BacktestEngine(
    initial_capital=1_000_000, top_n=TOP_N,
    rebalance_freq=REBALANCE_FREQ,
    risk_control_enabled=False,
    position_sizing_method="equal_weight",
)

all_results = []

# ============================================================
# 构建测试组合
# ============================================================

# --- 基准组：纯日级因子 ---
DAILY_COMBOS = {
    "D1_单ROE": ["roe_yoy_rank"],
    "D2_ROE+低PE": ["roe_yoy_rank", "pe_ttm_rank"],
    "D3_ROE+动量反转": ["roe_yoy_rank", "momentum_20d"],
    "D4_ROE+趋势反转": ["roe_yoy_rank", "trend_60d"],
    "D5_ROE+低波": ["roe_yoy_rank", "volatility_20d"],
    "D6_三因子经典": ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d"],
    "D7_三因子稳健": ["roe_yoy_rank", "pe_ttm_rank", "volatility_20d"],
    "D8_四因子均衡": ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "volatility_20d"],
    "D9_五因子全开": ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "trend_60d", "volatility_20d"],
}

print("  测试基准日级组合...")
for name, factors in DAILY_COMBOS.items():
    n = len(factors)
    # 等权重
    w = {f: 1.0/n for f in factors}
    r = run_bt(df, name, factors, w)
    if r:
        all_results.append(r)
    # ROE主导权重
    if n > 1:
        w2 = {"roe_yoy_rank": 0.4}
        rest_w = 0.6 / (n - 1)
        for f in factors:
            if f != "roe_yoy_rank":
                w2[f] = rest_w
        r2 = run_bt(df, name + "(ROE主导)", factors, w2)
        if r2:
            all_results.append(r2)

# --- 混合组：逐个加入分钟因子 ---
print("  测试分钟因子混合...")
for daily_name, daily_fs in [
    ("ROE+动量", ["roe_yoy_rank", "momentum_20d"]),
    ("ROE+动量+低波", ["roe_yoy_rank", "momentum_20d", "volatility_20d"]),
    ("ROE+PE+动量", ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d"]),
    ("ROE+PE+动量+低波", ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "volatility_20d"]),
]:
    # 每个分钟因子单独加入
    for mf in ["intraday_reversal_5m", "open_effect_5m",
               "vwap_deviation_5m", "tail_volume_5m"]:
        factors = daily_fs + [mf]
        name = f"M_{daily_name}+{mf.replace('_5m','')}"
        # 日级:分钟 = 70:30
        n_daily = len(daily_fs)
        daily_w = 0.7 / n_daily
        w = {f: daily_w for f in daily_fs}
        w[mf] = 0.3
        r = run_bt(df, name, factors, w)
        if r:
            all_results.append(r)

    # 所有分钟因子一起加入
    all_mf = ["intraday_reversal_5m", "open_effect_5m",
              "vwap_deviation_5m", "tail_volume_5m"]
    factors = daily_fs + all_mf
    name = f"M_{daily_name}+4分钟"
    n_daily = len(daily_fs)
    daily_w = 0.55 / n_daily
    w = {f: daily_w for f in daily_fs}
    for mf in all_mf:
        w[mf] = 0.45 / 4
    r = run_bt(df, name, factors, w)
    if r:
        all_results.append(r)

    # 分钟因子权重更高的版本
    w2 = {f: 0.4 / n_daily for f in daily_fs}
    for mf in all_mf:
        w2[mf] = 0.6 / 4
    r2 = run_bt(df, name + "(分钟主导)", factors, w2)
    if r2:
        all_results.append(r2)

# --- 分钟因子组合（纯分钟因子） ---
print("  测试纯分钟因子...")
for mf_combo in [
    ["intraday_reversal_5m"],
    ["open_effect_5m"],
    ["vwap_deviation_5m"],
    ["intraday_reversal_5m", "open_effect_5m"],
    ["intraday_reversal_5m", "vwap_deviation_5m"],
    ["intraday_reversal_5m", "open_effect_5m", "vwap_deviation_5m"],
    MINUTE_FACTOR_NAMES[:4],
]:
    n = len(mf_combo)
    w = {f: 1.0/n for f in mf_combo}
    name = "P_" + "+".join([f.replace("_5m","")[:10] for f in mf_combo])
    r = run_bt(df, name, mf_combo, w)
    if r:
        all_results.append(r)

# ============================================================
# Phase 4: 排名 + 报告
# ============================================================
print(f"\n[Phase 4] 排名 ({len(all_results)} 个组合)...")

all_results.sort(key=lambda x: x.sharpe, reverse=True)

# ── Top 20 ──
print(f"\n{'='*90}")
print(f"🏆 Top 25 组合（按 Sharpe 排序）")
print(f"{'='*90}")
print(f"{'Rank':<5} {'Sharpe':>7} {'Calmar':>7} {'收益':>8} {'回撤':>8} {'胜率':>7} {'组合'}")
print("-" * 90)

for i, r in enumerate(all_results[:25]):
    print(f"{i+1:<5} {r.sharpe:>7.3f} {r.calmar:>7.3f} {r.total_return:>7.1%} "
          f"{r.max_drawdown:>7.1%} {r.win_rate:>6.1%} {r.name[:55]}")

# ── 最佳组合详情 ──
best = all_results[0]
print(f"\n{'='*90}")
print(f"🥇 最佳: {best.name}")
print(f"{'='*90}")
print(f"因子 ({len(best.factors)}):")
for f in best.factors:
    direction = FACTOR_DIRECTION.get(f, 1)
    dir_s = "(+)" if direction > 0 else "(-)"
    print(f"  {dir_s} {f:35s}  {best.weights[f]:.0%}")
print(f"\n回测指标:")
print(f"  Sharpe:   {best.sharpe:.4f}")
print(f"  Calmar:   {best.calmar:.4f}")
print(f"  总收益:   {best.total_return:.2%}")
print(f"  年化收益: {best.annual_return:.2%}")
print(f"  最大回撤: {best.max_drawdown:.2%}")
print(f"  胜率:     {best.win_rate:.2%}")
print(f"  交易:     {best.n_trades}")

# ── 分钟因子提升分析 ──
print(f"\n{'='*90}")
print(f"📊 分钟因子提升分析")
print(f"{'='*90}")

daily_only = [r for r in all_results if r.name.startswith("D")]
mixed = [r for r in all_results if r.name.startswith("M_")]
pure_minute = [r for r in all_results if r.name.startswith("P_")]

if daily_only:
    best_daily = max(daily_only, key=lambda x: x.sharpe)
    avg_ds = np.mean([r.sharpe for r in daily_only])
    print(f"  纯日级最佳: {best_daily.name:25s}  Sharpe={best_daily.sharpe:.3f}  收益={best_daily.total_return:.1%}")
    print(f"  纯日级平均: Sharpe={avg_ds:.3f}")

if mixed:
    best_mixed = max(mixed, key=lambda x: x.sharpe)
    avg_ms = np.mean([r.sharpe for r in mixed])
    print(f"  混合最佳:   {best_mixed.name:25s}  Sharpe={best_mixed.sharpe:.3f}  收益={best_mixed.total_return:.1%}")
    print(f"  混合平均:   Sharpe={avg_ms:.3f}")
    if daily_only:
        delta = avg_ms - avg_ds
        print(f"  分钟因子平均提升: {'+' if delta > 0 else ''}{delta:.3f} Sharpe")

if pure_minute:
    best_pm = max(pure_minute, key=lambda x: x.sharpe)
    print(f"  纯分钟最佳: {best_pm.name:25s}  Sharpe={best_pm.sharpe:.3f}  收益={best_pm.total_return:.1%}")

# ── IC 验证（用 Spearman 计算各因子的 ICIR）──
print(f"\n{'='*90}")
print(f"📈 IC 验证（Spearman Rank IC）")
print(f"{'='*90}")

df["next_return"] = np.nan
for ts in df["ts_code"].unique():
    s = df[df["ts_code"] == ts].sort_values("trade_date")
    closes = s["close"].values
    returns = np.diff(closes) / closes[:-1]
    idx_map = {d: i for i, d in enumerate(s["trade_date"])}
    for i, d in enumerate(s["trade_date"].iloc[:-1]):
        if i < len(returns):
            df.loc[(df["ts_code"] == ts) & (df["trade_date"] == d), "next_return"] = returns[i]

from scipy.stats import spearmanr

all_factor_names = (list(DAILY_COMBOS["D9_五因子全开"]) + MINUTE_FACTOR_NAMES[:4])
print(f"{'Factor':<30s} {'IC Mean':>8s} {'IC Std':>8s} {'ICIR':>8s} {'Win':>7s} {'Rating'}")
print("-" * 75)

for fn in all_factor_names:
    if fn not in df.columns:
        continue
    ic_list = []
    for d in dates_list[5:]:  # skip first 5 for rolling window
        day = df[(df["trade_date"] == d) & df[fn].notna() & df["next_return"].notna()]
        if len(day) < 20:
            continue
        vals = day[fn].values
        rets = day["next_return"].values
        # winsorize 1%
        lo, hi = np.percentile(vals, [1, 99])
        mask = (vals >= lo) & (vals <= hi)
        if mask.sum() < 20:
            continue
        try:
            ic, _ = spearmanr(vals[mask], rets[mask])
            if not np.isnan(ic):
                ic_list.append(ic)
        except Exception:
            pass

    if len(ic_list) < 5:
        print(f"  {fn:<28s}  {'—':>8s}  {'—':>8s}  {'—':>8s}  {'—':>7s}  数据不足")
        continue

    ic_mean = np.mean(ic_list)
    ic_std = np.std(ic_list, ddof=1)
    icir = ic_mean / ic_std if ic_std > 0 else 0
    wr = sum(1 for x in ic_list if x > 0) / len(ic_list)
    rating = "⭐⭐⭐" if abs(icir) >= 0.5 else ("⭐⭐" if abs(icir) >= 0.25 else ("⭐" if abs(icir) >= 0.1 else "❌"))
    print(f"  {fn:<28s}  {ic_mean:>+8.4f}  {ic_std:>8.4f}  {icir:>+8.3f}  {wr:>6.1%}  {rating}")

print(f"\n✅ 完成 — {len(all_results)} 个组合已测试")

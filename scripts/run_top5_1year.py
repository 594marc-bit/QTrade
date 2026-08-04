#!/usr/bin/env python3
"""
Top 5 组合 × 近1年回测 (多线程优化版)

使用 ThreadPoolExecutor 并行拉取分钟因子数据，大幅缩短 Phase 1 耗时。
"""

import sys
import time
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

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

END_DATE = "20260724"
START_DATE = "20250724"
N_STOCKS = 100
REBALANCE_FREQ = "W"
TOP_N = 10
MAX_WORKERS = 10  # 并发线程数（保守，避免QMT API压力）

LOG_FILE = CACHE_DIR / f"top5_1year_{END_DATE}.log"


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()


# ============================================================
# Phase 1: 并行预计算分钟因子
# ============================================================
CACHE_FILE = CACHE_DIR / f"top5_1year_{END_DATE}_{N_STOCKS}s.pkl"

FACTOR_DIRECTION = {
    "roe_yoy_rank": +1, "pe_ttm_rank": -1, "momentum_20d": -1,
    "trend_60d": -1, "volatility_20d": -1,
    "intraday_reversal_5m": +1, "open_effect_5m": +1,
    "vwap_deviation_5m": +1, "tail_volume_5m": -1,
}

TOP5_COMBOS = [
    {
        "name": "1.ROE+低波(ROE主导)",
        "factors": ["roe_yoy_rank", "volatility_20d"],
        "weights": {"roe_yoy_rank": 0.40, "volatility_20d": 0.60},
        "type": "纯日级",
    },
    {
        "name": "2.ROE+低波",
        "factors": ["roe_yoy_rank", "volatility_20d"],
        "weights": {"roe_yoy_rank": 0.50, "volatility_20d": 0.50},
        "type": "纯日级",
    },
    {
        "name": "3.ROE+PE+低波(ROE主导)",
        "factors": ["roe_yoy_rank", "pe_ttm_rank", "volatility_20d"],
        "weights": {"roe_yoy_rank": 0.40, "pe_ttm_rank": 0.30, "volatility_20d": 0.30},
        "type": "纯日级",
    },
    {
        "name": "4.ROE+动量+低波+4分钟(分钟主导)",
        "factors": [
            "roe_yoy_rank", "momentum_20d", "volatility_20d",
            "intraday_reversal_5m", "open_effect_5m",
            "vwap_deviation_5m", "tail_volume_5m",
        ],
        "weights": {
            "roe_yoy_rank": 0.40 / 3, "momentum_20d": 0.40 / 3,
            "volatility_20d": 0.40 / 3, "intraday_reversal_5m": 0.60 / 4,
            "open_effect_5m": 0.60 / 4, "vwap_deviation_5m": 0.60 / 4,
            "tail_volume_5m": 0.60 / 4,
        },
        "type": "🔥 混合",
    },
    {
        "name": "5.ROE+PE+动量+低波+VWAP偏离",
        "factors": [
            "roe_yoy_rank", "pe_ttm_rank", "momentum_20d",
            "volatility_20d", "vwap_deviation_5m",
        ],
        "weights": {
            "roe_yoy_rank": 0.70 / 4, "pe_ttm_rank": 0.70 / 4,
            "momentum_20d": 0.70 / 4, "volatility_20d": 0.70 / 4,
            "vwap_deviation_5m": 0.30,
        },
        "type": "混合",
    },
]


def compute_minute_factors_for_bar(df_bar):
    """从一根5分钟K线的DataFrame计算所有分钟因子"""
    df_bar = df_bar[df_bar["is_trading"] == 1].copy()
    df_bar["time_str"] = df_bar["bar_time"].str[8:14]
    total_vol = df_bar["vol"].sum()
    if total_vol == 0:
        return {}

    close_price = df_bar.iloc[-1]["close"]
    open_price = df_bar.iloc[0]["open"]
    am = df_bar[df_bar["time_str"] <= "113000"]
    pm = df_bar[df_bar["time_str"] >= "130000"]

    # 日内反转
    intraday_rev = np.nan
    if not am.empty and not pm.empty:
        am_ret = am.iloc[-1]["close"] / am.iloc[0]["open"] - 1
        pm_ret = pm.iloc[-1]["close"] / pm.iloc[0]["open"] - 1
        intraday_rev = am_ret * pm_ret

    # 开盘效应
    open_bars = df_bar.head(6)
    open_effect = np.nan
    if len(open_bars) >= 6:
        open_ret = open_bars.iloc[-1]["close"] / open_bars.iloc[0]["open"] - 1
        daily_ret = close_price / open_price - 1
        open_effect = open_ret / daily_ret if abs(daily_ret) >= 0.0005 else 0.0

    # VWAP偏离
    vwap = (df_bar["close"] * df_bar["vol"]).sum() / total_vol
    vwap_dev = close_price / vwap - 1 if vwap > 0 else np.nan

    # 尾盘放量
    tail = df_bar[df_bar["time_str"] >= "143000"]
    tail_vol = tail["vol"].sum() / total_vol if not tail.empty else 0.0

    return {
        "intraday_reversal_5m": intraday_rev,
        "open_effect_5m": open_effect,
        "vwap_deviation_5m": vwap_dev,
        "tail_volume_5m": tail_vol,
    }


def fetch_single(ts_code, d):
    """拉取单日单股的分钟因子，返回 dict 或 None"""
    try:
        df_bar = qmt_fetcher.fetch_minute_kline(
            ts_code, f"{d}093000", f"{d}150000"
        )
    except Exception:
        return None

    if df_bar.empty or len(df_bar) < 10:
        return None

    factors = compute_minute_factors_for_bar(df_bar)
    return {"trade_date": d, "ts_code": ts_code, **factors}


if CACHE_FILE.exists():
    log(f"✅ 缓存命中: {CACHE_FILE}")
    df_minute = pd.read_pickle(CACHE_FILE)
    stocks_list = sorted(df_minute["ts_code"].unique())
    dates_list = sorted(df_minute["trade_date"].unique())
    log(f"   {len(df_minute)} 行, {len(stocks_list)} 股, {len(dates_list)} 天")
else:
    log(f"{'='*60}")
    log(f"Phase 1: 并行预计算分钟因子 ({START_DATE}~{END_DATE})")
    log(f"{'='*60}")

    # 交易日历
    td = qmt_fetcher._get_json("/api/trade_dates", {
        "start_date": START_DATE, "end_date": END_DATE
    })
    dates_list = sorted([d for d in td.get("trade_dates", []) if START_DATE <= d <= END_DATE])
    log(f"交易日: {len(dates_list)} ({dates_list[0]}~{dates_list[-1]})")

    # 选股
    df_all = qmt_fetcher.fetch_all_stocks_for_date(END_DATE)
    df_all = df_all.dropna(subset=["amount"])
    stocks_list = df_all.nlargest(N_STOCKS, "amount")["ts_code"].tolist()

    # 验证分钟数据
    valid = []
    for code in stocks_list[:20]:
        try:
            df_t = qmt_fetcher.fetch_minute_kline(code, f"{dates_list[-1]}093000", f"{dates_list[-1]}150000")
            if not df_t.empty and len(df_t) > 5:
                valid.append(code)
        except Exception:
            pass
    valid.extend(stocks_list[20:])
    stocks_list = valid
    log(f"有效股票: {len(stocks_list)}")

    # 并行拉取
    total = len(stocks_list) * len(dates_list)
    log(f"并行拉取: {len(stocks_list)}股×{len(dates_list)}天 = {total} 请求, {MAX_WORKERS} 线程")
    est = total * 0.15 / MAX_WORKERS
    log(f"预计耗时: ~{est:.0f}s ({est/60:.1f}min)")

    t0 = time.time()
    tasks = [(c, d) for c in stocks_list for d in dates_list]
    results = []
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_single, c, d): (c, d) for c, d in tasks}
        for fut in as_completed(futures):
            done += 1
            try:
                res = fut.result(timeout=15)  # 单请求超时15秒
                if res is not None:
                    results.append(res)
                else:
                    errors += 1
            except Exception:
                errors += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done)
                log(f"进度: {done}/{total} ({done/total*100:.0f}%)  "
                    f"有效:{len(results)} 错误:{errors}  "
                    f"已用{elapsed:.0f}s 剩余{eta:.0f}s")

    total_t = time.time() - t0
    log(f"✅ Phase 1 完成! 耗时 {total_t:.0f}s ({total_t/60:.1f}min)")
    log(f"   有效记录: {len(results)}, 错误: {errors}")

    df_minute = pd.DataFrame(results)
    # 滚动平滑
    for col in ["intraday_reversal_5m", "open_effect_5m", "vwap_deviation_5m", "tail_volume_5m"]:
        if col in df_minute.columns:
            df_minute[col] = df_minute.groupby("ts_code")[col].transform(
                lambda x: x.rolling(5, min_periods=3).mean()
            )

    df_minute.to_pickle(CACHE_FILE)
    log(f"缓存已保存: {CACHE_FILE}")

# ============================================================
# Phase 2: 日线 + 日级因子
# ============================================================
log(f"\n{'='*60}")
log(f"Phase 2: 日线数据 + 日级因子")
log(f"{'='*60}")

t0 = time.time()
stock_set = set(stocks_list)
all_rows = []
for i, d in enumerate(dates_list):
    if i % 50 == 0:
        log(f"  拉取日线: {d} ({i+1}/{len(dates_list)})")
    try:
        df_day = qmt_fetcher.fetch_all_stocks_for_date(d)
        if not df_day.empty:
            for _, row in df_day[df_day["ts_code"].isin(stock_set)].iterrows():
                all_rows.append({
                    "trade_date": d, "ts_code": row["ts_code"],
                    "open": row.get("open", row["close"]),
                    "close": row["close"],
                    "vol": row.get("vol", 0),
                    "amount": row.get("amount", 0),
                })
    except Exception:
        pass

df_daily = pd.DataFrame(all_rows)
log(f"  日线: {len(df_daily)} 行")

df = df_daily.merge(df_minute, on=["trade_date", "ts_code"], how="inner")
df = df.sort_values(["ts_code", "trade_date"])
log(f"  合并: {len(df)} 行, {df['ts_code'].nunique()} 股, {df['trade_date'].nunique()} 天")

# 日级因子
df["return_1d"] = df.groupby("ts_code")["close"].pct_change()
df["momentum_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).mean()
)
df["volatility_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).std()
)
df["close_vol_ratio"] = df["close"] / (df["vol"] + 1)
df["pe_ttm_rank"] = df.groupby("trade_date")["close_vol_ratio"].transform(
    lambda x: x.rank(pct=True)
)
df["roe_yoy_rank"] = df.groupby("trade_date")["close"].transform(
    lambda x: x.rank(pct=True)
)

required_cols = ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "volatility_20d"]
minute_cols = [c for c in ["intraday_reversal_5m", "open_effect_5m", "vwap_deviation_5m", "tail_volume_5m"] if c in df.columns]
df = df.dropna(subset=required_cols, how="any")
df[minute_cols] = df[minute_cols].fillna(0)
log(f"  清洗后: {len(df)} 行, {df['ts_code'].nunique()} 股")
log(f"  耗时: {time.time()-t0:.1f}s")

# ============================================================
# Phase 3: Top 5 回测
# ============================================================
log(f"\n{'='*60}")
log(f"Phase 3: Top 5 组合回测")
log(f"{'='*60}")

engine = BacktestEngine(
    initial_capital=1_000_000, top_n=TOP_N,
    rebalance_freq=REBALANCE_FREQ,
    risk_control_enabled=False,
    position_sizing_method="equal_weight",
)


@dataclass
class ComboResult:
    name: str
    combo_type: str
    factors: list[str]
    weights: dict[str, float]
    sharpe: float = 0
    total_return: float = 0
    max_drawdown: float = 0
    calmar: float = 0
    annual_return: float = 0
    win_rate: float = 0
    n_trades: int = 0


def run_bt(df_base, combo):
    name = combo["name"]
    factors = combo["factors"]
    weights = combo["weights"]
    combo_type = combo["type"]

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
    except Exception as e:
        log(f"  ⚠️ {name} 回测失败: {e}")
        return None

    m = bt.metrics
    return ComboResult(
        name=name, combo_type=combo_type, factors=factors, weights=weights,
        sharpe=m.get("sharpe_ratio", 0), total_return=m.get("total_return", 0),
        max_drawdown=m.get("max_drawdown", 0), calmar=m.get("calmar_ratio", 0),
        annual_return=m.get("annual_return", 0), win_rate=m.get("win_rate", 0),
        n_trades=m.get("total_trades", 0),
    )


t0 = time.time()
results = []
for combo in TOP5_COMBOS:
    log(f"  回测: {combo['name']}")
    r = run_bt(df, combo)
    if r:
        results.append(r)
        log(f"    Sharpe={r.sharpe:.3f}  收益={r.total_return:.2%}  回撤={r.max_drawdown:.2%}")
    else:
        log(f"    ❌ 失败")

log(f"  回测耗时: {time.time()-t0:.1f}s")

# ============================================================
# Phase 4: 报告
# ============================================================
results.sort(key=lambda x: x.sharpe, reverse=True)

print(f"\n{'='*90}")
print(f"🏆 Top 5 组合 — 近1年回测结果 ({len(dates_list)} 交易日)")
print(f"  日期: {dates_list[0]} ~ {dates_list[-1]}")
print(f"  股票池: Top {N_STOCKS} 成交额 | 调仓: {REBALANCE_FREQ} | 持仓: {TOP_N} 只")
print(f"  并发线程: {MAX_WORKERS}")
print(f"{'='*90}")

print(f"\n{'Rank':<4} {'Sharpe':>7} {'Calmar':>7} {'收益':>9} {'回撤':>8} "
      f"{'胜率':>7} {'交易':>6} {'类型':>8} {'组合'}")
print("-" * 95)
for i, r in enumerate(results):
    print(f"{i+1:<4} {r.sharpe:>7.3f} {r.calmar:>7.3f} {r.total_return:>8.1%} "
          f"{r.max_drawdown:>7.1%} {r.win_rate:>6.1%} {r.n_trades:>6d} "
          f"{r.combo_type:>8s} {r.name}")

# 最佳详情
if results:
    best = results[0]
    print(f"\n{'='*90}")
    print(f"🥇 最佳组合: {best.name}")
    print(f"{'='*90}")
    print(f"类型: {best.combo_type}  |  因子数: {len(best.factors)}")
    for f in best.factors:
        d = FACTOR_DIRECTION.get(f, 1)
        print(f"  {'(+)' if d>0 else '(-)'} {f:35s}  {best.weights[f]:.0%}")
    print(f"\n  Sharpe: {best.sharpe:.4f}  |  Calmar: {best.calmar:.4f}")
    print(f"  总收益: {best.total_return:.2%}  |  年化: {best.annual_return:.2%}")
    print(f"  最大回撤: {best.max_drawdown:.2%}  |  胜率: {best.win_rate:.2%}  |  交易: {best.n_trades}")

# 分钟因子IC
print(f"\n{'='*90}")
print(f"📈 分钟因子 IC（近1年 Spearman）")
print(f"{'='*90}")

df["next_return"] = np.nan
for ts in df["ts_code"].unique():
    s = df[df["ts_code"] == ts].sort_values("trade_date")
    closes = s["close"].values
    rets = np.diff(closes) / closes[:-1]
    for i, d in enumerate(s["trade_date"].iloc[:-1]):
        if i < len(rets):
            df.loc[(df["ts_code"] == ts) & (df["trade_date"] == d), "next_return"] = rets[i]

from scipy.stats import spearmanr

mf_list = [c for c in ["intraday_reversal_5m", "open_effect_5m", "vwap_deviation_5m", "tail_volume_5m"] if c in df.columns]
print(f"{'Factor':<30s} {'IC Mean':>8s} {'IC Std':>8s} {'ICIR':>8s} {'Win':>7s} {'Rating'}")
print("-" * 75)

for fn in mf_list:
    ic_list = []
    for d in dates_list[5:]:
        day = df[(df["trade_date"] == d) & df[fn].notna() & df["next_return"].notna()]
        if len(day) < 20:
            continue
        vals = day[fn].values
        rets = day["next_return"].values
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

print(f"\n✅ 完成")

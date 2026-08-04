#!/usr/bin/env python3
"""
Top 5 组合 × 近1年回测 — 真实财务数据版

用 Tushare 本地数据库中真实的 PE_TTM 和 ROE YoY 替换之前的 proxy 因子：
  - roe_yoy_rank: close price rank proxy → 真实 roe_yoy (fina_indicator)
  - pe_ttm_rank: close/volume ratio proxy → 真实 pe_ttm (daily_basic)

用法: python3 -u scripts/run_top5_1year_real.py
"""

import sys
import time
import sqlite3
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
DB_PATH = Path(__file__).parent.parent / "data" / "stock_data.db"

END_DATE = "20260724"
START_DATE = "20250724"
REBALANCE_FREQ = "W"
TOP_N = 10

LOG_FILE = CACHE_DIR / f"top5_1year_real_{END_DATE}.log"


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()


FACTOR_DIRECTION = {
    "roe_yoy_rank": +1, "pe_ttm_rank": -1, "momentum_20d": -1,
    "volatility_20d": -1,
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

# ============================================================
# Phase 1: 交易日历 + 股票池 + 分钟因子缓存
# ============================================================
CACHE_MINUTE = CACHE_DIR / f"top5_1year_{END_DATE}_100s.pkl"

# 交易日历
td = qmt_fetcher._get_json("/api/trade_dates", {
    "start_date": START_DATE, "end_date": END_DATE
})
dates_list = sorted([d for d in td.get("trade_dates", []) if START_DATE <= d <= END_DATE])
log(f"交易日: {len(dates_list)} ({dates_list[0]}~{dates_list[-1]})")

# 股票池
df_all = qmt_fetcher.fetch_all_stocks_for_date(END_DATE)
df_all = df_all.dropna(subset=["amount"])
stocks_raw = df_all.nlargest(100, "amount")["ts_code"].tolist()

# 只保留在 DB 中有 daily_basic 数据的股票
db_stocks = set()
with sqlite3.connect(DB_PATH) as conn:
    rows = conn.execute(
        "SELECT DISTINCT ts_code FROM daily_basic "
        "WHERE trade_date >= ? AND trade_date <= ?",
        (START_DATE, END_DATE)
    ).fetchall()
    db_stocks = {r[0] for r in rows}
stocks_list = [s for s in stocks_raw if s in db_stocks][:80]
log(f"有效股票: {len(stocks_list)} (在DB daily_basic中：{len(db_stocks)}只)")

if CACHE_MINUTE.exists():
    log(f"✅ 分钟因子缓存命中")
    df_minute = pd.read_pickle(CACHE_MINUTE)
    # 过滤到当前股票池
    df_minute = df_minute[df_minute["ts_code"].isin(stocks_list)]
else:
    log(f"⚠️ 分钟因子缓存未找到，需要先运行 run_top5_1year.py Phase 1")
    sys.exit(1)

# ============================================================
# Phase 2: 从 DB 加载真实财务数据
# ============================================================
log(f"\n{'='*60}")
log(f"Phase 2: 加载真实财务数据 (pe_ttm + roe_yoy)")
log(f"{'='*60}")

t0 = time.time()

# 2a: 加载 daily_basic (pe_ttm)
log("  加载 daily_basic (pe_ttm)...")
with sqlite3.connect(DB_PATH) as conn:
    placeholders = ",".join("?" * len(stocks_list))
    df_pe = pd.read_sql_query(
        f"SELECT trade_date, ts_code, pe_ttm, pb FROM daily_basic "
        f"WHERE trade_date >= ? AND trade_date <= ? AND ts_code IN ({placeholders})",
        conn,
        params=[START_DATE, END_DATE] + stocks_list
    )
log(f"    pe_ttm: {len(df_pe)} 行, {df_pe['ts_code'].nunique()} 股, "
    f"有效PE: {df_pe['pe_ttm'].notna().sum()} ({df_pe['pe_ttm'].notna().sum()/len(df_pe)*100:.0f}%)")

# 2b: 加载 fina_indicator (roe_yoy)
log("  加载 fina_indicator (roe_yoy)...")
with sqlite3.connect(DB_PATH) as conn:
    df_roe = pd.read_sql_query(
        f"SELECT trade_date, ts_code, roe, roe_yoy FROM fina_indicator "
        f"WHERE ts_code IN ({placeholders})",
        conn,
        params=stocks_list
    )
log(f"    roe: {len(df_roe)} 行, {df_roe['ts_code'].nunique()} 股")

# 2c: 获取日线价格
log("  获取日线价格...")
stock_set = set(stocks_list)
all_rows = []
for i, d in enumerate(dates_list):
    if i % 50 == 0:
        log(f"    拉取日线: {d} ({i+1}/{len(dates_list)})")
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
log(f"    日线: {len(df_daily)} 行")

# 合并分钟因子
df = df_daily.merge(df_minute, on=["trade_date", "ts_code"], how="inner")
df = df.sort_values(["ts_code", "trade_date"])

# 合并 pe_ttm
df = df.merge(df_pe[["trade_date", "ts_code", "pe_ttm"]], on=["trade_date", "ts_code"], how="left")

# 合并 roe_yoy (前向填充——用最新已披露的财务数据)
df_roe = df_roe.sort_values(["ts_code", "trade_date"])
# 对每个 stock，把 roe_yoy forward-fill 到所有交易日
all_dates_df = pd.DataFrame([
    (d, c) for d in dates_list for c in stocks_list
], columns=["trade_date", "ts_code"])
df_roe_full = all_dates_df.merge(df_roe, on=["ts_code", "trade_date"], how="left")
df_roe_full = df_roe_full.sort_values(["ts_code", "trade_date"])
df_roe_full["roe_yoy"] = df_roe_full.groupby("ts_code")["roe_yoy"].ffill()
df = df.merge(df_roe_full[["trade_date", "ts_code", "roe_yoy"]],
              on=["trade_date", "ts_code"], how="left")

log(f"    合并后: {len(df)} 行, {df['ts_code'].nunique()} 股")

# 2d: 计算日级因子
log("  计算日级因子...")
df["return_1d"] = df.groupby("ts_code")["close"].pct_change()
df["momentum_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).mean()
)
df["volatility_20d"] = df.groupby("ts_code")["return_1d"].transform(
    lambda x: x.rolling(20, min_periods=10).std()
)

# 真实 PE_TTM rank（横截面排名，值越小=越便宜=越好）
# 注意：PE_TTM 为负（亏损公司）设为NaN，不参与排名
df["pe_ttm_clean"] = df["pe_ttm"].where(df["pe_ttm"] > 0, np.nan)
df["pe_ttm_rank"] = df.groupby("trade_date")["pe_ttm_clean"].transform(
    lambda x: x.rank(pct=True, na_option="bottom")
)

# 真实 ROE YoY rank（横截面排名，值越大=ROE改善越多=越好）
df["roe_yoy_rank_raw"] = df.groupby("trade_date")["roe_yoy"].transform(
    lambda x: x.rank(pct=True, na_option="bottom")
)

# 将 roe_yoy_rank 归一化到 [0,1]
df["roe_yoy_rank"] = df["roe_yoy_rank_raw"]

# 清洗
required_cols = ["roe_yoy_rank", "pe_ttm_rank", "momentum_20d", "volatility_20d"]
minute_cols = [c for c in ["intraday_reversal_5m", "open_effect_5m", "vwap_deviation_5m", "tail_volume_5m"] if c in df.columns]
df = df.dropna(subset=required_cols, how="any")
df[minute_cols] = df[minute_cols].fillna(0)

log(f"    清洗后: {len(df)} 行, {df['ts_code'].nunique()} 股, {df['trade_date'].nunique()} 天")
log(f"    耗时: {time.time()-t0:.1f}s")

# ============================================================
# Phase 3: Top 5 回测 + Proxy vs Real 对比回测
# ============================================================
log(f"\n{'='*60}")
log(f"Phase 3: Top 5 回测（真实财务数据）")
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

# ── 加载 proxy 版本结果用于对比 ──
PROXY_LOG = CACHE_DIR / f"top5_1year_{END_DATE}.log"
proxy_results = None
if PROXY_LOG.exists():
    # 从 log 中提取 proxy 结果
    proxy_results = [
        ("1.ROE+低波(ROE主导)", -0.757, -0.094, -0.191),
        ("2.ROE+低波", -0.086, -0.005, -0.154),
        ("3.ROE+PE+低波(ROE主导)", 0.269, 0.053, -0.178),
        ("4.ROE+动量+低波+4分钟(分钟主导)", -0.443, -0.090, -0.312),
        ("5.ROE+PE+动量+低波+VWAP偏离", -0.570, -0.121, -0.320),
    ]

print(f"\n{'='*90}")
print(f"🏆 Top 5 组合 — 近1年回测：Proxy vs 真实财务数据")
print(f"  日期: {dates_list[0]} ~ {dates_list[-1]} ({len(dates_list)} 交易日)")
print(f"  股票池: {len(stocks_list)} 只 Top 成交额 | 调仓: {REBALANCE_FREQ} | 持仓: {TOP_N} 只")
print(f"  🔑 真实数据: pe_ttm ← daily_basic, roe_yoy ← fina_indicator")
print(f"{'='*90}")

print(f"\n{'组合':<32s} {'真实Sharpe':>10s} {'真实收益':>9s} {'真实回撤':>9s}  "
      f"{'Proxy Sharpe':>12s} {'Proxy收益':>9s} {'ΔSharpe':>9s}")
print("-" * 100)

result_map = {r.name: r for r in results}
if proxy_results:
    for pname, psharpe, pret, pdd in proxy_results:
        r = result_map.get(pname)
        if r:
            delta = r.sharpe - psharpe
            dsign = "+" if delta > 0 else ""
            print(f"{pname:<32s} {r.sharpe:>10.3f} {r.total_return:>8.1%} "
                  f"{r.max_drawdown:>8.1%}  {psharpe:>12.3f} {pret:>8.1%} "
                  f"{dsign}{delta:>8.3f}")
        else:
            print(f"{pname:<32s} {'N/A':>10s} {'N/A':>9s} {'N/A':>9s}  "
                  f"{psharpe:>12.3f} {pret:>8.1%}  {'—':>9s}")

print(f"\n{'='*90}")
print(f"📊 真实财务数据回测详情")
print(f"{'='*90}")
print(f"{'Rank':<4} {'Sharpe':>7} {'Calmar':>7} {'收益':>9} {'回撤':>8} "
      f"{'胜率':>7} {'交易':>6} {'类型':>8} {'组合'}")
print("-" * 95)
for i, r in enumerate(results):
    print(f"{i+1:<4} {r.sharpe:>7.3f} {r.calmar:>7.3f} {r.total_return:>8.1%} "
          f"{r.max_drawdown:>7.1%} {r.win_rate:>6.1%} {r.n_trades:>6d} "
          f"{r.combo_type:>8s} {r.name}")

if results:
    best = results[0]
    print(f"\n🥇 最佳: {best.name}")
    print(f"   Sharpe: {best.sharpe:.4f}  |  收益: {best.total_return:.2%}  |  回撤: {best.max_drawdown:.2%}")

# ── 因子 IC 分析 ──
print(f"\n{'='*90}")
print(f"📈 因子 IC 对比：Proxy vs 真实数据（近1年 Spearman）")
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

ic_factors = [
    ("roe_yoy_rank", "ROE YoY (真实)"),
    ("pe_ttm_rank", "PE_TTM (真实)"),
    ("momentum_20d", "动量20日 (真实)"),
    ("volatility_20d", "波动率20日 (真实)"),
    ("intraday_reversal_5m", "日内反转5min"),
    ("open_effect_5m", "开盘效应5min"),
    ("vwap_deviation_5m", "VWAP偏离5min"),
    ("tail_volume_5m", "尾盘放量5min"),
]

print(f"{'Factor':<28s} {'IC Mean':>8s} {'IC Std':>8s} {'ICIR':>8s} {'Win':>7s} {'Rating'}")
print("-" * 75)

for fn, label in ic_factors:
    if fn not in df.columns:
        continue
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
        print(f"  {label:<26s}  {'—':>8s}  {'—':>8s}  {'—':>8s}  {'—':>7s}  数据不足")
        continue

    ic_mean = np.mean(ic_list)
    ic_std = np.std(ic_list, ddof=1)
    icir = ic_mean / ic_std if ic_std > 0 else 0
    wr = sum(1 for x in ic_list if x > 0) / len(ic_list)
    rating = "⭐⭐⭐" if abs(icir) >= 0.5 else ("⭐⭐" if abs(icir) >= 0.25 else ("⭐" if abs(icir) >= 0.1 else "❌"))
    print(f"  {label:<26s}  {ic_mean:>+8.4f}  {ic_std:>8.4f}  {icir:>+8.3f}  {wr:>6.1%}  {rating}")

print(f"\n✅ 完成")

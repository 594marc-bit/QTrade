#!/usr/bin/env python3
"""
分钟级因子 IC 分析脚本 (v2 — 批量优化版)

用全市场日线 API 获取历史数据，避免逐个股票拉取。
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data import qmt_fetcher
from src.factors.minute_factors import (
    OpenEffectFactor,
    VWAPDeviationFactor,
    TailVolumeFactor,
    IntradayReversalFactor,
    VolumeConcentrationFactor,
    AMVolumeRatioFactor,
    _clear_cache,
)

# ============================================================
# 配置
# ============================================================
N_STOCKS = 50
N_DAYS = 30
END_DATE = "20260722"

print("=" * 60)
print("分钟级因子 IC 分析 (v2)")
print(f" 样本: Top {N_STOCKS} × {N_DAYS} 天")
print("=" * 60)

# ============================================================
# Step 1: 批量获取全市场日线 (最近 N_DAYS 天)
# ============================================================
print(f"\n[1/3] 批量获取全市场日线 ({N_DAYS} 天)...")
t0 = time.time()

# 获取交易日历
td = qmt_fetcher._get_json("/api/trade_dates", {
    "start_date": "20260501",
    "end_date": END_DATE,
})
all_dates = sorted([d for d in td.get("trade_dates", []) if d <= END_DATE])
analysis_dates = all_dates[-N_DAYS:]
print(f"  日期: {analysis_dates[0]} ~ {analysis_dates[-1]}")

# 批量获取每个交易日的全市场日线
daily_data = {}  # date -> DataFrame
for i, d in enumerate(analysis_dates):
    df = qmt_fetcher.fetch_all_stocks_for_date(d)
    if not df.empty:
        daily_data[d] = df
    if (i + 1) % 10 == 0:
        print(f"  已获取 {i+1}/{len(analysis_dates)} 天")

print(f"  有效天数: {len(daily_data)}/{len(analysis_dates)}")
print(f"  耗时: {time.time()-t0:.1f}s")

# ============================================================
# Step 2: 选 Top N 股票 + 构造因子计算 DataFrame
# ============================================================
print(f"\n[2/3] 选股 + 计算因子...")
t0 = time.time()

# 用最后一天的数据选 Top N 成交活跃股
last_df = daily_data.get(END_DATE)
if last_df is None:
    last_df = daily_data.get(sorted(daily_data.keys())[-1])

last_df = last_df.dropna(subset=["amount"])
top_stocks = last_df.nlargest(N_STOCKS, "amount")["ts_code"].tolist()
print(f"  Top {N_STOCKS}: {top_stocks[0]} ~ {top_stocks[-1]}")

# 构造因子计算用的 DataFrame (需要 trade_date + ts_code + close)
# close 从 daily_data 里补
rows = []
all_ts_set = set(top_stocks)
for d, df_day in daily_data.items():
    day_stocks = df_day[df_day["ts_code"].isin(all_ts_set)]
    for _, row in day_stocks.iterrows():
        rows.append({
            "trade_date": d,
            "ts_code": row["ts_code"],
            "close": row["close"],
        })
df_input = pd.DataFrame(rows)
print(f"  构造 {len(df_input)} 行数据")

# 计算次日收益（先算好，因子计算时可以复用）
df_input["next_return"] = np.nan
for ts_code in top_stocks:
    stock_rows = df_input[df_input["ts_code"] == ts_code].sort_values("trade_date")
    closes = stock_rows[["trade_date", "close"]].set_index("trade_date")["close"]
    for i in range(len(closes) - 1):
        curr_date = closes.index[i]
        next_date = closes.index[i + 1]
        if closes.iloc[i] > 0:
            ret = closes.iloc[i + 1] / closes.iloc[i] - 1
            df_input.loc[
                (df_input["ts_code"] == ts_code) & (df_input["trade_date"] == curr_date),
                "next_return",
            ] = ret

# 计算所有因子
factors = [
    OpenEffectFactor(window=5),
    VWAPDeviationFactor(window=5),
    TailVolumeFactor(window=5),
    IntradayReversalFactor(window=5),
    VolumeConcentrationFactor(window=5),
    AMVolumeRatioFactor(window=5),
]

for f in factors:
    t1 = time.time()
    df_input = f.calculate(df_input)
    elapsed = time.time() - t1
    non_null = df_input[f.factor_name].notna().sum()
    print(f"  {f.factor_name:30s}  valid={non_null:4d}/{len(df_input)}  [{elapsed:.1f}s]")

_clear_cache()
print(f"  总耗时: {time.time()-t0:.1f}s")

# ============================================================
# Step 3: IC 分析
# ============================================================
print(f"\n[3/3] Spearman IC 分析...\n")

valid_dates = sorted(daily_data.keys())
print(f"  分析日期: {len(valid_dates)} 天\n")

print(f"{'Factor':<30s} {'IC Mean':>8s} {'IC Std':>8s} {'ICIR':>8s} {'Win Rate':>8s} {'Rating':>8s}")
print("-" * 78)

results = []
for f in factors:
    name = f.factor_name
    ic_list = []

    for d in valid_dates:
        day_data = df_input[
            (df_input["trade_date"] == d) &
            df_input[name].notna() &
            df_input["next_return"].notna()
        ]
        if len(day_data) < 10:
            continue

        f_vals = day_data[name].values
        lower, upper = np.percentile(f_vals, [1, 99])
        mask = (f_vals >= lower) & (f_vals <= upper)
        f_vals_clean = f_vals[mask]
        ret_vals_clean = day_data["next_return"].values[mask]

        if len(f_vals_clean) < 10:
            continue

        try:
            ic, _ = spearmanr(f_vals_clean, ret_vals_clean, nan_policy="omit")
            if not np.isnan(ic):
                ic_list.append(ic)
        except Exception:
            pass

    if len(ic_list) < 5:
        print(f"  {name:<28s}  {'—':>8s}  {'—':>8s}  {'—':>8s}  {'—':>8s}  样本不足")
        continue

    ic_mean = np.mean(ic_list)
    ic_std = np.std(ic_list, ddof=1)
    icir = ic_mean / ic_std if ic_std > 0 else 0
    win_rate = sum(1 for x in ic_list if x > 0) / len(ic_list)

    if abs(icir) >= 0.5 and win_rate >= 0.55:
        rating = "⭐⭐⭐ 有效"
    elif abs(icir) >= 0.25 and win_rate >= 0.52:
        rating = "⭐⭐ 可关注"
    elif abs(icir) >= 0.1:
        rating = "⭐ 弱信号"
    else:
        rating = "❌ 无效"

    print(f"  {name:<28s}  {ic_mean:>+8.4f}  {ic_std:>8.4f}  {icir:>+8.3f}  {win_rate:>7.1%}  {rating}")
    results.append((name, ic_mean, ic_std, icir, win_rate, len(ic_list)))

# 汇总
print("\n" + "=" * 60)
print("ICIR 排名 (绝对值):")
results.sort(key=lambda x: abs(x[3]), reverse=True)
for name, ic_m, ic_s, icir, wr, ndays in results:
    direction = "正向" if ic_m > 0 else "反向"
    print(f"  {name:<30s} IC={ic_m:+.4f}  ICIR={icir:+.3f}  win={wr:.0%}  ({ndays}d) [{direction}]")

print("\n✅ 完成")

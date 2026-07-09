#!/usr/bin/env python3
"""Focused weight grid search — tests ~25 combos around IC-proportional ratios.

Usage:
    python3.14 grid_focused.py
"""

import os, sys
from pathlib import Path
from itertools import product

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"]:
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from src.data.fetcher import get_index_constituents, get_index_daily
from src.data.cleaner import clean_pipeline
from src.data.storage import (load_daily_price, load_daily_basic, merge_fundamentals,
    load_fina_indicator, merge_fina_indicator)
from src.factors.scorer import standardize_factors, _factor_to_score_col
from src.backtest.engine import BacktestEngine

import src.factors.rsi; import src.factors.return_20d; import src.factors.roe_change
from src.factors.base import get_registered_factors

INDEX = "000852"
START = "20250101"  # shorter period for speed
END = "20260611"
TOP_N = 10
CAPITAL = 1_000_000
FREQ = "M"
ALL_FACTORS = ["roe_yoy_rank", "return_20d", "rsi_14d"]

# Focused grid around IC-proportional ratios (roe ~0.39, return ~0.36, rsi ~0.25)
# Test ROE: 0.30-0.55, Return: -0.25 to -0.45, RSI: -0.15 to -0.35
# = 5 × 5 × 5 = 125 combos... still lot. Let me narrow to key ratios.
# Key insight: the ratio between factors matters, not absolute scale.
# We'll vary: ROE/(Return+RSI) ratio and Return/RSI ratio.

# Approach: pick meaningful combos directly
GRID = [
    # Baseline
    ( 0.50, -0.25, -0.25),
    # IC-proportional
    ( 0.39, -0.36, -0.25),
    ( 0.40, -0.35, -0.25),
    ( 0.40, -0.30, -0.30),
    ( 0.35, -0.40, -0.25),
    ( 0.35, -0.35, -0.30),
    ( 0.35, -0.33, -0.32),
    ( 0.45, -0.30, -0.25),
    ( 0.45, -0.28, -0.27),
    ( 0.45, -0.35, -0.20),
    ( 0.30, -0.45, -0.25),
    ( 0.30, -0.40, -0.30),
    ( 0.30, -0.35, -0.35),
    ( 0.55, -0.25, -0.20),
    ( 0.55, -0.30, -0.15),
    ( 0.50, -0.30, -0.20),
    ( 0.50, -0.35, -0.15),
    ( 0.40, -0.40, -0.20),
    # More extreme for comparison
    ( 0.60, -0.20, -0.20),
    ( 0.25, -0.50, -0.25),
    ( 0.33, -0.44, -0.22),
]

def main():
    print(f"聚焦权重搜索: {len(GRID)} 组合", flush=True)

    # Load
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()
    df = load_daily_price(start_date=START, end_date=END)
    df = df[df["ts_code"].isin(ts_codes)]
    df, _ = clean_pipeline(df)
    basic_df = load_daily_basic(start_date=START, end_date=END)
    if not basic_df.empty: df = merge_fundamentals(df, basic_df)
    fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START, end_date=END)
    if not fina_df.empty: df = merge_fina_indicator(df, fina_df)

    factors = get_registered_factors()
    factor_cols = []
    for name, cls in factors.items():
        if name in ALL_FACTORS:
            factor = cls()
            df = factor.calculate(df)
            factor_cols.append(factor.factor_name)
    df = standardize_factors(df, factor_cols)
    factor_map = {f: _factor_to_score_col(f) for f in factor_cols}
    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START, end_date=END)

    print(f"数据就绪: {len(df)}行 {df['ts_code'].nunique()}只", flush=True)

    results = []
    for i, (roe, ret, rsi) in enumerate(GRID):
        weights = {
            factor_map["roe_yoy_rank"]: roe,
            factor_map["return_20d"]: ret,
            factor_map["rsi_14d"]: rsi,
        }
        df_run = df.copy()
        df_run["total_score"] = 0.0
        for sc, w in weights.items():
            if sc in df_run.columns:
                df_run["total_score"] += df_run[sc].fillna(0) * w

        engine = BacktestEngine(initial_capital=CAPITAL, top_n=TOP_N, rebalance_freq=FREQ)
        bt = engine.run(df_run, benchmark_df=benchmark_df)
        m = bt.metrics
        results.append({
            "roe": roe, "ret": ret, "rsi": rsi,
            "annual": m["annual_return"], "dd": m["max_drawdown"],
            "sharpe": m["sharpe_ratio"], "calmar": m["calmar_ratio"],
            "win_rate": m["win_rate"], "final_nav": m["final_nav"],
        })
        print(f"  [{i+1}/{len(GRID)}] roe={roe:+.2f} ret={ret:+.2f} rsi={rsi:+.2f} → 年化={m['annual_return']:.2%} 夏普={m['sharpe_ratio']:.3f}", flush=True)

    # Sort & display
    df_r = pd.DataFrame(results).sort_values("sharpe", ascending=False)

    print("\n" + "="*80)
    print(f"  🏆 权重优化结果 ({START}~{END}, {INDEX})")
    print("="*80)
    print(f"{'#':<4} {'ROE':>7} {'Return':>8} {'RSI':>8} {'年化':>9} {'回撤':>9} {'夏普':>7} {'Calmar':>7}")
    print("-"*80)
    for idx, (_, row) in enumerate(df_r.iterrows()):
        marker = "← 基线" if row["roe"] == 0.50 and row["ret"] == -0.25 and row["rsi"] == -0.25 else ""
        print(f"{idx+1:<4} {row['roe']:>+7.2f} {row['ret']:>+8.2f} {row['rsi']:>+8.2f} "
              f"{row['annual']:>9.2%} {row['dd']:>9.2%} {row['sharpe']:>7.3f} {row['calmar']:>7.3f}  {marker}")

    best = df_r.iloc[0]
    print(f"\n🎯 最优: roe_yoy_rank={best['roe']:+.2f}, return_20d={best['ret']:+.2f}, rsi_14d={best['rsi']:+.2f}")
    print(f"   年化={best['annual']:.2%} 夏普={best['sharpe']:.3f} 回撤={best['dd']:.2%} 胜率={best['win_rate']:.1%}")

if __name__ == "__main__":
    main()

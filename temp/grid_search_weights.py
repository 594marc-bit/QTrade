#!/usr/bin/env python3
"""Grid search for optimal factor weights on Plan A (精简反转版0708).

Tests weight combinations for 3 factors, ranks by Sharpe ratio.

Usage:
    python3.14 grid_search_weights.py > grid_results.txt
"""

import os
import sys
from pathlib import Path
from itertools import product

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from src.config import DATA_DIR
from src.data.fetcher import get_index_constituents, get_index_daily
from src.data.cleaner import clean_pipeline
from src.data.storage import (
    load_daily_price, load_daily_basic, merge_fundamentals,
    load_fina_indicator, save_fina_indicator, merge_fina_indicator,
)
from src.factors.scorer import standardize_factors, compute_total_score, select_top_n, _factor_to_score_col
from src.backtest.engine import BacktestEngine

# Import to register factors
import src.factors.rsi
import src.factors.return_20d
import src.factors.roe_change
from src.factors.base import get_registered_factors

# ── Configuration ──
INDEX = "000852"
START_DATE = "20230101"
END_DATE = "20260611"
TOP_N = 10
INITIAL_CAPITAL = 1_000_000
REBALANCE_FREQ = "M"

ALL_FACTORS = ["roe_yoy_rank", "return_20d", "rsi_14d"]

# ── Weight grid ──
# ROE: 0.30 ~ 0.55 (positive, anchors on quality)
# Return: -0.20 ~ -0.45 (negative, reversal signal)
# RSI: -0.15 ~ -0.35 (negative, oversold signal)
ROE_WEIGHTS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
RETURN_WEIGHTS = [-0.20, -0.25, -0.30, -0.35, -0.40, -0.45]
RSI_WEIGHTS = [-0.15, -0.20, -0.25, -0.30, -0.35]

# Total: 6 * 6 * 5 = 180 combos, each backtest ~0.5s = ~90s total


def main():
    print("=" * 60)
    print("  QTrade — 因子权重网格搜索")
    print(f"  因子: {ALL_FACTORS}")
    print(f"  组合数: {len(ROE_WEIGHTS)} × {len(RETURN_WEIGHTS)} × {len(RSI_WEIGHTS)} = {len(ROE_WEIGHTS)*len(RETURN_WEIGHTS)*len(RSI_WEIGHTS)}")
    print("=" * 60)

    # Load data once
    print("\n[1/3] 加载数据 & 计算因子...")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    df, report = clean_pipeline(df)

    basic_df = load_daily_basic(start_date=START_DATE, end_date=END_DATE)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START_DATE, end_date=END_DATE)
    if not fina_df.empty:
        df = merge_fina_indicator(df, fina_df)

    # Calculate factors
    factors = get_registered_factors()
    factor_cols = []
    for name, cls in factors.items():
        if name in ALL_FACTORS:
            factor = cls()
            df = factor.calculate(df)
            factor_cols.append(factor.factor_name)

    df = standardize_factors(df, factor_cols)

    # Factor mapping
    factor_cols_map = {f: _factor_to_score_col(f) for f in factor_cols}
    print(f"  因子->评分: {factor_cols_map}", flush=True)

    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)

    # Grid search
    total = len(ROE_WEIGHTS) * len(RETURN_WEIGHTS) * len(RSI_WEIGHTS)
    print(f"\n[2/3] 开始网格搜索 ({total} 组合)...", flush=True)
    results = []

    count = 0

    for roe_w, ret_w, rsi_w in product(ROE_WEIGHTS, RETURN_WEIGHTS, RSI_WEIGHTS):
        count += 1
        weights = {
            factor_cols_map["roe_yoy_rank"]: roe_w,
            factor_cols_map["return_20d"]: ret_w,
            factor_cols_map["rsi_14d"]: rsi_w,
        }

        # Score
        df_run = df.copy()
        df_run["total_score"] = 0.0
        for sc, w in weights.items():
            if sc in df_run.columns:
                df_run["total_score"] += df_run[sc].fillna(0) * w

        # Backtest
        engine = BacktestEngine(
            initial_capital=INITIAL_CAPITAL,
            top_n=TOP_N,
            rebalance_freq=REBALANCE_FREQ,
        )
        bt_result = engine.run(df_run, benchmark_df=benchmark_df)

        metrics = bt_result.metrics
        results.append({
            "roe_w": roe_w,
            "ret_w": ret_w,
            "rsi_w": rsi_w,
            "annual_return": metrics["annual_return"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe": metrics["sharpe_ratio"],
            "calmar": metrics["calmar_ratio"],
            "win_rate": metrics["win_rate"],
            "final_value": metrics["final_nav"],
        })

        if count % 20 == 0 or count == 1:
            print(f"  进度: {count}/{total}", flush=True)

    # Sort & display
    print(f"\n[3/3] 结果排序...")
    df_results = pd.DataFrame(results)

    # Sort by Sharpe
    df_results = df_results.sort_values("sharpe", ascending=False)

    print("\n" + "=" * 80)
    print("  🏆 Top 20 by Sharpe Ratio")
    print("=" * 80)
    print(f"{'Rank':<5} {'ROE':>6} {'Return':>8} {'RSI':>8} {'年化':>8} {'最大回撤':>10} {'夏普':>7} {'Calmar':>7} {'胜率':>7}")
    print("-" * 80)

    for i, row in df_results.head(20).iterrows():
        idx = df_results.index.get_loc(i) + 1
        print(
            f"{idx:<5} "
            f"{row['roe_w']:>+6.2f} "
            f"{row['ret_w']:>+8.2f} "
            f"{row['rsi_w']:>+8.2f} "
            f"{row['annual_return']:>8.2%} "
            f"{row['max_drawdown']:>10.2%} "
            f"{row['sharpe']:>7.3f} "
            f"{row['calmar']:>7.3f} "
            f"{row['win_rate']:>6.1%}"
        )

    # Also show current baseline
    baseline = df_results[(df_results["roe_w"] == 0.50) & (df_results["ret_w"] == -0.25) & (df_results["rsi_w"] == -0.25)]
    if not baseline.empty:
        b = baseline.iloc[0]
        print("\n  📍 当前方案A权重 (基线):")
        print(f"     roe={b['roe_w']:+.2f} return={b['ret_w']:+.2f} rsi={b['rsi_w']:+.2f} "
              f"→ 年化={b['annual_return']:.2%} 夏普={b['sharpe']:.3f} 回撤={b['max_drawdown']:.2%}")

    # Best overall
    best = df_results.iloc[0]
    print(f"\n  🎯 最优权重:")
    print(f"     roe_yoy_rank: {best['roe_w']:+.2f}")
    print(f"     return_20d:   {best['ret_w']:+.2f}")
    print(f"     rsi_14d:      {best['rsi_w']:+.2f}")
    print(f"     → 年化={best['annual_return']:.2%} 夏普={best['sharpe']:.3f} "
          f"回撤={best['max_drawdown']:.2%} 胜率={best['win_rate']:.1%}")

    # Save full results
    save_path = DATA_DIR / "weight_grid_search.csv"
    df_results.to_csv(save_path, index=False)
    print(f"\n  完整结果已保存: {save_path}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

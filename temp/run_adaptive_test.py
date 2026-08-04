#!/usr/bin/env python3
"""Run an adaptive-weights backtest: weights adjust based on rolling IC.

Unlike the wizard pipeline which uses uniform weights, this script:
1. Computes IC per factor on each date
2. Uses trailing 60-day IC to adjust weights dynamically
3. Re-scores and backtests with time-varying weights (no look-ahead bias)

Usage:
    python3.14 run_adaptive_test.py
"""

import os
import sys
from pathlib import Path

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
from src.factors.ic_analyzer import compute_future_return, evaluate_factor
from src.backtest.engine import BacktestEngine
from src.visualization.backtest_charts import generate_backtest_charts

# Import to register factors
import src.factors.rsi
import src.factors.return_20d
import src.factors.roe_change
from src.factors.base import get_registered_factors

# ── Configuration ──
INDEX = "000852"  # CSI 1000
START_DATE = "20230101"
END_DATE = "20260611"
TOP_N = 10
INITIAL_CAPITAL = 1_000_000
REBALANCE_FREQ = "M"
IC_WINDOW = 5
ADAPTIVE_WINDOW = 60  # trailing days for IC averaging

# ── Factors ──
ALL_FACTORS = ["roe_yoy_rank", "return_20d", "rsi_14d"]

# ── Base weights (sign only matters for direction) ──
BASE_WEIGHTS = {
    "roe_yoy_rank_score": 0.50,
    "return_score": -0.25,
    "rsi_score": -0.25,
}


def compute_daily_ic_series(df, factor_cols, return_col, min_window=60):
    """Compute rolling daily IC series for each factor.

    Returns DataFrame with trade_date index and factor columns = rolling IC values.
    Uses expanding window (first min_window days) then rolling window.
    """
    dates = sorted(df["trade_date"].unique())
    ic_data = {col: {} for col in factor_cols}

    for i, date in enumerate(dates):
        # Use expanding window up to min_window, then rolling ADAPTIVE_WINDOW
        if i < min_window:
            # Not enough data yet — skip
            continue

        window_start_idx = max(0, i - ADAPTIVE_WINDOW)
        window_dates = dates[window_start_idx:i]

        train_df = df[df["trade_date"].isin(window_dates)]

        for col in factor_cols:
            valid = train_df[[col, return_col]].dropna()
            if len(valid) < 30:
                ic_data[col][date] = np.nan
                continue
            ic_val = valid[col].corr(valid[return_col], method="spearman")
            ic_data[col][date] = ic_val

    ic_df = pd.DataFrame(ic_data)
    ic_df.index.name = "trade_date"
    return ic_df


def compute_adaptive_weights_at_date(date, ic_df, base_weights):
    """Compute adaptive weights for a single date based on trailing IC."""
    # Find the latest IC row before or at this date
    available = ic_df[ic_df.index <= date]
    if len(available) == 0:
        return base_weights

    latest_ic = available.iloc[-1]
    if latest_ic.isna().any():
        return base_weights

    # Map factor -> score col using standard naming
    adaptive = {}
    for factor_col, ic_val in latest_ic.items():
        score_col = _factor_to_score_col(factor_col)
        # Preserve sign from base_weights
        base_sign = -1.0 if base_weights.get(score_col, 0) < 0 else 1.0
        adaptive[score_col] = base_sign * abs(ic_val)

    total = sum(abs(v) for v in adaptive.values())
    if total == 0:
        return base_weights

    return {k: v / total for k, v in adaptive.items()}


def main():
    print("=" * 60)
    print("  QTrade — 自适应权重回测 (IC-Adaptive)")  
    print(f"  滚动窗口: {ADAPTIVE_WINDOW}天")
    print("=" * 60)

    # Step 1: Load data
    print(f"\n[1/8] 加载数据: {INDEX} {START_DATE} ~ {END_DATE}")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()
    print(f"  {INDEX} 成分股: {len(ts_codes)} 只")

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    print(f"  加载行情数据: {len(df)} 行, {df['ts_code'].nunique()} 只")

    df, report = clean_pipeline(df)
    print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只")

    # Step 2: Merge fundamentals
    print("\n[2/8] 合并基本面数据...")
    basic_df = load_daily_basic(start_date=START_DATE, end_date=END_DATE)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START_DATE, end_date=END_DATE)
    if fina_df.empty:
        from src.data.tushare_fetcher import fetch_fina_indicator
        print("  从 Tushare 获取 ROE 数据...")
        fina_df = fetch_fina_indicator(ts_codes, start_date=START_DATE, end_date=END_DATE)
        if not fina_df.empty:
            save_fina_indicator(fina_df)
            fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START_DATE, end_date=END_DATE)
    if not fina_df.empty:
        df = merge_fina_indicator(df, fina_df)
        print("  合并ROE数据完成")

    # Step 3: Calculate factors
    print("\n[3/8] 计算因子...")
    factors = get_registered_factors()
    factor_cols = []
    for name, cls in factors.items():
        if name in ALL_FACTORS:
            factor = cls()
            df = factor.calculate(df)
            factor_cols.append(factor.factor_name)
            print(f"  ✓ {factor.factor_name}")

    # Step 4: Standardize
    print("\n[4/8] 标准化因子...")
    df = standardize_factors(df, factor_cols)

    # Step 5: Compute future returns & IC series
    print(f"\n[5/8] 计算未来收益 & 滚动 IC (窗口={ADAPTIVE_WINDOW}天)...")
    df = compute_future_return(df, n_days=IC_WINDOW)
    return_col = f"future_return_{IC_WINDOW}d"

    ic_df = compute_daily_ic_series(df, factor_cols, return_col)
    print(f"  已计算 {len(ic_df)} 个交易日的滚动IC")

    # IC stats (full sample)
    print(f"\n  IC 分析 (窗口={IC_WINDOW}日):")
    for name in ALL_FACTORS:
        if name not in df.columns:
            continue
        result = evaluate_factor(df, name, return_col)
        s = result["summary"]
        verdict = result["verdict"]
        direction = "正" if s["ic_direction"] == 1 else ("负" if s["ic_direction"] == -1 else "—")
        print(
            f"  {name:20s}  "
            f"IC均值: {s['ic_mean']:+.4f}  "
            f"ICIR: {s['icir']:+.4f}  "
            f"胜率: {s['win_rate']:.1%}  "
            f"方向: {direction}  "
            f"→ {verdict}"
        )

    # Step 6: Score with adaptive weights (per-date)
    print("\n[6/8] 自适应权重打分 (Look-Ahead Safe)...")
    df["total_score"] = 0.0
    dates = sorted(df["trade_date"].unique())

    weight_history = {}  # track weights per date for analysis
    for date in dates:
        weights = compute_adaptive_weights_at_date(date, ic_df, BASE_WEIGHTS)
        weight_history[date] = weights

        mask = df["trade_date"] == date
        for score_col, weight in weights.items():
            if score_col in df.columns:
                df.loc[mask, "total_score"] += (
                    df.loc[mask, score_col].fillna(0) * weight
                )

    # Show weight evolution
    print(f"  权重变化（首/中/末）:")
    sample_dates = [dates[0], dates[len(dates)//2], dates[-1]]
    for d in sample_dates:
        w = weight_history.get(d, {})
        w_str = ", ".join(f"{k}:{v:+.3f}" for k, v in sorted(w.items()))
        print(f"    {d}: {w_str}")

    latest_date = dates[-1]
    top_picks = select_top_n(df, latest_date, n=TOP_N)
    print(f"\n  {latest_date} Top {TOP_N}:")
    for _, row in top_picks.iterrows():
        print(f"    {row['ts_code']}  得分: {row['total_score']:.2f}")

    # Step 7: Backtest
    print(f"\n[7/8] 回测 (调仓: {REBALANCE_FREQ}, 持仓: {TOP_N}只)...")
    engine = BacktestEngine(
        initial_capital=INITIAL_CAPITAL,
        top_n=TOP_N,
        rebalance_freq=REBALANCE_FREQ,
    )
    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)
    bt_result = engine.run(df, benchmark_df=benchmark_df)
    print(bt_result.summary())

    # Show final weights
    print(f"\n  最终自适应权重: {weight_history.get(dates[-1], 'N/A')}")

    # Step 8: Charts
    print("\n[8/8] 生成图表...")
    generate_backtest_charts(bt_result)

    print("\n" + "=" * 60)
    print("  完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()

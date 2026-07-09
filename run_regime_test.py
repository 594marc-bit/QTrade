#!/usr/bin/env python3
"""Run a regime-adaptive backtest: applies different factor weights based on
market regime (trend vs mean-revert).

Usage:
    python3.14 run_regime_test.py
"""

import os
import sys
from pathlib import Path

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

sys.path.insert(0, str(Path(__file__).parent))

from src.config import DATA_DIR
from src.data.fetcher import get_index_constituents, get_index_daily, fetch_daily_basic
from src.data.cleaner import clean_pipeline
from src.data.storage import (
    load_daily_price, load_daily_basic, merge_fundamentals, save_daily_price,
)
from src.factors.scorer import standardize_factors, compute_total_score, select_top_n
from src.factors.ic_analyzer import compute_future_return, evaluate_factor
from src.factors.regime_detector import add_regime_column
from src.backtest.engine import BacktestEngine
from src.visualization.backtest_charts import generate_backtest_charts

# Import to register factors
import src.factors.rsi
import src.factors.return_20d
import src.factors.roe_change
import src.factors.momentum
import src.factors.trend_60d
import src.factors.valuation
import src.factors.volatility
from src.factors.base import get_registered_factors

# ── Configuration ──
INDEX = "000852"  # CSI 1000
START_DATE = "20230101"
END_DATE = "20260611"
TOP_N = 10
INITIAL_CAPITAL = 1_000_000
REBALANCE_FREQ = "M"
IC_WINDOW = 5

# ── Factors for both regimes ──
# These are the union of all factors used in either regime
ALL_FACTORS = [
    "roe_yoy_rank",
    "return_20d",
    "rsi_14d",
    "momentum_20d",
    "trend_60d",
]

# ── Regime-dependent weight sets ──
REGIME_WEIGHTS = {
    "trend": {
        # In uptrend: ride momentum, anchor on quality, light reversal
        "roe_yoy_rank_score": 0.30,
        "momentum_score": 0.30,
        "trend_score": 0.20,
        "return_score": 0.10,
        "rsi_score": 0.10,
    },
    "mean_revert": {
        # In downtrend/ranging: heavy reversal, anchor on quality
        "roe_yoy_rank_score": 0.40,
        "return_score": -0.30,
        "rsi_score": -0.30,
    },
}

# ── Default weights (for dates without regime data) ──
DEFAULT_WEIGHTS = {
    "roe_yoy_rank_score": 0.50,
    "return_score": -0.25,
    "rsi_score": -0.25,
}


def main():
    print("=" * 60)
    print("  QTrade — 市场状态自适应回测 (Regime-Adaptive)")
    print("=" * 60)

    # Step 1: Load data
    print(f"\n[1/8] 加载数据: {INDEX} {START_DATE} ~ {END_DATE}")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()
    print(f"  {INDEX} 成分股: {len(ts_codes)} 只")

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    print(f"  加载行情数据: {len(df)} 行, {df['ts_code'].nunique()} 只")

    # Clean data (remove low-quality stocks)
    df, report = clean_pipeline(df)
    print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只")

    # Step 2: Merge fundamentals
    print("\n[2/8] 合并基本面数据...")
    basic_df = load_daily_basic(start_date=START_DATE, end_date=END_DATE)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    # Fetch ROE data for roe_yoy_rank factor
    from src.data.storage import load_fina_indicator, save_fina_indicator, merge_fina_indicator
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
        print(f"  合并ROE数据完成")

    # Step 3: Calculate factors
    print("\n[3/8] 计算因子...")
    factors = get_registered_factors()
    factor_cols = []
    for name, cls in factors.items():
        if name in ALL_FACTORS:
            factor = cls()
            df = factor.calculate(df)
            factor_cols.append(factor.factor_name)
            print(f"  ✓ {factor.factor_name}: {factor.description}")

    # Step 4: Detect regime
    print("\n[4/8] 检测市场状态...")
    index_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)
    df = add_regime_column(df, index_df)

    # Show regime distribution
    regime_counts = df.groupby("trade_date")["regime"].first().value_counts()
    print(f"  趋势市: {regime_counts.get('trend', 0)} 天")
    print(f"  反转市: {regime_counts.get('mean_revert', 0)} 天")

    # Step 5: Standardize & score
    print("\n[5/8] 标准化 & 状态自适应打分...")
    df = standardize_factors(df, factor_cols)
    df = compute_total_score(
        df,
        weights=DEFAULT_WEIGHTS,
        regime_weights=REGIME_WEIGHTS,
    )

    # Show latest top picks
    latest_date = df["trade_date"].max()
    top_picks = select_top_n(df, latest_date, n=TOP_N)
    print(f"\n  {latest_date} Top {TOP_N}:")
    for _, row in top_picks.iterrows():
        regime = df[(df["trade_date"] == latest_date) & (df["ts_code"] == row["ts_code"])]["regime"].values
        regime_label = regime[0] if len(regime) > 0 else "?"
        print(f"    {row['ts_code']}  得分: {row['total_score']:.2f}  [{regime_label}]")

    # Step 6: IC analysis
    print(f"\n[6/8] IC 分析 (窗口={IC_WINDOW}日)...")
    df = compute_future_return(df, n_days=IC_WINDOW)
    return_col = f"future_return_{IC_WINDOW}d"

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

    # Step 8: Charts
    print("\n[8/8] 生成图表...")
    result_dir = DATA_DIR / "results" / "regime_adaptive"
    result_dir.mkdir(parents=True, exist_ok=True)
    generate_backtest_charts(bt_result)

    print("\n" + "=" * 60)
    print("  完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()

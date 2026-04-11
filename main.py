"""QTrade main entry point: data pipeline → factor calculation → IC analysis → backtest → report."""

import os
import sys
from pathlib import Path

# Disable system proxy before any imports that use requests
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from src.config import START_DATE, END_DATE, DATA_DIR
from src.config import (
    ADAPTIVE_WEIGHTS_ENABLED, ADAPTIVE_WEIGHTS_IC_WINDOW,
    INDUSTRY_NEUTRAL_ENABLED, INDUSTRY_NEUTRAL_MAX_PCT,
    RISK_CONTROL_ENABLED, RISK_CONTROL_STOP_LOSS, RISK_CONTROL_TAKE_PROFIT,
    RISK_CONTROL_MAX_DRAWDOWN_STOP, RISK_CONTROL_COOLDOWN_DAYS,
    POSITION_SIZING_METHOD, POSITION_SIZING_MIN_WEIGHT, POSITION_SIZING_MAX_WEIGHT,
)
from src.data.fetcher import get_index_constituents, sync_stocks_data, get_index_daily, fetch_daily_basic
from src.data.cleaner import clean_pipeline
from src.data.industry import get_industry_map
from src.data.storage import (
    save_daily_price, load_daily_price,
    save_daily_basic, load_daily_basic, merge_fundamentals,
)
from src.factors.base import get_registered_factors
from src.factors.scorer import standardize_factors, compute_total_score, select_top_n
from src.factors.ic_analyzer import compute_future_return, evaluate_factor
from src.factors.adaptive_weights import compute_adaptive_weights
from src.factors.industry_neutral import apply_industry_constraint
from src.backtest import BacktestEngine
from src.visualization.charts import generate_all_charts
from src.visualization.backtest_charts import generate_backtest_charts

# Import to trigger registration
import src.factors.momentum
import src.factors.volume
import src.factors.volatility
import src.factors.rsi
import src.factors.ma_deviation
import src.factors.turnover
import src.factors.intraday_range
import src.factors.valuation
import src.factors.return_20d
import src.factors.trend_60d


def run_pipeline(
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    ic_window: int = 5,
    top_n: int = 10,
    rebalance_freq: str = "M",
    initial_capital: float = 1_000_000,
):
    """Run the full pipeline: fetch -> clean -> factor -> IC -> backtest -> report."""

    print("=" * 60)
    print("  QTrade 量化交易系统 — 数据管道 & 因子分析 & 回测 & 可视化")
    print("=" * 60)

    # Step 1: Fetch data
    print("\n[1/10] 获取沪深300成分股列表...")
    constituents = get_index_constituents()
    ts_codes = constituents["ts_code"].tolist()
    print(f"  共 {len(ts_codes)} 只成分股")

    # Smart sync: incremental or full fetch
    print("\n[2/10] 同步数据...")
    new_data = sync_stocks_data(ts_codes, end_date=end_date)

    if new_data.empty:
        print("  所有数据已是最新，使用本地缓存数据...")
        df = load_daily_price(start_date=start_date, end_date=end_date)
    else:
        print(f"  获取完成: {len(new_data)} 行新数据")
        df = load_daily_price(start_date=start_date, end_date=end_date)

    if df.empty:
        print("  错误：未获取到数据！请检查网络连接和API。")
        return

    # Step 2: Clean data
    print("\n[3/10] 清洗数据...")
    df, report = clean_pipeline(df)
    print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只股票")
    print(f"  日期范围: {report['date_range']}")
    if report["issues"]:
        for issue in report["issues"]:
            print(f"  ⚠️  {issue}")
    else:
        print("  ✅ 数据验证通过")

    # Save cleaned data
    saved = save_daily_price(df)
    print(f"  已保存 {saved} 行到数据库")

    # Step 3: Fetch & merge fundamental data
    print("\n[4/10] 获取基本面数据 (PE/PB/PS)...")
    basic_df = load_daily_basic(start_date=start_date, end_date=end_date)
    if basic_df.empty:
        print("  本地无基本面数据，从API获取...")
        basic_df = fetch_daily_basic(ts_codes, start_date=start_date, end_date=end_date)
        if not basic_df.empty:
            saved_basic = save_daily_basic(basic_df)
            print(f"  获取并保存 {saved_basic} 行基本面数据")
        else:
            print("  ⚠️ 未获取到基本面数据")
    else:
        print(f"  使用本地缓存基本面数据: {len(basic_df)} 行")

    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)
        has_pe = "✓" if "pe_ttm" in df.columns else "✗"
        has_pb = "✓" if "pb" in df.columns else "✗"
        print(f"  已合并: pe_ttm={has_pe} pb={has_pb}")
    else:
        print("  ⚠️ 无基本面数据，估值因子将跳过")

    # Step 4: Calculate factors
    print("\n[5/10] 计算因子...")
    factors = get_registered_factors()
    factor_cols = []
    for name, cls in factors.items():
        factor = cls()
        df = factor.calculate(df)
        factor_cols.append(factor.factor_name)
        print(f"  ✓ {factor.factor_name}: {factor.description}")

    # Step 5: Score and rank
    print("\n[6/10] 标准化 & 综合打分...")
    df = standardize_factors(df, factor_cols)

    # Adaptive weights (optional)
    weights = None
    if ADAPTIVE_WEIGHTS_ENABLED:
        print("  自适应权重已启用，基于滚动IC计算...")
        # Build IC DataFrame from ic_results (computed after scoring)
        # We'll compute total_score after IC analysis if adaptive is on
    df = compute_total_score(df, weights=weights)

    # Show top picks for the latest date
    latest_date = df["trade_date"].max()
    top_picks = select_top_n(df, latest_date, n=top_n)

    # Industry neutral constraint
    if INDUSTRY_NEUTRAL_ENABLED:
        industry_map = get_industry_map(ts_codes)
        if industry_map:
            top_picks = apply_industry_constraint(
                top_picks, industry_map,
                max_pct=INDUSTRY_NEUTRAL_MAX_PCT, n=top_n,
            )
            print(f"  行业中性约束已应用 (单行业上限 {INDUSTRY_NEUTRAL_MAX_PCT:.0%})")

    print(f"\n  {latest_date} 综合得分 Top {len(top_picks)}:")
    for _, row in top_picks.iterrows():
        print(f"    {row['ts_code']}  得分: {row['total_score']:.2f}")

    # Step 6: IC analysis
    print(f"\n[7/10] IC 分析 (预测窗口: {ic_window}日)...")
    df = compute_future_return(df, n_days=ic_window)
    return_col = f"future_return_{ic_window}d"

    ic_results = {}
    for name in factors:
        factor = factors[name]()
        factor_col = factor.factor_name
        if factor_col not in df.columns:
            continue
        result = evaluate_factor(df, factor_col, return_col)
        s = result["summary"]
        verdict = result["verdict"]
        direction = "正" if s["ic_direction"] == 1 else ("负" if s["ic_direction"] == -1 else "—")
        print(
            f"  {factor_col:20s}  "
            f"IC均值: {s['ic_mean']:+.4f}  "
            f"ICIR: {s['icir']:+.4f}  "
            f"胜率: {s['win_rate']:.1%}  "
            f"方向: {direction}  "
            f"→ {verdict}"
        )
        ic_results[factor_col] = result

    # Adaptive weights: re-score if enabled and IC data is available
    if ADAPTIVE_WEIGHTS_ENABLED and ic_results:
        ic_series_data = {}
        for factor_col, result in ic_results.items():
            if "ic_series" in result and result["ic_series"] is not None:
                s = result["ic_series"]
                s.index.name = "trade_date"
                ic_series_data[factor_col] = s
        if ic_series_data:
            ic_df_for_weights = pd.DataFrame(ic_series_data).reset_index()
            weights = compute_adaptive_weights(
                ic_df_for_weights,
                window=ADAPTIVE_WEIGHTS_IC_WINDOW,
            )
            print(f"  自适应权重计算完成 (窗口={ADAPTIVE_WEIGHTS_IC_WINDOW}日)")
            df = compute_total_score(df, weights=weights)
        else:
            print("  ⚠️ IC数据不足，使用固定权重")

    # Step 7: Backtest
    print(f"\n[8/10] 回测 (调仓频率: {rebalance_freq}, 持仓: {top_n}只, 初始资金: {initial_capital:,.0f})...")

    # Load industry map for backtest if industry neutral is enabled
    bt_industry_map = None
    if INDUSTRY_NEUTRAL_ENABLED:
        bt_industry_map = get_industry_map(ts_codes)

    engine = BacktestEngine(
        initial_capital=initial_capital,
        top_n=top_n,
        rebalance_freq=rebalance_freq,
        risk_control_enabled=RISK_CONTROL_ENABLED,
        stop_loss=RISK_CONTROL_STOP_LOSS,
        take_profit=RISK_CONTROL_TAKE_PROFIT,
        max_drawdown_stop=RISK_CONTROL_MAX_DRAWDOWN_STOP,
        cooldown_days=RISK_CONTROL_COOLDOWN_DAYS,
        position_sizing_method=POSITION_SIZING_METHOD,
        min_weight=POSITION_SIZING_MIN_WEIGHT,
        max_weight=POSITION_SIZING_MAX_WEIGHT,
        industry_map=bt_industry_map,
        max_industry_pct=INDUSTRY_NEUTRAL_MAX_PCT,
    )

    # Fetch benchmark data
    benchmark_df = get_index_daily(start_date=start_date, end_date=end_date)

    bt_result = engine.run(df, benchmark_df=benchmark_df)
    print(bt_result.summary())

    # Step 8: Visualization
    print("\n[9/10] 生成因子分析图表...")
    code2name = dict(zip(constituents["ts_code"], constituents["name"]))
    generate_all_charts(df, ic_results, factor_cols, code2name)

    print("\n[10/10] 生成回测图表...")
    generate_backtest_charts(bt_result)

    # Export results
    print("\n导出数据...")

    # Export factor scores (latest date)
    latest_df = df[df["trade_date"] == latest_date].copy()
    export_cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount",
                   "total_score", "momentum_score", "vol_score", "volatility_score",
                   "rsi_score", "ma_deviation_score", "turnover_momentum_score", "intraday_range_score",
                   "pe_ttm_rank_score", "pb_rank_score"]
    available_export = [c for c in export_cols if c in latest_df.columns]
    export_path = DATA_DIR / "factor_scores.csv"
    latest_df[available_export].to_csv(export_path, index=False)
    print(f"  因子评分已导出: {export_path}")

    # Export backtest trades
    if not bt_result.trades.empty:
        trades_path = DATA_DIR / "backtest_trades.csv"
        bt_result.trades.to_csv(trades_path, index=False)
        print(f"  回测交易记录已导出: {trades_path} ({len(bt_result.trades)} 笔)")

    # Export backtest NAV series
    if not bt_result.nav_series.empty:
        nav_path = DATA_DIR / "backtest_nav.csv"
        bt_result.nav_series.to_csv(nav_path, index=False)
        print(f"  回测净值序列已导出: {nav_path}")

    print("\n" + "=" * 60)
    print("  完成!")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline()

#!/usr/bin/env python3
"""回测：流动性增强版0709（4因子最优组合）"""

import os, sys
from pathlib import Path

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"]:
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
from src.factors.scorer import standardize_factors, compute_total_score, select_top_n

# ── 注册因子 ──
import src.factors.roe_change, src.factors.liquidity, src.factors.volume
from src.factors.base import get_registered_factors

from src.backtest.engine import BacktestEngine
from src.visualization.backtest_charts import generate_backtest_charts

# ── 配置 ──
INDEX = "000852"
START_DATE = "20230101"
END_DATE = "20260611"
TOP_N = 10
INITIAL_CAPITAL = 1_000_000
REBALANCE_FREQ = "M"

FACTORS = ["roe_yoy_rank", "amihud_20d", "vol_ratio", "dollar_volume_20d"]

# 优化后的方向感知权重 (score_col: weight)
WEIGHTS = {
    "roe_yoy_rank_score": 0.55,
    "amihud_score": 0.20,
    "vol_score": -0.15,
    "dollar_volume_score": -0.10,
}

def main():
    print("=" * 60)
    print("  流动性增强版0709 — 回测")
    print(f"  因子: {FACTORS}")
    print(f"  权重: {WEIGHTS}")
    print("=" * 60)

    # 1. 加载数据
    print(f"\n[1/6] 加载数据: {INDEX} {START_DATE} ~ {END_DATE}")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()
    print(f"  成分股: {len(ts_codes)} 只")

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    print(f"  行情: {len(df)} 行, {df['ts_code'].nunique()} 只")

    df, report = clean_pipeline(df)
    print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只")

    # 2. 合并基本面
    print("\n[2/6] 合并基本面...")
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

    # 3. 计算因子
    print("\n[3/6] 计算因子...")
    factors = get_registered_factors()
    for name in FACTORS:
        if name in factors:
            factor = factors[name]()
            df = factor.calculate(df)
            print(f"  ✓ {name}")

    # 4. 标准化 + 评分
    print("\n[4/6] 标准化 & 评分...")
    df = standardize_factors(df, FACTORS)
    df = compute_total_score(df, WEIGHTS)

    # 5. 回测
    print(f"\n[5/6] 回测 (调仓: {REBALANCE_FREQ}, 持仓: {TOP_N}只)...")
    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)
    engine = BacktestEngine(
        initial_capital=INITIAL_CAPITAL,
        top_n=TOP_N,
        rebalance_freq=REBALANCE_FREQ,
    )
    bt_result = engine.run(df, benchmark_df=benchmark_df)
    print(bt_result.summary())

    # 6. 图表
    print("\n[6/6] 生成图表...")
    generate_backtest_charts(bt_result)

    print("\n" + "=" * 60)
    print("  完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()

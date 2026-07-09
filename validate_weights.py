#!/usr/bin/env python3
"""Validate top weight combos on FULL period (2023-2026)."""

import os, sys
from pathlib import Path
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"]:
    os.environ.pop(key, None)
sys.path.insert(0, str(Path(__file__).parent))

from src.data.fetcher import get_index_constituents, get_index_daily
from src.data.cleaner import clean_pipeline
from src.data.storage import (load_daily_price, load_daily_basic, merge_fundamentals,
    load_fina_indicator, merge_fina_indicator)
from src.factors.scorer import standardize_factors, _factor_to_score_col
from src.backtest.engine import BacktestEngine

import src.factors.rsi; import src.factors.return_20d; import src.factors.roe_change
from src.factors.base import get_registered_factors

INDEX = "000852"
START = "20230101"
END = "20260611"
ALL_FACTORS = ["roe_yoy_rank", "return_20d", "rsi_14d"]

COMBOS = [
    ("🥇 冠军",     +0.60, -0.20, -0.20),
    ("🥈 亚军",     +0.55, -0.30, -0.15),
    ("🥉 季军",     +0.25, -0.50, -0.25),
    ("📍 基线",     +0.50, -0.25, -0.25),
    ("🔬 ROE轻",    +0.30, -0.45, -0.25),
    ("🔬 ROE中",    +0.40, -0.30, -0.30),
]

def main():
    print("全周期验证: 6 组合", flush=True)

    # Load data
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

    print(f"数据就绪, 开始回测...\n", flush=True)

    for label, roe, ret, rsi in COMBOS:
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

        engine = BacktestEngine(initial_capital=1_000_000, top_n=10, rebalance_freq="M")
        bt = engine.run(df_run, benchmark_df=benchmark_df)
        m = bt.metrics

        print(f"{label:8s} roe={roe:+.2f} ret={ret:+.2f} rsi={rsi:+.2f}  "
              f"年化={m['annual_return']:.2%}  回撤={m['max_drawdown']:.2%}  "
              f"夏普={m['sharpe_ratio']:.3f}  胜率={m['win_rate']:.1%}  净值=¥{m['final_nav']/1e6:.2f}M",
              flush=True)

if __name__ == "__main__":
    main()

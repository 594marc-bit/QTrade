#!/usr/bin/env python3
"""
第一阶段：因子筛选 (Factor Screening)

目标：从 31 个因子中筛选出 IC 显著且低冗余的候选因子池。

流程：
  1. 加载数据 + 计算全部 31 个因子
  2. 横截面 Z-score 标准化
  3. 计算各因子与未来 5 日收益的 IC（Spearman 秩相关）
  4. 过滤 IC 不显著的因子（|ICIR| < 阈值, 胜率过低）
  5. 计算因子间相关系数矩阵，剔除高相关（>0.7）冗余因子（保留 ICIR 更强的）

输出：筛选后的因子列表 + 详细报告
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
from src.data.fetcher import get_index_constituents
from src.data.cleaner import clean_pipeline
from src.data.storage import (
    load_daily_price, load_daily_basic, merge_fundamentals,
    load_fina_indicator, save_fina_indicator, merge_fina_indicator,
)
from src.factors.scorer import standardize_factors
from src.factors.ic_analyzer import compute_future_return, compute_ic_series, compute_ic_summary

# ── 导入全部因子模块以触发注册 ──
import src.factors.intraday_range
import src.factors.ma_deviation
import src.factors.momentum
import src.factors.return_20d
import src.factors.roe_change
import src.factors.rsi
import src.factors.trend_60d
import src.factors.turnover
import src.factors.valuation
import src.factors.volatility
import src.factors.volume
import src.factors.downside_risk
import src.factors.return_distribution
import src.factors.market_relative
import src.factors.liquidity
import src.factors.short_reversal
import src.factors.candlestick
import src.factors.valuation_extended
import src.factors.profitability
import src.factors.volume_price
from src.factors.base import get_registered_factors

# ── 配置 ──
INDEX = "000852"         # 中证1000
START_DATE = "20230101"
END_DATE = "20260611"
FORWARD_DAYS = 5          # 前瞻收益天数
MIN_ABS_ICIR = 0.10       # |ICIR| 最低阈值（低于此值视为不显著）
MIN_WIN_RATE = 0.50       # 最低胜率（>50% 即方向预测好于猜）
MAX_CORRELATION = 0.70    # 因子间相关性上限


def load_and_prepare():
    """加载数据 + 清洗 + 合并基本面"""
    print("=" * 70)
    print("  第一阶段：因子筛选")
    print("=" * 70)

    print(f"\n[1] 加载数据: {INDEX} {START_DATE} ~ {END_DATE}")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()
    print(f"  成分股: {len(ts_codes)} 只")

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    print(f"  行情: {len(df)} 行, {df['ts_code'].nunique()} 只")

    df, report = clean_pipeline(df)
    print(f"  清洗后: {report['total_rows']} 行, {report['total_stocks']} 只")

    # 合并估值
    basic_df = load_daily_basic(start_date=START_DATE, end_date=END_DATE)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    # 合并 ROE
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

    return df


def compute_all_factors(df):
    """计算全部已注册因子"""
    print(f"\n[2] 计算因子...")
    factors = get_registered_factors()
    factor_cols = []

    for name, cls in sorted(factors.items()):
        try:
            factor_inst = cls()
            df = factor_inst.calculate(df)
            factor_cols.append(factor_inst.factor_name)
            print(f"  ✓ {factor_inst.factor_name:30s} [{factor_inst.category}]")
        except Exception as e:
            print(f"  ✗ {name:30s} 失败: {e}")

    print(f"  成功: {len(factor_cols)}/{len(factors)} 个因子")
    return df, factor_cols


def screen_factors(df, factor_cols):
    """IC 分析 + 相关性筛选"""
    return_col = f"future_return_{FORWARD_DAYS}d"

    print(f"\n[3] 计算前瞻收益 (forward {FORWARD_DAYS}d)...")
    df = compute_future_return(df, n_days=FORWARD_DAYS)

    # ── Step A: 标准化 ──
    print(f"\n[4] 横截面 Z-score 标准化...")
    # 只对连续值因子做标准化（排除百分位排名类，它们本身已经是 0-100 分布）
    # 但实际上所有因子统一做 zscore 也不会有问题
    df = standardize_factors(df, factor_cols)

    # ── Step B: IC 分析 ──
    print(f"\n[5] IC 分析 (Spearman, forward {FORWARD_DAYS}d)...")
    ic_results = []
    for col in factor_cols:
        ic_series = compute_ic_series(df, col, return_col, method="spearman")
        summary = compute_ic_summary(ic_series)
        ic_results.append({
            "factor": col,
            **summary,
        })

    ic_df = pd.DataFrame(ic_results).set_index("factor")
    ic_df["abs_icir"] = ic_df["icir"].abs()

    # ── 打印全量 IC 报告 ──
    print(f"\n{'因子':30s} {'IC均值':>8s} {'ICIR':>8s} {'胜率':>7s} {'天数':>6s} {'方向':>4s}")
    print("-" * 70)
    for row in ic_results:
        dire = "正" if row["ic_direction"] == 1 else ("负" if row["ic_direction"] == -1 else "—")
        print(f"{row['factor']:30s} {row['ic_mean']:+8.4f} {row['icir']:+8.4f} {row['win_rate']:7.1%} {row['count']:6d} {dire:>4s}")

    # ── Step C: 第一轮过滤 —— IC 显著性 ──
    print(f"\n[6] 第一轮过滤: |ICIR| >= {MIN_ABS_ICIR}, 胜率 >= {MIN_WIN_RATE:.0%}")
    passed_ic = ic_df[
        (ic_df["abs_icir"] >= MIN_ABS_ICIR) &
        (ic_df["win_rate"] >= MIN_WIN_RATE)
    ].copy()

    dropped_ic = ic_df.index.difference(passed_ic.index)
    for f in dropped_ic:
        row = ic_df.loc[f]
        reasons = []
        if abs(row["icir"]) < MIN_ABS_ICIR:
            reasons.append(f"|ICIR|={abs(row['icir']):.3f}<{MIN_ABS_ICIR}")
        if row["win_rate"] < MIN_WIN_RATE:
            reasons.append(f"胜率={row['win_rate']:.1%}<{MIN_WIN_RATE:.0%}")
        print(f"  ✗ {f:30s} → 淘汰 ({', '.join(reasons)})")

    print(f"\n  通过: {len(passed_ic)}/{len(factor_cols)} 个因子")

    if len(passed_ic) == 0:
        print("  无因子通过 IC 筛选，降低阈值后重试")
        return df, []

    # ── Step D: 因子相关性矩阵（基于得分） ──
    score_cols = [f"{f}_score" if f"{f}_score" in df.columns else f for f in passed_ic.index]
    # 对齐：用标准化后的因子列（已经是 _score 后缀的 zscore）
    # standardize_factors 生成的列是 factor_name（直接替换原值），不是 _score
    # 实际列名就是 factor_col 本身，standardize 会原地替换

    print(f"\n[7] 计算因子间相关性 (基于日收益)...")
    # 用日收益做 corr 更精确（每天是一条观测）
    daily_data = df[["trade_date"] + list(passed_ic.index) + [return_col]].dropna()

    # 计算每个 factor 的日均截面值（避免每日冗余）
    # 直接用所有天数据算 pairwise correlation
    valid_data = daily_data[list(passed_ic.index)].dropna()
    corr_matrix = valid_data.corr()

    print(f"\n  因子相关系数矩阵 ({len(passed_ic)}×{len(passed_ic)}):")
    print(f"  {'':30s}", end="")
    for f in passed_ic.index:
        print(f"{f[:12]:>12s}", end="")
    print()
    for f1 in passed_ic.index:
        print(f"  {f1:30s}", end="")
        for f2 in passed_ic.index:
            corr_val = corr_matrix.loc[f1, f2]
            print(f"{corr_val:12.3f}", end="")
        print()

    # ── Step E: 第二轮过滤 —— 去冗余 ──
    print(f"\n[8] 第二轮过滤: 剔除高相关冗余因子 (r > {MAX_CORRELATION})")

    remaining = list(passed_ic.sort_values("abs_icir", ascending=False).index)
    removed = set()

    for i, f1 in enumerate(remaining):
        if f1 in removed:
            continue
        for f2 in remaining[i + 1:]:
            if f2 in removed:
                continue
            r = abs(corr_matrix.loc[f1, f2])
            if r > MAX_CORRELATION:
                # 保留 |ICIR| 更强的那个
                icir1 = passed_ic.loc[f1, "abs_icir"]
                icir2 = passed_ic.loc[f2, "abs_icir"]
                if icir1 >= icir2:
                    removed.add(f2)
                    print(f"  ✗ {f2:30s} (r={r:.3f} with {f1}, ICIR {icir2:.3f} < {icir1:.3f})")
                else:
                    removed.add(f1)
                    print(f"  ✗ {f1:30s} (r={r:.3f} with {f2}, ICIR {icir1:.3f} < {icir2:.3f})")
                    break  # f1 被移除，跳到下一个 f1

    final_factors = [f for f in remaining if f not in removed]

    # ── 最终报告 ──
    print(f"\n{'='*70}")
    print(f"  筛选结果")
    print(f"{'='*70}")
    print(f"  原始因子: {len(factor_cols)}")
    print(f"  IC 筛选通过: {len(passed_ic)}")
    print(f"  去冗后最终: {len(final_factors)}")
    print(f"\n  最终候选因子池 ({len(final_factors)} 个):")
    for f in final_factors:
        row = ic_df.loc[f]
        dire = "正" if row["ic_direction"] == 1 else "负"
        print(
            f"  ✓ {f:30s} "
            f"IC均值: {row['ic_mean']:+7.4f}  "
            f"ICIR: {row['icir']:+7.4f}  "
            f"胜率: {row['win_rate']:6.1%}  "
            f"方向: {dire}"
        )

    # 输出可直接复制到下一阶段的因子列表
    print(f"\n  ── 复制以下用于第二阶段 ──")
    print(f"  CANDIDATE_FACTORS = {final_factors}")

    return df, final_factors, ic_df, corr_matrix


def main():
    df = load_and_prepare()
    df, factor_cols = compute_all_factors(df)
    df, final_factors, ic_df, corr_matrix = screen_factors(df, factor_cols)

    # 保存报告
    report_path = Path(DATA_DIR) / "factor_screening_report.csv"
    ic_df.to_csv(report_path)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()

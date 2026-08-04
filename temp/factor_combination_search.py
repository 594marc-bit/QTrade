#!/usr/bin/env python3
"""
第二阶段：前向逐步选择 (Forward Stepwise Selection)

从第一阶段筛选出的候选因子中，用贪心算法搜索最佳因子组合。

流程：
  1. 对所有候选因子做 Z-score 标准化
  2. 从空组合开始，每次加入一个使复合 ICIR 提升最大的因子
  3. 以等权复合得分的 ICIR 作为选择标准
  4. 当 ICIR 不再提升或达到最大因子数时停止
  5. 对最优 Top-K 组合做完整回测验证

用法：
    python factor_combination_search.py
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
from itertools import combinations

from src.config import DATA_DIR
from src.data.fetcher import get_index_constituents
from src.data.cleaner import clean_pipeline
from src.data.storage import (
    load_daily_price, load_daily_basic, merge_fundamentals,
    load_fina_indicator, save_fina_indicator, merge_fina_indicator,
)
from src.factors.ic_analyzer import compute_future_return, compute_ic_series, compute_ic_summary
from src.factors.scorer import standardize_factors

# ── 导入全部因子模块 ──
import src.factors.intraday_range, src.factors.ma_deviation, src.factors.momentum
import src.factors.return_20d, src.factors.roe_change, src.factors.rsi
import src.factors.trend_60d, src.factors.turnover, src.factors.valuation
import src.factors.volatility, src.factors.volume
import src.factors.downside_risk, src.factors.return_distribution
import src.factors.market_relative, src.factors.liquidity
import src.factors.short_reversal, src.factors.candlestick
import src.factors.valuation_extended, src.factors.profitability, src.factors.volume_price
from src.factors.base import get_registered_factors

# ── 配置 ──
INDEX = "000852"
START_DATE = "20230101"
END_DATE = "20260611"
FORWARD_DAYS = 5
MAX_FACTORS = 8              # 组合最多 8 个因子
TOP_COMBINATIONS = 3         # 最终回测验证的组合数
MIN_ICIR_IMPROVEMENT = 0.01  # ICIR 提升阈值（小于此值停止搜索）

# ── 第一阶段筛选结果 ──
# (factor_name, ic_direction): +1 = high factor → high return, -1 = high factor → low return
CANDIDATE_FACTORS = [
    'amihud_20d', 'dollar_volume_20d', 'volume_price_corr_20d',
    'skewness_60d', 'trend_60d', 'intraday_range_10d',
    'roe_yoy_rank', 'vol_ratio', 'price_acceleration',
    'roe_rank', 'reversal_5d', 'downside_volatility_60d',
]

# IC 方向（来自第一阶段结果）
FACTOR_DIRECTION = {
    'amihud_20d': +1,                # 正：非流动性高 → 高收益（流动性溢价）
    'dollar_volume_20d': -1,         # 负：大盘股 → 低收益（规模效应）
    'volume_price_corr_20d': -1,     # 负：量价正相关 → 低收益
    'skewness_60d': -1,              # 负：正偏度 → 低收益（彩票效应）
    'trend_60d': -1,                 # 负：强趋势 → 低收益（反转）
    'intraday_range_10d': -1,        # 负：高振幅 → 低收益
    'roe_yoy_rank': +1,              # 正：ROE改善 → 高收益
    'vol_ratio': -1,                 # 负：放量 → 低收益
    'price_acceleration': +1,         # 正：加速 → 高收益
    'roe_rank': +1,                  # 正：ROE高 → 高收益
    'reversal_5d': -1,               # 负：短期涨 → 低收益（反转）
    'downside_volatility_60d': -1,   # 负：下行波动高 → 低收益
}


def load_data():
    """加载并准备数据"""
    print("=" * 70)
    print("  第二阶段：前向逐步选择")
    print("=" * 70)

    print(f"\n[1] 加载数据...")
    constituents = get_index_constituents(index_code=INDEX)
    ts_codes = constituents["ts_code"].tolist()

    df = load_daily_price(start_date=START_DATE, end_date=END_DATE)
    df = df[df["ts_code"].isin(ts_codes)]
    df, _ = clean_pipeline(df)

    basic_df = load_daily_basic(start_date=START_DATE, end_date=END_DATE)
    if not basic_df.empty:
        df = merge_fundamentals(df, basic_df)

    fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START_DATE, end_date=END_DATE)
    if fina_df.empty:
        from src.data.tushare_fetcher import fetch_fina_indicator
        fina_df = fetch_fina_indicator(ts_codes, start_date=START_DATE, end_date=END_DATE)
        if not fina_df.empty:
            save_fina_indicator(fina_df)
            fina_df = load_fina_indicator(ts_codes=ts_codes, start_date=START_DATE, end_date=END_DATE)
    if not fina_df.empty:
        df = merge_fina_indicator(df, fina_df)

    return df


def compute_factors(df):
    """计算候选因子"""
    print(f"\n[2] 计算 {len(CANDIDATE_FACTORS)} 个候选因子...")
    factors = get_registered_factors()
    for name in CANDIDATE_FACTORS:
        if name in factors:
            inst = factors[name]()
            df = inst.calculate(df)
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} 未注册")
    return df


def composite_icir(df, factor_subset, return_col):
    """
    计算一组因子的方向感知复合 ICIR。

    先将每个因子 Z-score 标准化，按 IC 方向翻转后等权加总，
    再计算复合得分与未来收益的 IC，返回 ICIR。
    """
    if len(factor_subset) == 0:
        return float('-inf'), {}

    # 确保得分列存在并方向翻转
    flipped_cols = []
    for f in factor_subset:
        sc = f"{f}_dir"
        if sc not in df.columns:
            raw_sc = f"{f}_score"
            if raw_sc not in df.columns:
                from src.factors.scorer import _safe_zscore
                df[raw_sc] = df.groupby("trade_date")[f].transform(_safe_zscore)
            # 按 IC 方向翻转：高分 = 预期高收益
            direction = FACTOR_DIRECTION.get(f, 1)
            df[sc] = df[raw_sc] * direction
        flipped_cols.append(sc)

    # 等权复合得分（已方向翻转，高分 = 预期高收益）
    composite = df[flipped_cols].mean(axis=1, skipna=True)

    # 计算 IC
    temp_df = df[["trade_date"]].copy()
    temp_df["composite_score"] = composite
    temp_df[return_col] = df[return_col]

    ic_series = compute_ic_series(temp_df, "composite_score", return_col, method="spearman")
    summary = compute_ic_summary(ic_series)

    return summary.get("icir", float('nan')), summary


def forward_stepwise(df, return_col):
    """
    前向逐步选择：

    1. 从空集开始
    2. 每次遍历剩余因子，选择加入后复合 ICIR 最高者
    3. 若 ICIR 提升 < MIN_ICIR_IMPROVEMENT，停止
    """
    print(f"\n[3] 前向逐步选择 (max {MAX_FACTORS} factors, min ICIR improvement: {MIN_ICIR_IMPROVEMENT})")
    print(f"{'':-^60}")

    remaining = set(CANDIDATE_FACTORS)
    selected = []
    best_icir = float('-inf')
    history = []

    for step in range(1, MAX_FACTORS + 1):
        candidates = []
        for factor in sorted(remaining):
            trial_set = selected + [factor]
            icir, summary = composite_icir(df, trial_set, return_col)
            candidates.append({
                'factor': factor,
                'icir': icir,
                'summary': summary,
            })

        # 按 ICIR 降序
        candidates.sort(key=lambda x: x['icir'], reverse=True)
        best_candidate = candidates[0]
        improvement = best_candidate['icir'] - best_icir

        # 打印本轮所有尝试
        print(f"\n  Step {step} (当前组合: {selected or '空'})")
        print(f"  {'候选因子':30s} {'复合ICIR':>10s} {'提升':>10s}")
        print(f"  {'-'*50}")
        for c in candidates[:5]:  # 只显示 top 5
            imp = c['icir'] - best_icir
            print(f"  {c['factor']:30s} {c['icir']:+10.4f} {imp:+10.4f}")

        if len(candidates) > 5:
            print(f"  ... (共 {len(candidates)} 个候选)")

        # 所有候选都不如当前最佳
        if best_candidate['icir'] <= best_icir or abs(improvement) < MIN_ICIR_IMPROVEMENT:
            print(f"\n  → 停止: ICIR 提升 {improvement:.4f} < {MIN_ICIR_IMPROVEMENT}")
            break

        # 加入最佳因子
        selected.append(best_candidate['factor'])
        remaining.remove(best_candidate['factor'])
        best_icir = best_candidate['icir']
        history.append({
            'step': step,
            'factor': best_candidate['factor'],
            'icir': best_icir,
            'improvement': improvement,
            'summary': best_candidate['summary'],
        })

        print(f"\n  ✓ 选中: {best_candidate['factor']}")
        print(f"    复合 ICIR: {best_icir:.4f} (提升 {improvement:+.4f})")

    return selected, history


def validate_with_backtest(df, factor_sets):
    """
    对多个因子组合做完整回测验证。

    factor_sets: list of (name, factor_list)
    """
    print(f"\n[4] 回测验证 Top 组合...")

    from src.factors.scorer import compute_total_score, select_top_n
    from src.backtest.engine import BacktestEngine
    from src.data.fetcher import get_index_daily

    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)

    results = []
    for name, factor_set in factor_sets:
        print(f"\n  回测: {name}")
        print(f"  因子: {factor_set}")

        # 标准化 + 方向感知评分
        df_bt = standardize_factors(df, factor_set)
        from src.factors.scorer import _factor_to_score_col

        # 构建方向感知权重（等权，方向由 IC 符号决定）
        n = len(factor_set)
        weights = {}
        for f in factor_set:
            sc = _factor_to_score_col(f)
            direction = FACTOR_DIRECTION.get(f, 1)
            weights[sc] = direction / n
            # 确保 zscore 列存在
            if sc not in df_bt.columns:
                from src.factors.scorer import _safe_zscore
                df_bt[sc] = df_bt.groupby("trade_date")[f].transform(_safe_zscore)

        df_bt = compute_total_score(df_bt, weights)

        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=10,
            rebalance_freq="M",
        )
        bt_result = engine.run(df_bt, benchmark_df=benchmark_df)

        m = bt_result.metrics
        results.append({
            'name': name,
            'factors': factor_set,
            'total_return': m.get('total_return', 0),
            'annual_return': m.get('annual_return', 0),
            'sharpe': m.get('sharpe_ratio', 0),
            'max_drawdown': m.get('max_drawdown', 0),
            'calmar': m.get('calmar_ratio', 0),
            'win_rate': m.get('win_rate', 0),
        })

    return results


def main():
    df = load_data()
    df = compute_factors(df)
    return_col = f"future_return_{FORWARD_DAYS}d"
    df = compute_future_return(df, n_days=FORWARD_DAYS)

    # ── 前向逐步选择 ──
    selected, history = forward_stepwise(df, return_col)

    # ── 报告 ──
    print(f"\n{'='*70}")
    print(f"  逐步选择结果")
    print(f"{'='*70}")
    print(f"\n  {'步':>3s}  {'因子':30s}  {'复合ICIR':>10s}  {'提升':>10s}  {'IC均值':>8s}  {'胜率':>7s}")
    print(f"  {'-'*70}")
    for h in history:
        s = h['summary']
        print(
            f"  {h['step']:3d}  {h['factor']:30s}  {h['icir']:+10.4f}  {h['improvement']:+10.4f}  "
            f"{s['ic_mean']:+8.4f}  {s['win_rate']:7.1%}"
        )

    print(f"\n  最优组合 ({len(selected)} 因子):")
    print(f"  {selected}")

    # ── 生成多个组合用于回测验证 ──
    # 取逐步过程的每个阶段的组合（1因子, 2因子, ... N因子）
    # 加上最后最优组合
    combos_to_test = []
    for h in history:
        subset = [x['factor'] for x in history[:h['step']]]
        combos_to_test.append((f"Top{h['step']}", subset))

    # 只回测最后3个（N-2, N-1, N）
    combos_to_test = combos_to_test[-min(3, len(combos_to_test)):]

    # ── 回测验证 ──
    bt_results = validate_with_backtest(df, combos_to_test)

    print(f"\n{'='*70}")
    print(f"  回测结果对比")
    print(f"{'='*70}")
    print(f"  {'组合':>10s}  {'因子数':>5s}  {'年化收益':>10s}  {'Sharpe':>8s}  {'最大回撤':>9s}  {'Calmar':>8s}  {'胜率':>7s}")
    print(f"  {'-'*65}")
    for r in bt_results:
        print(
            f"  {r['name']:>10s}  {len(r['factors']):5d}  "
            f"{r['annual_return']:9.1%}  {r['sharpe']:8.4f}  "
            f"{r['max_drawdown']:8.1%}  {r['calmar']:8.4f}  "
            f"{r['win_rate']:7.1%}"
        )

    # 输出最佳组合详情
    best = max(bt_results, key=lambda x: x['sharpe'])
    print(f"\n  ★ 最佳组合: {best['name']}")
    print(f"    因子: {best['factors']}")
    print(f"    Sharpe: {best['sharpe']:.4f}, 年化收益: {best['annual_return']:.1%}")
    print(f"    最大回撤: {best['max_drawdown']:.1%}, Calmar: {best['calmar']:.4f}")

    # ── 保存结果 ──
    history_df = pd.DataFrame(history)
    history_path = Path(DATA_DIR) / "factor_stepwise_history.csv"
    history_df.to_csv(history_path, index=False)
    print(f"\n结果已保存: {history_path}")


if __name__ == "__main__":
    main()

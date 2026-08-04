#!/usr/bin/env python3
"""
第三阶段：权重网格搜索 (Weight Grid Search)

对第二阶段选出的最优 N 因子组合，搜索最佳权重配比。

流程：
  1. 粗粒度网格搜索（step=0.10），用复合 ICIR 做快速筛选
  2. 对 Top-K 组合做完整回测验证
  3. 精细调优（step=0.05）围绕最优区域

用法：
    python weight_optimization.py
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
from itertools import combinations_with_replacement, permutations

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

# ── 第二阶段最优因子组合 ──
OPTIMAL_FACTORS = ['amihud_20d', 'roe_yoy_rank', 'vol_ratio', 'dollar_volume_20d']

# IC 方向（来自第一阶段）
FACTOR_DIRECTION = {
    'amihud_20d': +1,
    'dollar_volume_20d': -1,
    'vol_ratio': -1,
    'roe_yoy_rank': +1,
}

# ── 网格搜索参数 ──
COARSE_STEP = 0.10      # 粗搜索步长
MIN_WEIGHT = 0.05        # 最低权重（0表示允许剔除因子）
TOP_N_ICIR = 20          # 粗筛后保留的组合数
TOP_N_BACKTEST = 10      # 最终回测验证数
FINE_STEP = 0.05         # 精细调优步长


def load_and_prepare():
    """加载数据 + 计算因子"""
    print("=" * 70)
    print("  第三阶段：权重网格搜索")
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

    print(f"\n[2] 计算 {len(OPTIMAL_FACTORS)} 个因子...")
    factors = get_registered_factors()
    for name in OPTIMAL_FACTORS:
        if name in factors:
            inst = factors[name]()
            df = inst.calculate(df)
            print(f"  ✓ {name}")

    # 计算前瞻收益
    return_col = f"future_return_{FORWARD_DAYS}d"
    df = compute_future_return(df, n_days=FORWARD_DAYS)

    # 标准化因子并方向翻转
    from src.factors.scorer import _factor_to_score_col
    df = standardize_factors(df, OPTIMAL_FACTORS)
    for f in OPTIMAL_FACTORS:
        sc = _factor_to_score_col(f)
        direction = FACTOR_DIRECTION.get(f, 1)
        df[f"{f}_dir"] = df[sc] * direction

    return df


def generate_weight_combinations(n_factors, step, min_w=0.0):
    """
    生成所有权重组合，满足 sum(w_i) = 1.0, w_i >= min_w。

    通过枚举 step 的整数倍实现。

    Yields: list of weights (floats)
    """
    n_steps = int(1.0 / step)
    # 枚举 w1, w2, w3, 计算 w4 = 1 - sum(w1,w2,w3)
    for i in range(int(min_w / step), n_steps + 1):
        for j in range(int(min_w / step), n_steps - i + 1):
            for k in range(int(min_w / step), n_steps - i - j + 1):
                l = n_steps - i - j - k
                if l >= int(min_w / step):
                    w = [i * step, j * step, k * step, l * step]
                    # 归一化（处理浮点误差）
                    total = sum(w)
                    w = [x / total for x in w]
                    yield w


def compute_composite_icir_for_weights(df, weights, return_col):
    """给定权重，计算复合得分的 ICIR。weights 为 dict: {factor_name: weight}."""
    flipped_cols = [f"{f}_dir" for f in OPTIMAL_FACTORS]
    composite = np.zeros(len(df))
    for i, f in enumerate(OPTIMAL_FACTORS):
        col = f"{f}_dir"
        if col in df.columns:
            composite += df[col].fillna(0) * weights[f]

    temp_df = pd.DataFrame({
        "trade_date": df["trade_date"],
        "composite_score": composite,
        return_col: df[return_col],
    })
    ic_series = compute_ic_series(temp_df, "composite_score", return_col, method="spearman")
    summary = compute_ic_summary(ic_series)
    return summary


def grid_search_coarse(df, return_col):
    """粗粒度网格搜索（快速 ICIR 筛选）"""
    print(f"\n[3] 粗粒度网格搜索 (step={COARSE_STEP})...")

    results = []
    n_total = 0
    for w in generate_weight_combinations(4, COARSE_STEP, min_w=MIN_WEIGHT):
        n_total += 1
        weight_dict = {OPTIMAL_FACTORS[i]: w[i] for i in range(4)}

        # 跳过全相等的平凡组合（后面单独测试）
        summary = compute_composite_icir_for_weights(df, weight_dict, return_col)
        results.append({
            'weights': weight_dict,
            'icir': summary.get('icir', float('nan')),
            'ic_mean': summary.get('ic_mean', float('nan')),
            'win_rate': summary.get('win_rate', float('nan')),
        })

    print(f"  共测试 {n_total} 个权重组合")

    # 排序
    results.sort(key=lambda x: x['icir'], reverse=True)
    return results


def validate_with_backtest(df, top_results):
    """对 ICIR 最优的组合做回测验证"""
    print(f"\n[4] 回测验证 Top {TOP_N_BACKTEST} 组合...")

    from src.factors.scorer import compute_total_score, _factor_to_score_col
    from src.backtest.engine import BacktestEngine
    from src.data.fetcher import get_index_daily

    benchmark_df = get_index_daily(ts_code=f"{INDEX}.SH", start_date=START_DATE, end_date=END_DATE)

    validated = []
    for rank, r in enumerate(top_results[:TOP_N_BACKTEST]):
        weight_dict = r['weights']
        print(f"\n  [{rank+1}/{TOP_N_BACKTEST}] 权重: { {k: f'{v:.2f}' for k, v in weight_dict.items()} }")

        # 构建方向感知的 score 权重
        score_weights = {}
        for f in OPTIMAL_FACTORS:
            sc = _factor_to_score_col(f)
            score_weights[sc] = weight_dict[f] * FACTOR_DIRECTION.get(f, 1)

        df_bt = compute_total_score(df, score_weights)

        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=10,
            rebalance_freq="M",
        )
        bt_result = engine.run(df_bt, benchmark_df=benchmark_df)
        m = bt_result.metrics

        validated.append({
            **r,
            'annual_return': m.get('annual_return', 0),
            'sharpe': m.get('sharpe_ratio', 0),
            'max_drawdown': m.get('max_drawdown', 0),
            'calmar': m.get('calmar_ratio', 0),
            'win_rate_bt': m.get('win_rate', 0),
        })

    return validated


def grid_search_fine(df, return_col, best_weights):
    """围绕最优权重做精细调优"""
    print(f"\n[5] 精细调优 (step={FINE_STEP})...")

    base = best_weights.copy()
    results = []

    # 在最优权重附近 ±0.15 范围内搜索
    for f1_w in _weight_range(base[OPTIMAL_FACTORS[0]], FINE_STEP, 0.15):
        for f2_w in _weight_range(base[OPTIMAL_FACTORS[1]], FINE_STEP, 0.15):
            for f3_w in _weight_range(base[OPTIMAL_FACTORS[2]], FINE_STEP, 0.15):
                for f4_w in _weight_range(base[OPTIMAL_FACTORS[3]], FINE_STEP, 0.15):
                    w = [f1_w, f2_w, f3_w, f4_w]
                    total = sum(w)
                    if abs(total - 1.0) > 0.001:
                        continue
                    w = [x / total for x in w]
                    if min(w) < 0.02:
                        continue

                    weight_dict = {OPTIMAL_FACTORS[i]: w[i] for i in range(4)}
                    summary = compute_composite_icir_for_weights(df, weight_dict, return_col)
                    results.append({
                        'weights': weight_dict,
                        'icir': summary.get('icir', float('nan')),
                        'ic_mean': summary.get('ic_mean', float('nan')),
                        'win_rate': summary.get('win_rate', float('nan')),
                    })

    results.sort(key=lambda x: x['icir'], reverse=True)
    print(f"  共测试 {len(results)} 个精细组合")
    return results


def _weight_range(center, step, spread):
    """生成 center ± spread 范围内的权重值"""
    vals = []
    n = int(spread / step)
    for i in range(-n, n + 1):
        v = center + i * step
        if 0.02 <= v <= 0.80:
            vals.append(v)
    return vals


def print_results(results, title="结果", top_n=15):
    """格式化打印结果"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    print(f"  {'排名':>4s}  {'amihud':>7s}  {'roe_yoy':>7s}  {'vol_rat':>7s}  {'dollar_v':>7s}  "
          f"{'ICIR':>8s}  {'IC均值':>7s}  {'胜率':>6s}", end="")
    if 'sharpe' in results[0]:
        print(f"  {'年化':>7s}  {'Sharpe':>7s}  {'回撤':>7s}")
    else:
        print()
    print(f"  {'-'*85}")

    for i, r in enumerate(results[:top_n]):
        w = r['weights']
        line = (
            f"  {i+1:4d}  {w['amihud_20d']:7.3f}  {w['roe_yoy_rank']:7.3f}  "
            f"{w['vol_ratio']:7.3f}  {w['dollar_volume_20d']:7.3f}  "
            f"{r['icir']:+8.4f}  {r['ic_mean']:+7.4f}  {r['win_rate']:6.1%}"
        )
        if 'sharpe' in r:
            line += f"  {r['annual_return']:6.1%}  {r['sharpe']:7.4f}  {r['max_drawdown']:6.1%}"
        print(line)


def main():
    df = load_and_prepare()
    return_col = f"future_return_{FORWARD_DAYS}d"

    # ── 粗搜索 ──
    coarse_results = grid_search_coarse(df, return_col)

    # 显示等权基准
    equal_w = {f: 0.25 for f in OPTIMAL_FACTORS}
    eq_summary = compute_composite_icir_for_weights(df, equal_w, return_col)
    print(f"\n  等权基准: ICIR={eq_summary['icir']:.4f}, IC均值={eq_summary['ic_mean']:.4f}, 胜率={eq_summary['win_rate']:.1%}")

    print_results(coarse_results, f"ICIR 排名 Top {TOP_N_ICIR}", top_n=TOP_N_ICIR)

    # ── 回测验证 ──
    validated = validate_with_backtest(df, coarse_results)
    validated.sort(key=lambda x: x['sharpe'], reverse=True)
    print_results(validated, "回测验证排名", top_n=TOP_N_BACKTEST)

    # ── 精细调优 ──
    best = validated[0]
    print(f"\n  围绕最优权重进行精细搜索: { {k: f'{v:.2f}' for k, v in best['weights'].items()} }")
    fine_results = grid_search_fine(df, return_col, best['weights'])

    if fine_results:
        print_results(fine_results, "精细搜索 Top 15", top_n=15)
        # 对精细搜索的前几个做回测
        fine_validated = validate_with_backtest(df, fine_results[:5])
        fine_validated.sort(key=lambda x: x['sharpe'], reverse=True)
        print_results(fine_validated, "精细搜索回测验证", top_n=5)
        best = fine_validated[0]

    # ── 最终推荐 ──
    print(f"\n{'='*70}")
    print(f"  ★ 最终推荐权重")
    print(f"{'='*70}")
    w = best['weights']
    print(f"  amihud_20d:        {w['amihud_20d']:.2f}")
    print(f"  roe_yoy_rank:      {w['roe_yoy_rank']:.2f}")
    print(f"  vol_ratio:         {w['vol_ratio']:.2f}")
    print(f"  dollar_volume_20d: {w['dollar_volume_20d']:.2f}")
    print(f"\n  ICIR: {best['icir']:.4f}")
    if 'sharpe' in best:
        print(f"  Sharpe: {best['sharpe']:.4f}")
        print(f"  年化收益: {best['annual_return']:.1%}")
        print(f"  最大回撤: {best['max_drawdown']:.1%}")

    # ── 保存 ──
    results_df = pd.DataFrame(validated)
    results_path = Path(DATA_DIR) / "weight_optimization_results.csv"
    results_df.to_csv(results_path, index=False)
    print(f"\n结果已保存: {results_path}")


if __name__ == "__main__":
    main()

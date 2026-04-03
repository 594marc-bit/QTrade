## Context

当前 `src/factors/ic_analyzer.py` 中的 IC 有效性判定只考虑正向 IC：
- `evaluate_factor()` 第 139-143 行：`ic_mean > ic_threshold`（固定正方向）
- `compute_ic_summary()` 第 104 行：`win_rate = (valid > 0).sum()`（只统计正 IC 天数）

实际运行中，intraday_range_10d (IC=-0.046) 和 volatility_20d (IC=-0.040) 的绝对 IC 已超过 0.03 阈值，配合负权重使用完全有效，但被判定为"无效"。

## Goals / Non-Goals

**Goals:**
- 修复 IC 判定逻辑：使用 `abs(ic_mean) > threshold` 判断有效性
- 修复 win_rate 计算：对负方向因子统计 `IC < 0` 的天数占比
- 在 summary 中输出 `ic_direction`（+1/-1），为后续动态加权做准备

**Non-Goals:**
- 不实现 IC 动态加权（属于下一个 change）
- 不修改 scorer 或 weights 逻辑
- 不修改 IC 计算本身（spearman/pearson）

## Decisions

### 1. win_rate 改为方向感知

**方案 A（采用）**：`win_rate = (sgn(valid) == sgn(ic_mean)).sum() / len(valid)`
- 正方向因子统计 `IC > 0` 的比例，负方向因子统计 `IC < 0` 的比例
- 语义统一：衡量"IC 方向与预期一致的天数占比"

**方案 B**：始终统计 `abs(IC) > 0` 的比例 — 信息量不足，无法区分方向一致性

### 2. ic_direction 输出

在 summary dict 中新增 `ic_direction`：`+1` 表示正方向，`-1` 表示负方向，`0` 表示 ic_mean 为 0 或 NaN。后续动态加权可直接用 `ic_direction * abs(icir)` 作为因子权重依据。

### 3. evaluate_factor 判定逻辑

```python
is_effective = (
    not np.isnan(summary["ic_mean"])
    and abs(summary["ic_mean"]) > ic_threshold
    and summary["win_rate"] > win_rate_threshold
)
```

简单直接，只改两处：`>` → `abs() >`，win_rate 计算改为方向感知。

## Risks / Trade-offs

- **向后兼容**：`compute_ic_summary` 返回值新增 `ic_direction` key，不删改已有 key → 无破坏性
- **win_rate 语义变更**：旧代码中正方向因子的 win_rate 值不变，负方向因子的 win_rate 会从 ~50% 变为正确的方向一致率 → 可能有测试需更新

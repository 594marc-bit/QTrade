## Why

IC 分析判定逻辑存在 bug：`evaluate_factor()` 只检查 `ic_mean > 0.03`（正向），导致负 IC 因子（如 intraday_range_10d IC=-0.046、volatility_20d IC=-0.040）被判定为"无效"。这些因子的绝对 IC 已超过阈值，配合负权重使用时信号完全有效。同时 `win_rate` 的计算也只统计 `IC > 0` 的比例，对负方向因子不适用。

## What Changes

- **修复 `compute_ic_summary`**：`win_rate` 应统计"IC 方向与均值方向一致"的天数占比，而非固定 `> 0`
- **修复 `evaluate_factor`**：有效性判定改用 `abs(ic_mean) > threshold` + 方向感知的 win_rate
- **返回 IC 方向**：summary 中新增 `ic_direction` 字段（+1 或 -1），供后续动态加权使用

## Capabilities

### New Capabilities

- `ic-direction-aware`: IC 分析的方向感知判定 — 正确处理正/负 IC 因子的有效性评估

### Modified Capabilities

（无已有 specs）

## Impact

- `src/factors/ic_analyzer.py` — 核心修改
- `tests/test_factors.py` — 更新 IC 测试用例
- `main.py` — 输出展示增加方向信息

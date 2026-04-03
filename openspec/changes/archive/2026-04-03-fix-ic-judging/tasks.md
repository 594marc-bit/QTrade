## 1. IC Summary 修复

- [x] 1.1 修改 `compute_ic_summary()`：win_rate 改为方向感知（IC sign == mean sign），新增 ic_direction 字段
- [x] 1.2 修改 `evaluate_factor()`：有效性判定改用 `abs(ic_mean) > threshold` + 方向感知 win_rate

## 2. 测试更新

- [x] 2.1 更新 `tests/test_factors.py` 中的 TestICAnalyzer：验证负 IC 因子的有效性判定、ic_direction 字段、方向感知 win_rate
- [x] 2.2 运行全量测试确认无回归：`pytest tests/ -v`

## 3. 输出展示

- [x] 3.1 更新 `main.py` 的 IC 分析输出：打印 ic_direction 信息

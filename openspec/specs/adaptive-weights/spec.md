## ADDED Requirements

### Requirement: 滚动 IC 自适应权重计算
系统 SHALL 提供基于历史 IC 序列动态计算因子权重的功能。给定各因子的日 IC 时间序列和滚动窗口长度，系统 SHALL 计算每个因子的滚动 IC 均值，并将其转换为因子权重。

#### Scenario: 正常计算自适应权重
- **WHEN** 提供包含 9 个因子、至少 60 个交易日的 IC 数据
- **THEN** 系统返回一个权重字典，各因子权重之和为 1，且保留因子原有的正负方向

#### Scenario: IC 数据不足时回退
- **WHEN** 提供的 IC 数据行数少于滚动窗口长度
- **THEN** 系统 SHALL 回退使用 `DEFAULT_WEIGHTS` 固定权重，并输出警告日志

#### Scenario: 配置开关控制
- **WHEN** `adaptive_weights.enabled` 配置为 `false`（默认）
- **THEN** 系统 SHALL 使用固定权重，不执行自适应计算

#### Scenario: 自适应权重与评分系统集成
- **WHEN** `adaptive_weights.enabled` 为 `true` 且有足够 IC 数据
- **THEN** `compute_total_score()` SHALL 使用自适应权重替代 `DEFAULT_WEIGHTS`

### Requirement: 自适应权重配置参数
系统 SHALL 支持以下配置项：
- `adaptive_weights.enabled`: 布尔值，是否启用（默认 false）
- `adaptive_weights.ic_window`: 整数，滚动窗口天数（默认 60）

#### Scenario: 读取配置
- **WHEN** 系统启动时读取 config.ini
- **THEN** 从 `[adaptive_weights]` section 解析上述参数，未配置时使用默认值

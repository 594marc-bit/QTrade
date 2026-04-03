## ADDED Requirements

### Requirement: 评分加权持仓
系统 SHALL 支持根据个股评分分配持仓权重。评分越高的股票获得越大的仓位。

#### Scenario: 评分加权分配
- **WHEN** 配置 `position_sizing.method = score_weighted`，选出的 Top10 股票评分为 [0.86, 0.80, ..., 0.72]
- **THEN** 各股权重 = 个股评分 / 总评分，权重之和为 1

#### Scenario: 评分加权权重上下限
- **WHEN** 评分加权计算后某只股票权重超过 20% 或低于 5%
- **THEN** 系统 SHALL 将其限制在 [5%, 20%] 范围内，剩余权重按比例重新分配

### Requirement: 风险平价持仓
系统 SHALL 支持风险平价（Risk Parity）仓位分配。根据个股历史波动率分配权重，使每只股票的风险贡献相等。

#### Scenario: 风险平价分配
- **WHEN** 配置 `position_sizing.method = risk_parity`
- **THEN** 各股权重 ∝ 1/个股波动率（20日标准差），归一化后权重之和为 1

#### Scenario: 波动率数据不足
- **WHEN** 某只股票波动率数据不足 20 个交易日
- **THEN** 系统 SHALL 回退为等权分配该股票权重，并输出警告日志

### Requirement: 等权持仓（默认）
系统 SHALL 保持现有的等权持仓模式作为默认选项。

#### Scenario: 等权模式
- **WHEN** 配置 `position_sizing.method = equal_weight`（默认）
- **THEN** 各股权重 = 1/N（N 为持仓股票数量）

### Requirement: 仓位管理与 Portfolio 集成
系统 SHALL 将仓位权重传递给 `Portfolio.rebalance()` 方法执行。

#### Scenario: Portfolio 接收权重字典
- **WHEN** `Portfolio.rebalance()` 接收到 `target_weights` 参数（非 None）
- **THEN** 系统 SHALL 按指定权重调整持仓，而非等权分配

#### Scenario: 权重为 None 时等权
- **WHEN** `Portfolio.rebalance()` 的 `target_weights` 参数为 None
- **THEN** 系统 SHALL 使用等权分配（向后兼容）

### Requirement: 仓位管理配置参数
系统 SHALL 支持以下配置项：
- `position_sizing.method`: 字符串，仓位模式（`equal_weight`/`score_weighted`/`risk_parity`，默认 `equal_weight`）
- `position_sizing.min_weight`: 浮点数，单只股票最小权重（默认 0.05）
- `position_sizing.max_weight`: 浮点数，单只股票最大权重（默认 0.20）

#### Scenario: 读取配置
- **WHEN** 系统启动时读取 config.ini
- **THEN** 从 `[position_sizing]` section 解析上述参数，未配置时使用默认值

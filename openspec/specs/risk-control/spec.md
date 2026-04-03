## ADDED Requirements

### Requirement: 个股止损
系统 SHALL 支持在回测中对持仓个股进行止损检查。当个股从买入价计算的收益率跌破止损阈值时，触发卖出。

#### Scenario: 触发个股止损
- **WHEN** 某持仓个股收益率 ≤ -8%（默认阈值），且风控已启用
- **THEN** 系统 SHALL 在当日以收盘价卖出该股票，并记录交易日志

#### Scenario: 未触发止损
- **WHEN** 所有持仓个股收益率均高于止损阈值
- **THEN** 系统 SHALL 不执行任何卖出操作

#### Scenario: 止损后冷冻期
- **WHEN** 一只股票因止损被卖出
- **THEN** 系统 SHALL 在后续 5 个交易日内不再买入该股票

### Requirement: 个股止盈
系统 SHALL 支持在回测中对持仓个股进行止盈检查。当个股从买入价计算的收益率超过止盈阈值时，触发卖出。

#### Scenario: 触发个股止盈
- **WHEN** 某持仓个股收益率 ≥ 15%（默认阈值），且风控已启用
- **THEN** 系统 SHALL 在当日以收盘价卖出该股票

### Requirement: 组合回撤止损
系统 SHALL 支持在回测中监控组合净值回撤。当组合从历史最高净值的回撤超过阈值时，清仓全部持仓。

#### Scenario: 触发组合回撤止损
- **WHEN** 组合净值回撤 ≥ 10%（默认阈值），且风控已启用
- **THEN** 系统 SHALL 以收盘价卖出所有持仓，进入空仓状态

#### Scenario: 回撤止损后恢复
- **WHEN** 组合因回撤止损清仓后，到达下一个调仓日
- **THEN** 系统 SHALL 正常执行选股和建仓操作

### Requirement: 风控每日检查
系统 SHALL 在回测的每个交易日（不仅限于调仓日）执行风控检查。

#### Scenario: 非调仓日的风控检查
- **WHEN** 当前交易日不是调仓日，但持仓中有个股触发止损/止盈或组合触发回撤止损
- **THEN** 系统 SHALL 执行相应的卖出操作

#### Scenario: 调仓日与风控并存
- **WHEN** 当前交易日既是调仓日又有风控触发
- **THEN** 系统 SHALL 先执行风控卖出，再执行调仓操作

### Requirement: 风控配置参数
系统 SHALL 支持以下配置项：
- `risk_control.enabled`: 布尔值，是否启用（默认 false）
- `risk_control.stop_loss`: 浮点数，个股止损阈值（默认 -0.08）
- `risk_control.take_profit`: 浮点数，个股止盈阈值（默认 0.15）
- `risk_control.max_drawdown_stop`: 浮点数，组合回撤止损阈值（默认 -0.10）
- `risk_control.cooldown_days`: 整数，止损后冷冻天数（默认 5）

#### Scenario: 读取配置
- **WHEN** 系统启动时读取 config.ini
- **THEN** 从 `[risk_control]` section 解析上述参数，未配置时使用默认值

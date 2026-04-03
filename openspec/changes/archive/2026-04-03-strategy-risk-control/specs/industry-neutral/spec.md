## ADDED Requirements

### Requirement: 行业分类数据获取
系统 SHALL 能够获取 A 股股票的行业分类信息并持久化存储。支持从 Tushare 或 AKShare 获取申万行业分类数据。

#### Scenario: 首次获取行业数据
- **WHEN** SQLite 数据库中无行业分类数据
- **THEN** 系统从配置的数据源获取全量行业分类，存入 SQLite 的 `industry_classify` 表

#### Scenario: 使用缓存数据
- **WHEN** SQLite 中已存在行业分类数据
- **THEN** 系统 SHALL 直接从数据库读取，不重复请求外部接口

#### Scenario: 数据源不可用时的降级
- **WHEN** 外部数据源请求失败
- **THEN** 系统 SHALL 输出警告日志，并按无行业约束模式继续运行

### Requirement: 行业中性选股约束
系统 SHALL 在选股时限制单一行业的最大持仓比例。在 Top N 选股结果上进行二次筛选。

#### Scenario: 正常行业约束筛选
- **WHEN** 配置 `industry_neutral.enabled = true`，`max_industry_pct = 0.3`，Top10 候选中有 5 只银行股
- **THEN** 系统按评分从高到低依次纳入，银行股最多选 3 只（30%），其余跳过，从后续候选中递补

#### Scenario: 配置关闭
- **WHEN** `industry_neutral.enabled = false`（默认）
- **THEN** 系统 SHALL 不做任何行业过滤，保持原始 Top N 结果

#### Scenario: 行业数据缺失
- **WHEN** 某只股票无行业分类数据
- **THEN** 系统 SHALL 将该股票归入"未知"行业类别，不阻止其入选

### Requirement: 行业约束配置参数
系统 SHALL 支持以下配置项：
- `industry_neutral.enabled`: 布尔值，是否启用（默认 false）
- `industry_neutral.max_industry_pct`: 浮点数，单一行业最大占比（默认 0.30）

#### Scenario: 读取配置
- **WHEN** 系统启动时读取 config.ini
- **THEN** 从 `[industry_neutral]` section 解析上述参数，未配置时使用默认值

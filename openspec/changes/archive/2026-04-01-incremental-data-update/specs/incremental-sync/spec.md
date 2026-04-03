## ADDED Requirements

### Requirement: 增量 UPSERT 写入
`save_daily_price()` SHALL 使用 SQLite `INSERT OR REPLACE` 进行增量写入，不再读取全量数据到内存。表上 SHALL 有 `(trade_date, ts_code)` UNIQUE 约束。

#### Scenario: 增量写入新数据
- **WHEN** 调用 `save_daily_price(new_df)` 且 new_df 包含数据库中不存在的 (trade_date, ts_code) 组合
- **THEN** 新数据行被插入数据库，已有数据不变

#### Scenario: 覆盖已有数据
- **WHEN** new_df 包含数据库中已存在的 (trade_date, ts_code) 组合
- **THEN** 已有行被新数据替换

#### Scenario: 自动创建 UNIQUE 约束
- **WHEN** 数据库表存在但没有 `(trade_date, ts_code)` UNIQUE 索引
- **THEN** 首次保存时自动创建该索引

### Requirement: 按股票粒度查询最新日期
系统 SHALL 提供 `get_latest_date_per_stock()` 函数，返回每只股票在数据库中的最新交易日期。

#### Scenario: 正常查询
- **WHEN** 数据库中有 300 只股票的数据
- **THEN** 返回 dict，key 为 ts_code，value 为最新 trade_date

#### Scenario: 部分股票无数据
- **WHEN** 某些 ts_code 在数据库中无记录
- **THEN** 这些 ts_code 不出现在返回 dict 中

### Requirement: 智能增量同步
`sync_stocks_data()` SHALL 按股票粒度自动判断全量或增量获取，最小化 API 调用次数。

#### Scenario: 数据库为空，全量获取
- **WHEN** 数据库无任何数据
- **THEN** 所有 300 只股票走全量获取 (START_DATE ~ END_DATE)

#### Scenario: 部分股票需要增量更新
- **WHEN** 某股票最新日期为 20260328，当前日期为 20260401
- **THEN** 仅获取该股票 20260329 ~ 20260401 的数据

#### Scenario: 股票已是最新，跳过
- **WHEN** 某股票最新日期等于 END_DATE
- **THEN** 跳过该股票，不调用 API

#### Scenario: 单只股票失败不影响其他
- **WHEN** 获取某只股票数据时 API 报错
- **THEN** 记录失败，继续获取下一只股票

### Requirement: 管道使用增量同步
`main.py` 数据获取步骤 SHALL 调用 `sync_stocks_data()`，而非当前的 "全量判断 + get_stocks_batch" 逻辑。

#### Scenario: 日常运行增量更新
- **WHEN** 运行 `python main.py` 且数据库已有数据
- **THEN** 仅获取缺失的新数据，打印增量统计（X 只全量 / Y 只增量 / Z 只跳过）

### Requirement: 保留 checkpoint 机制
批量获取时 SHALL 保留每 50 只股票的 checkpoint 保存，防止中断丢数据。

#### Scenario: 中途中断
- **WHEN** 获取到第 60 只股票时程序中断
- **THEN** 前 50 只的数据已通过 checkpoint 保存到数据库

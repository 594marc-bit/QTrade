## ADDED Requirements

### Requirement: 获取沪深300成分股列表
系统 SHALL 通过数据接口获取沪深300指数（399300.SZ）的最新成分股代码列表，并缓存到本地。

#### Scenario: 首次获取成分股
- **WHEN** 用户首次运行数据获取，本地无缓存
- **THEN** 系统调用 AKShare 接口获取沪深300成分股列表并存储到数据库

#### Scenario: 使用缓存成分股
- **WHEN** 本地已有成分股列表且未超过缓存有效期（7天）
- **THEN** 系统直接使用本地缓存，不发起网络请求

### Requirement: 获取股票日线行情数据
系统 SHALL 获取指定股票列表在指定日期范围内的日线行情数据（开高低收、成交量、成交额）。

#### Scenario: 获取单只股票行情
- **WHEN** 请求获取 600519.SH 在 20230101 至 20260330 的日线数据
- **THEN** 系统返回包含 trade_date, open, high, low, close, vol, amount 的 DataFrame

#### Scenario: 批量获取多只股票
- **WHEN** 请求获取沪深300全部成分股的3年日线数据
- **THEN** 系统逐只获取，每次请求间隔至少0.5秒，全部成功后返回合并的 DataFrame

#### Scenario: 增量更新已有数据
- **WHEN** 本地已有某股票截至 20260320 的数据，请求更新至 20260330
- **THEN** 系统仅获取 20260321 至 20260330 的新数据并追加到本地

### Requirement: 缺失值处理
系统 SHALL 对日线数据中的缺失值进行前向填充（ffill），对连续缺失超过5个交易日的行予以删除。

#### Scenario: 短期缺失值填充
- **WHEN** 某股票有1-3个交易日的数据缺失
- **THEN** 系统使用前向填充（ffill）补全缺失值

#### Scenario: 长期缺失值删除
- **WHEN** 某股票连续缺失超过5个交易日
- **THEN** 系统删除该缺失区间对应的行

### Requirement: 复权处理
系统 SHALL 对日线价格数据进行前复权处理，消除分红送股导致的价格断层。

#### Scenario: 自动复权
- **WHEN** 获取日线数据时
- **THEN** 系统返回前复权后的价格数据，确保除权除息日前后的价格连续可比

### Requirement: 停牌标记
系统 SHALL 根据成交量为0标记停牌日，供后续因子计算和回测使用。

#### Scenario: 标记停牌日
- **WHEN** 某股票某交易日成交量为0
- **THEN** 系统将该行的 is_trading 字段设为 False

#### Scenario: 标记正常交易日
- **WHEN** 某股票某交易日成交量大于0
- **THEN** 系统将该行的 is_trading 字段设为 True

### Requirement: 异常值过滤
系统 SHALL 过滤日线数据中的异常值，包括涨跌幅超限和成交量为零但有价格变动的情况。

#### Scenario: 过滤涨跌幅超限数据
- **WHEN** 某交易日收盘价相对前一日涨跌幅超过 ±20%
- **THEN** 系统将该行标记为异常并过滤

#### Scenario: 过滤量价矛盾数据
- **WHEN** 某交易日成交量为0但收盘价与前一日不同
- **THEN** 系统将该行标记为异常并过滤

### Requirement: 数据持久化存储
系统 SHALL 将清洗后的数据存储到 SQLite 数据库，表名为 daily_price，并支持导出 CSV。

#### Scenario: 存储到 SQLite
- **WHEN** 数据清洗完成
- **THEN** 系统将 DataFrame 写入 SQLite 数据库的 daily_price 表，如果表已存在则追加（去重）

#### Scenario: 导出 CSV
- **WHEN** 用户请求导出数据
- **THEN** 系统将指定股票或全部数据导出为 CSV 文件到 data/ 目录

### Requirement: 数据验证
系统 SHALL 在数据清洗完成后执行基本验证，确保数据质量。

#### Scenario: 价格合理性检查
- **WHEN** 数据清洗完成后
- **THEN** 系统验证所有收盘价 > 0，且单日涨跌幅在 ±20% 以内

#### Scenario: 数据完整性检查
- **WHEN** 数据清洗完成后
- **THEN** 系统验证每个交易日至少有250只股票有数据（沪深300的正常水平）

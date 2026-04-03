## ADDED Requirements

### Requirement: 获取沪深300成分股列表
系统 SHALL 通过 Tushare `pro.index_cons()` 接口获取沪深300指数的最新成分股代码列表，返回格式与 AKShare 一致（ts_code, name）。

#### Scenario: 通过 Tushare 获取成分股
- **WHEN** 数据源配置为 tushare，请求获取沪深300成分股
- **THEN** 系统调用 `pro.index_cons(index_code='399300.SZ')` 获取成分股列表，返回包含 ts_code 和 name 列的 DataFrame

#### Scenario: Tushare Token 未配置
- **WHEN** 数据源配置为 tushare 但 config.ini 中未设置 token
- **THEN** 系统在启动时抛出明确的配置错误提示，而非 API 调用失败

### Requirement: 获取单只股票日线行情
系统 SHALL 通过 Tushare `pro.daily()` + `pro.adj_factor()` 获取单只股票的日线行情数据，并本地计算前复权价格。

#### Scenario: 获取前复权日线数据
- **WHEN** 请求获取 600519.SH 的日线数据（前复权）
- **THEN** 系统分别获取 `pro.daily()` 和 `pro.adj_factor()`，计算前复权价 = 原始价 × 当日复权因子 / 最新复权因子，返回标准格式 DataFrame

#### Scenario: 输出格式与 AKShare 一致
- **WHEN** Tushare 获取并处理日线数据后
- **THEN** 输出 DataFrame 包含列 trade_date, ts_code, open, high, low, close, vol, amount，数据类型和含义与 AKShare fetcher 完全一致

### Requirement: 批量获取多只股票行情
系统 SHALL 支持批量获取多只股票的日线数据，遵守 Tushare 的 API 频率限制。

#### Scenario: 批量获取带频率控制
- **WHEN** 批量获取 300 只股票的日线数据
- **THEN** 系统逐只获取，每次请求间隔遵守 Tushare 频率限制（默认 0.5s），显示进度条，并在连续失败时提前停止

#### Scenario: 增量更新
- **WHEN** 本地已有某股票的数据，请求增量更新
- **THEN** 系统仅获取最新日期之后的新数据并追加

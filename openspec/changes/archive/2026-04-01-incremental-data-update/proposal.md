## Why

当前 `save_daily_price()` 每次保存都要读取全量数据 → 合并 → 写回全表，数据量增长后性能急剧下降。同时 `main.py` 管道在本地数据不足时直接全量重新获取 300 只股票（600 次 API 调用），容易触发 Tushare API 频率和调用次数上限。已有的 `get_stock_daily_incremental()` 增量获取功能未被管道使用。

## What Changes

- **BREAKING**: `save_daily_price()` 改用 SQLite `INSERT OR REPLACE` 增量写入，不再全量读写
- 重构 `main.py` 管道数据获取逻辑：优先增量更新，仅首次或数据严重缺失时全量获取
- 新增 `get_latest_date_per_stock()` 函数，查询每只股票的最新日期，按需增量获取
- 新增 `sync_stocks_data()` 统一调度函数：全量/增量自动判断 + 逐股增量 + 进度条 + 错误隔离

## Capabilities

### New Capabilities
- `incremental-sync`: 智能增量数据同步，按股票粒度判断是否需要增量获取，使用 INSERT OR REPLACE 高效写入

### Modified Capabilities

## Impact

- `src/data/storage.py`：`save_daily_price()` 重写为增量 UPSERT
- `src/data/tushare_fetcher.py`：`get_stocks_batch()` 增加增量模式
- `src/data/akshare_fetcher.py`：同上
- `main.py`：数据获取步骤改用增量优先逻辑
- 不影响下游因子计算、清洗、可视化模块

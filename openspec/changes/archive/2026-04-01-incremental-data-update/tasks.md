## 1. 存储层重构

- [x] 1.1 修改 `src/data/storage.py` — `save_daily_price()` 改用 `INSERT OR REPLACE` + `executemany` 增量写入
- [x] 1.2 修改 `src/data/storage.py` — 添加 `_ensure_unique_index()` 自动创建 UNIQUE 约束
- [x] 1.3 添加 `get_latest_date_per_stock()` — 返回每只股票的最新 trade_date dict

## 2. 增量同步调度

- [x] 2.1 修改 `src/data/tushare_fetcher.py` — 新增 `sync_stocks_data()` 统一调度（全量/增量/跳过 + checkpoint）
- [x] 2.2 修改 `src/data/akshare_fetcher.py` — 同步新增 `sync_stocks_data()`
- [x] 2.2 修改 `src/data/akshare_fetcher.py` — 同步新增 `sync_stocks_data()`
- [x] 2.3 修改 `src/data/fetcher.py` — 导出 `sync_stocks_data`，根据 DATA_SOURCE 分发

## 3. 管道集成

- [x] 3.1 修改 `main.py` — 数据获取步骤改用 `sync_stocks_data()`，替换现有全量判断逻辑- [x] 3.2 更新 `tests/test_tushare_fetcher.py` — 补充增量同步相关测试
- [x] 3.3 更新 `tests/test_data_pipeline.py` — 补充 `save_daily_price` UPSERT 测试

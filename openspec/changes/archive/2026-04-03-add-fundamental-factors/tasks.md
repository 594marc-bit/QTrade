## 1. 数据获取层

- [x] 1.1 在 `src/data/storage.py` 中新增 `daily_basic` 表的 save/load/merge 函数
- [x] 1.2 在 `src/data/fetcher.py` 中新增 `fetch_daily_basic` 函数，支持 Tushare 和 AKShare 双源

## 2. 估值因子

- [x] 2.1 新建 `src/factors/valuation.py`：实现 PeFactor（pe_ttm_rank）和 PbFactor（pb_rank）
- [x] 2.2 在 `src/config.py` 中新增权重：`pe_ttm_rank_score` 和 `pb_rank_score`

## 3. Pipeline 集成

- [x] 3.1 更新 `main.py`：基本面数据获取/merge/valuation 因子 import，步骤编号更新

## 4. 测试

- [x] 4.1 新增 TestPeFactor、TestPbFactor：排名范围 [0,100]、NaN 处理、score 列名
- [x] 4.2 新增 TestDailyBasicStorage：save/load/merge/date filter
- [x] 4.3 全量测试 79 passed, 0 failed

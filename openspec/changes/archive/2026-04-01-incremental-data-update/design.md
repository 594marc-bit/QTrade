## Context

当前 QTrade 有 300 只 HS300 成分股、约 23.5 万行日线数据，存储在 SQLite (`data/stock_data.db`)。

现状问题：
1. `save_daily_price()` 每次调用执行 `SELECT *` → `pd.concat` → `DROP + CREATE` 全表，23 万行时已明显变慢
2. `main.py` 用 `len(existing) > 100_000` 判断是否全量获取，不够精细——某些股票可能缺少近期数据但总行数超阈值
3. `get_stock_daily_incremental()` 已实现但未被管道使用
4. Tushare 免费账户有调用频率限制（约 500 次/分钟）和每日调用次数限制

## Goals / Non-Goals

**Goals:**
- `save_daily_price()` 改为增量 UPSERT，不再全量读写
- 管道自动判断每只股票是否需要增量更新，只调用必要的 API
- 首次运行（数据库为空）时自动走全量获取
- 单只股票获取失败不影响其他股票

**Non-Goals:**
- 不做定时调度（cron 等）
- 不做多数据源增量同步的差异比对
- 不改变数据库 schema

## Decisions

### 1. 使用 SQLite `INSERT OR REPLACE` 替代全量读写

**选择**: `INSERT OR REPLACE` + UNIQUE 约束
**备选**: `UPDATE + INSERT`（需要先查询判断）、`DELETE + INSERT`（需要精准条件）

**理由**: SQLite 原生支持 UPSERT 语义，配合 `(trade_date, ts_code)` UNIQUE 约束，单条 SQL 即可完成，无需读取已有数据到内存。性能从 O(N) 全量降为 O(M) 增量（M = 新增行数）。

**实现**: 在 `daily_price` 表上创建 `UNIQUE(trade_date, ts_code)` 约束，使用 `executemany` + `INSERT OR REPLACE` 批量写入。

### 2. 逐股增量 + 统一调度

**选择**: 新增 `sync_stocks_data()` 函数，按股票粒度判断增量/全量
**备选**: 全局增量（查最大日期，所有股票统一从该日期获取）

**理由**: 不同股票可能上市时间不同、停牌时间不同，逐股增量更精确。数据库为空时自动走全量。

**流程**:
```
sync_stocks_data(codes, end_date):
  1. 查询数据库每只股票的最新日期 (get_latest_date_per_stock)
  2. 分类:
     - 无数据 → 全量获取 (START_DATE ~ end_date)
     - 有数据且非最新 → 增量获取 (last_date+1 ~ end_date)
     - 已是最新 → 跳过
  3. 逐股获取，save_daily_price() 增量写入
  4. 进度条 + 连续失败保护
```

### 3. 保持 checkpoint 机制

批量获取时每 50 只股票 checkpoint 一次，防止中断丢数据。增量模式下同样需要。

## Risks / Trade-offs

- **[UNIQUE 约束迁移]** → 需要处理现有表可能没有 UNIQUE 约束的情况，首次运行时自动创建索引
- **[增量模式需要 adj_factor 重新计算]** → 前复权价格依赖最新复权因子，增量追加可能导致历史复权价格不准。解决：增量获取时仍获取全量 adj_factor 范围，或接受复权价格可能有微小偏差（仅影响新增数据的复权计算）
- **[部分股票无数据]** → 单只股票获取失败记录到 failed 列表，不中断整体流程，结束时打印汇总

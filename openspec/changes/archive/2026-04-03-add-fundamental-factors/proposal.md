## Why

当前 7 个因子全部基于量价数据（动量、量比、波动率、RSI 等），缺乏估值维度。基本面因子（PE/PB）在 A 股有显著的均值回归效应——高估值股票长期跑输，低估值股票跑赢。增加基本面因子可以提供与量价因子低相关的新信号，提升组合的预测能力。

## What Changes

- **新增数据源**：在 `src/data/fetcher.py` 中添加基本面数据获取函数，支持 Tushare `daily_basic`（PE_TTM、PB、PS_TTM）和 AKShare `stock_a_indicator_lg` 双源
- **新增存储表**：在 SQLite 中新增 `daily_basic` 表，存储每日基本面指标
- **新增 2 个因子**：
  - `valuation_pe`：PE_TTM 截面排名因子（低 PE = 价值股，预期正收益）
  - `valuation_pb`：PB 截面排名因子（低 PB = 低估值，预期正收益）
- **更新 scoring**：在 DEFAULT_WEIGHTS 中添加 2 个新因子权重
- **更新 pipeline**：main.py 中引入新因子

## Capabilities

### New Capabilities
- `fundamental-data`: 基本面数据获取与存储（PE_TTM、PB、PS_TTM）
- `valuation-factors`: 估值因子（PE、PB 截面排名）

### Modified Capabilities
（无已有 specs）

## Impact

- `src/data/fetcher.py` — 新增基本面数据获取函数
- `src/data/storage.py` — 新增 `daily_basic` 表的 save/load
- `src/factors/valuation.py` — 新增估值因子
- `src/config.py` — 新增权重常量
- `src/factors/scorer.py` — 无需改动（已支持动态因子名映射）
- `main.py` — 新增数据获取步骤 + 因子 import
- `tests/test_factors.py` — 新增估值因子测试

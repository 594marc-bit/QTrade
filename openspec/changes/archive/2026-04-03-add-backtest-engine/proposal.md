## Why

当前 QTrade 系统能计算因子、评分和 IC 分析，但无法验证策略是否真正盈利。三个因子全部显示"无效"（IC≈0），且缺少历史回测能力来评估策略表现。没有回测引擎，所有因子研究和策略开发都缺乏实证基础，无法回答"这个策略能赚多少钱"这个核心问题。

## What Changes

- 新增回测引擎核心模块，支持基于因子评分的股票组合策略回测
- 支持定时调仓（按交易日频率），模拟买入/卖出操作
- 计算完整回测指标：年化收益率、夏普比率、最大回撤、胜率等
- 支持交易成本建模（佣金、滑点、印花税）
- 新增回测结果可视化（净值曲线、回撤图、持仓分析）
- 集成到 main.py pipeline，支持命令行触发回测

## Capabilities

### New Capabilities
- `backtest-engine`: 回测引擎核心 — 事件驱动的组合回测框架，支持调仓、交易成本、绩效指标计算
- `backtest-visualization`: 回测结果可视化 — 净值曲线、回撤图、持仓分布、绩效摘要

### Modified Capabilities

## Impact

- 新增 `src/backtest/` 模块（engine.py, portfolio.py, metrics.py）
- 新增 `src/visualization/backtest_charts.py` 可视化模块
- 修改 `main.py` 增加回测步骤
- 依赖现有 `src/data/storage.py`、`src/factors/` 模块
- 无 breaking changes，现有功能不受影响

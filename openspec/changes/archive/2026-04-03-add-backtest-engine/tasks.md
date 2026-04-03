## 1. 回测引擎核心

- [x] 1.1 创建 `src/backtest/` 模块结构，实现 `BacktestResult` 数据类（nav_series, trades, metrics, benchmark_nav）
- [x] 1.2 实现 `src/backtest/portfolio.py` — `Portfolio` 类：初始资金、持仓管理（等权分配）、调仓逻辑（卖出旧仓/买入新仓）、停牌股被动持有
- [x] 1.3 实现 `src/backtest/metrics.py` — 绩效指标计算：年化收益、夏普比率、最大回撤、Calmar 比率、胜率、基准对比（alpha/信息比率）
- [x] 1.4 实现 `src/backtest/engine.py` — `BacktestEngine` 类：驱动回测流程，按调仓频率（D/W/M）选股调仓，集成 Portfolio 和 metrics

## 2. 交易成本建模

- [x] 2.1 在 Portfolio 中实现交易成本扣除：买入佣金（0.03%）、卖出佣金（0.03%）+ 印花税（0.05%），参数可配置

## 3. 基准数据获取

- [x] 3.1 在 `tushare_fetcher.py` 和 `akshare_fetcher.py` 中新增 `get_index_daily()` 函数，获取沪深300指数日线数据作为回测基准

## 4. 回测可视化

- [x] 4.1 实现 `src/visualization/backtest_charts.py` — 净值曲线图（策略 vs 基准）
- [x] 4.2 实现回撤图（面积图 + 最大回撤标注）
- [x] 4.3 实现绩效摘要表格（年化收益/夏普/最大回撤/Calmar/胜率/调仓次数）
- [x] 4.4 实现月度收益热力图

## 5. 管道集成

- [x] 5.1 修改 `main.py` — 新增回测步骤：因子评分完成后运行回测，输出绩效指标和可视化
- [x] 5.2 修改 `src/config.py` — 新增回测相关配置项（initial_capital, top_n, rebalance_freq, 交易成本参数）

## 6. 测试

- [x] 6.1 新增 `tests/test_backtest.py` — 覆盖 Portfolio 调仓逻辑、交易成本扣除、绩效指标计算、完整回测流程
- [x] 6.2 新增 `tests/test_backtest_metrics.py` — 验证夏普比率、最大回撤等指标的计算精度

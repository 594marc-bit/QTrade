## ADDED Requirements

### Requirement: BacktestEngine 驱动回测流程
系统 SHALL 提供 `BacktestEngine` 类作为回测入口，接收数据 DataFrame、策略参数，输出回测结果。

#### Scenario: 运行基本回测
- **WHEN** 传入包含 trade_date/ts_code/close/total_score 列的 DataFrame，以及 top_n=10、rebalance_freq="M" 参数
- **THEN** 引擎按月度调仓，返回包含每日净值、交易记录、绩效指标的 BacktestResult 对象

#### Scenario: 缺少必要列时报错
- **WHEN** 传入的 DataFrame 缺少 trade_date 或 close 列
- **THEN** 抛出 ValueError 提示缺少的列名

### Requirement: 定期调仓选股
系统 SHALL 支持按指定频率（D/W/M）调仓，每个调仓日按 total_score 降序选取 top_n 只股票。

#### Scenario: 月度调仓
- **WHEN** rebalance_freq="M"，top_n=10
- **THEN** 每月第一个交易日选取 total_score 最高的 10 只股票建立新持仓

#### Scenario: 周度调仓
- **WHEN** rebalance_freq="W"
- **THEN** 每周第一个交易日进行调仓

#### Scenario: 排除停牌股
- **WHEN** 某股票在调仓日 is_trading == False
- **THEN** 该股票不参与排名和选股

### Requirement: Portfolio 组合管理
系统 SHALL 维护组合状态（持仓、现金、净值），在调仓时执行卖出旧持仓、买入新持仓。

#### Scenario: 初始建仓
- **WHEN** 回测开始，初始资金 1,000,000
- **THEN** 等权分配到 top_n 只股票，记录买入成本

#### Scenario: 调仓换股
- **WHEN** 调仓日到来，新选股与旧持仓不同
- **THEN** 卖出不在新名单中的旧持仓，买入新入选股票，保留重叠持仓

#### Scenario: 停牌股被动持有
- **WHEN** 持仓中的股票停牌，且调仓时需要卖出
- **THEN** 该股票无法卖出，继续持有，不占用新资金分配

### Requirement: 交易成本建模
系统 SHALL 在每次交易中扣除交易成本。

#### Scenario: 扣除买入成本
- **WHEN** 买入 100,000 元股票
- **THEN** 实际花费 = 100,000 × (1 + buy_rate)，buy_rate 默认 0.0003

#### Scenario: 扣除卖出成本
- **WHEN** 卖出 100,000 元股票
- **THEN** 实际收入 = 100,000 × (1 - sell_rate - stamp_tax)，sell_rate 默认 0.0003，stamp_tax 默认 0.001

### Requirement: 绩效指标计算
系统 SHALL 从回测结果计算以下指标。

#### Scenario: 计算核心指标
- **WHEN** 回测完成
- **THEN** 返回 dict 包含: total_return, annual_return, sharpe_ratio, max_drawdown, calmar_ratio, win_rate, trade_count, avg_holding_days

#### Scenario: 基准对比
- **WHEN** 提供了基准净值序列（如沪深300）
- **THEN** 计算超额收益（alpha）、跟踪误差、信息比率

### Requirement: 回测结果输出
系统 SHALL 返回结构化的 BacktestResult 对象。

#### Scenario: 结果包含净值序列
- **WHEN** 回测完成
- **THEN** BacktestResult 包含 DataFrame 类型的 nav_series（列: date, nav, benchmark_nav, position_count）

#### Scenario: 结果包含交易记录
- **WHEN** 回测完成
- **THEN** BacktestResult 包含 DataFrame 类型的 trades（列: date, ts_code, action, shares, price, amount, cost）

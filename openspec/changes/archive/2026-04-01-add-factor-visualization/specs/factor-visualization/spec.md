## ADDED Requirements

### Requirement: IC 时序图
系统 SHALL 生成各因子每日 IC 值的时序折线图，X 轴为交易日期，Y 轴为 IC 值，包含零线参考。

#### Scenario: 正常生成 IC 时序图
- **WHEN** 管道运行完成且有至少 10 个交易日的因子和收益率数据
- **THEN** 生成包含所有因子 IC 值的折线图，保存为 `data/charts/ic_timeseries.png`

#### Scenario: 数据不足
- **WHEN** 因子数据不足 10 个交易日
- **THEN** 跳过 IC 时序图生成，打印警告信息

### Requirement: 评分分布直方图
系统 SHALL 生成最新交易日的综合评分分布直方图，展示股票评分的分布情况。

#### Scenario: 生成评分分布图
- **WHEN** 管道完成因子计算和综合评分
- **THEN** 生成评分分布直方图，X 轴为评分区间，Y 轴为股票数量，保存为 `data/charts/score_distribution.png`

### Requirement: Top 10 排名条形图
系统 SHALL 生成最新交易日综合得分 Top 10 股票的水平条形图，显示股票名称、代码和分项因子贡献。

#### Scenario: 生成 Top 10 图表
- **WHEN** 管道完成综合评分
- **THEN** 生成水平条形图，Y 轴为股票名称+代码，X 轴为分数，用不同颜色区分动量、量比、波动率的贡献，保存为 `data/charts/top10_stocks.png`

### Requirement: 因子相关性热力图
系统 SHALL 生成因子间相关系数的热力图，展示各因子的相互关系。

#### Scenario: 生成相关性热力图
- **WHEN** 管道完成因子计算
- **THEN** 生成因子相关性热力图，颜色表示相关系数（-1 到 1），保存为 `data/charts/factor_correlation.png`

### Requirement: 管道集成
系统 SHALL 在 main.py 管道中自动调用可视化模块，无需用户额外操作。

#### Scenario: 自动生成图表
- **WHEN** 运行 `python main.py`
- **THEN** 在管道末尾自动生成所有图表并打印保存路径

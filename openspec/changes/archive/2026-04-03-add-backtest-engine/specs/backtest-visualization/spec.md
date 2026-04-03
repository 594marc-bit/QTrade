## ADDED Requirements

### Requirement: 净值曲线图
系统 SHALL 绘制策略净值与基准净值的对比曲线图。

#### Scenario: 策略与基准对比
- **WHEN** 传入 BacktestResult 对象（包含策略净值和基准净值）
- **THEN** 生成双线图，X 轴为日期，Y 轴为归一化净值（起始=1.0），图例标注策略名和基准名

#### Scenario: 仅策略无基准
- **WHEN** 传入的 BacktestResult 不含基准净值
- **THEN** 仅绘制策略净值曲线

### Requirement: 回撤图
系统 SHALL 绘制策略历史回撤曲线。

#### Scenario: 回撤可视化
- **WHEN** 传入 BacktestResult 对象
- **THEN** 绘制面积图，Y 轴为回撤百分比（0 到负值），标注最大回撤点和发生日期

### Requirement: 绩效摘要
系统 SHALL 在图表中显示核心绩效指标文本。

#### Scenario: 显示绩效表格
- **WHEN** 传入 BacktestResult 对象
- **THEN** 在图表上方或侧边显示指标表格：年化收益、夏普比率、最大回撤、Calmar 比率、胜率、调仓次数

### Requirement: 月度收益热力图
系统 SHALL 按月度绘制收益热力图。

#### Scenario: 月度收益展示
- **WHEN** 传入 BacktestResult 的净值序列
- **THEN** 生成热力图，行为年份，列为月份，颜色深浅表示月度收益率，正值绿色，负值红色

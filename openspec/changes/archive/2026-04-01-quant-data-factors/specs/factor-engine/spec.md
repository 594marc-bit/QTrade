## ADDED Requirements

### Requirement: 因子基类
系统 SHALL 提供因子计算的抽象基类，所有因子 MUST 继承该基类并实现 `calculate` 方法。

#### Scenario: 因子注册与发现
- **WHEN** 系统初始化因子引擎
- **THEN** 系统扫描 factors/ 目录，自动注册所有继承基类的因子类

#### Scenario: 因子计算接口
- **WHEN** 对某个因子调用 calculate(df)
- **THEN** 因子在输入 DataFrame 上计算并返回一个新列，列名为因子名称

### Requirement: 动量因子
系统 SHALL 计算每只股票过去20个交易日的收益率作为动量因子值。

#### Scenario: 计算动量因子
- **WHEN** 输入包含 ts_code, trade_date, close 列的日线数据
- **THEN** 系统按股票分组计算 close.pct_change(20)，结果列名为 momentum_20d

#### Scenario: 新股不足20日
- **WHEN** 某股票上市不足20个交易日
- **THEN** 系统将该股票的动量因子值设为 NaN

### Requirement: 成交额动量因子
系统 SHALL 计算每只股票过去5日平均成交额与过去20日平均成交额的比值。

#### Scenario: 计算成交额动量
- **WHEN** 输入包含 ts_code, trade_date, amount 列的日线数据
- **THEN** 系统按股票分组计算 amount.rolling(5).mean() / amount.rolling(20).mean()，结果列名为 vol_ratio

#### Scenario: 成交额为零的情况
- **WHEN** 某股票过去20日平均成交额为0
- **THEN** 系统将 vol_ratio 设为 NaN 而非 inf

### Requirement: 波动率因子
系统 SHALL 计算每只股票过去20个交易日收益率的标准差作为波动率因子。

#### Scenario: 计算波动率
- **WHEN** 输入包含 ts_code, trade_date, close 列的日线数据
- **THEN** 系统按股票分组计算 close.pct_change().rolling(20).std()，结果列名为 volatility_20d

### Requirement: 因子标准化
系统 SHALL 对每个因子按交易日进行横截面 Z-Score 标准化。

#### Scenario: Z-Score 标准化
- **WHEN** 已计算出因子原始值（如 momentum_20d）
- **THEN** 系统按 trade_date 分组，对每组计算 (x - mean) / std，结果列名为因子名_score（如 momentum_score）

#### Scenario: 全为 NaN 的交易日
- **WHEN** 某交易日某因子值全为 NaN
- **THEN** 系统将该日该因子的标准化得分也设为 NaN

### Requirement: 综合打分
系统 SHALL 对多个因子标准化得分进行加权求和，生成综合得分。

#### Scenario: 默认权重打分
- **WHEN** 使用默认权重（动量40%、成交额动量30%、波动率30%取负）
- **THEN** 系统计算 total_score = momentum_score*0.4 + vol_ratio_score*0.3 + (-volatility_score)*0.3

#### Scenario: 自定义权重打分
- **WHEN** 用户指定自定义权重字典
- **THEN** 系统按自定义权重计算综合得分

### Requirement: 选股排序
系统 SHALL 按综合得分对股票进行每日排序，返回 Top N 股票列表。

#### Scenario: 选出 Top 10
- **WHEN** 请求某个交易日的 Top 10 股票
- **THEN** 系统返回该交易日 total_score 最高的10只股票的 ts_code 和得分

#### Scenario: 排除停牌股
- **WHEN** 某股票当日 is_trading 为 False
- **THEN** 该股票不参与选股排序

### Requirement: IC 分析
系统 SHALL 计算因子值与未来 N 日收益率的截面相关系数（IC），评估因子预测能力。

#### Scenario: 计算单日 IC
- **WHEN** 指定某交易日和因子名（如 momentum_score）及预测窗口（如5日）
- **THEN** 系统计算该日所有股票的因子值与未来5日收益率的 Spearman 相关系数

#### Scenario: 计算 IC 序列
- **WHEN** 指定因子名和时间段
- **THEN** 系统计算该时间段内每个交易日的 IC 值，返回 IC 序列

#### Scenario: IC 统计汇总
- **WHEN** 有 IC 序列数据
- **THEN** 系统返回 IC 均值、IC 标准差、ICIR（IC均值/IC标准差）、IC > 0 的比例

#### Scenario: 因子有效性判定
- **WHEN** IC 均值 > 0.03 且 IC > 0 的比例 > 55%
- **THEN** 系统判定该因子为有效因子

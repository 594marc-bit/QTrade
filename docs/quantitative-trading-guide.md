# 股市量化交易完整指南

_以A股为例，逐步详解每个环节_

---

## 目录

1. [数据获取与清洗](#1-数据获取与清洗)
2. [因子开发与策略构思](#2-因子开发与策略构思)
3. [回测（Backtesting）](#3-回测backtesting)
4. [绩效评估](#4-绩效评估)
5. [风险管理](#5-风险管理)
6. [实盘交易执行](#6-实盘交易执行)
7. [监控与迭代优化](#7-监控与迭代优化)

---

## 1. 数据获取与清洗

### 什么是数据获取与清洗？

量化交易的第一步是收集市场数据，并确保数据质量。没有干净的数据，再好的策略也是空中楼阁。业界常说 **"Garbage In, Garbage Out"**——数据质量直接决定策略的可靠性。

### 场景设定

假设你要开发一个**A股动量策略**：选出过去20天涨幅最大的股票，持有10天后卖出。第一步就是获取数据。

### Step 1：确定需要什么数据

你的策略需要：
- 沪深300成分股列表（知道买哪些股票）
- 每只股票过去3年的日线行情（开高低收、成交量）
- 可能还需要：涨跌停标记、停牌标记、复权因子

### Step 2：获取数据

**方式：免费API（如 Tushare）**
```python
import tushare as ts
pro = ts.pro_api('你的token')

# 获取沪深300成分股
df_cons = pro.index_cons(index_code='399300.SZ', start_date='20230101')

# 获取每只股票的日线数据
for ts_code in df_cons['con_code']:
    df = pro.daily(ts_code=ts_code, start_date='20230101', end_date='20260330')
    # 保存到本地
```

### Step 3：拿到的数据长这样

| trade_date | ts_code | open | high | low | close | vol | amount |
|-----------|---------|------|------|-----|-------|-----|--------|
| 2026-03-30 | 600519.SH | 1680 | 1695 | 1675 | 1690 | 32000 | 5.4亿 |
| 2026-03-30 | 000858.SZ | 145.2 | 146.8 | 144.5 | 146.0 | 89000 | 1.3亿 |
| 2026-03-30 | 603121.SH | 16.5 | 16.8 | 16.4 | 16.7 | 150000 | 2.5亿 |
| ... | ... | ... | ... | ... | ... | ... | ... |

每天300只股票 × 750个交易日（3年）= **22.5万行数据**

### Step 4：数据清洗（最耗时但最关键的环节）

#### 问题1：缺失值处理

**情况**：某股票连续3天没有数据

```
600036.SH:
2026-03-25  38.50  ✅
2026-03-26  NaN    ❌ 缺失（可能是API漏了）
2026-03-27  NaN    ❌
2026-03-28  38.60  ✅
```

**处理方式**：
- **删除**：直接删掉缺失的行 → 回测时这几天不交易
- **前向填充（ffill）**：用前一天的价格填充 → 适合短期停牌
- **插值**：用前后两个数据点计算中间值 → 不太适合股价数据

```python
df = df.fillna(method='ffill')  # 最常用
```

#### 问题2：复权处理

**问题**：股票会分红、送股，导致K线图出现"断层"

```
贵州茅台某次分红前：
2026-06-15  close: 1800
2026-06-16  close: 1200  ← 看似暴跌-33%，实际是除权除息
```

**处理**：用**前复权**或**后复权**，让价格连续可比

```python
# 前复权：调整历史价格，保持当前价格不变
df_adj = pro.daily(ts_code='600519.SH', adj='qfq')

# 后复权：调整当前价格，保持历史价格不变
df_adj = pro.daily(ts_code='600519.SH', adj='hfq')
```

复权后：
```
2026-06-15  close: 1200（已调整）
2026-06-16  close: 1200（连续，无断层）
```

#### 问题3：停牌处理

**问题**：股票停牌期间无法交易，回测时不能"买到"停牌股

```
603121.SH:
2026-03-25  16.70  正常交易 ✅
2026-03-26  停牌   ❌
2026-03-27  停牌   ❌
2026-03-28  16.80  复牌 ✅
```

**处理**：标记停牌日，回测时跳过买入

```python
# 检查是否停牌（成交量为0通常意味着停牌）
df['is_trading'] = df['vol'] > 0

# 买入前检查
if not df.loc[today, 'is_trading']:
    print("该股票停牌，跳过买入")
```

#### 问题4：异常值处理

**问题**：某些数据明显有问题

```
异常案例：
- 成交量为0但有价格变动（数据错误）
- 价格突然涨跌10倍（数据录入错误）
- 成交量比前一天大100倍（可能是合股/拆股）
```

**处理**：
```python
# 涨跌幅超过±20%（A股正常范围是±10%或±20%）
df['pct_change'] = df['close'].pct_change()
df = df[df['pct_change'].between(-0.20, 0.20)]

# 成交量为0但有价格变动
df = df[~((df['vol'] == 0) & (df['close'] != df['close'].shift(1)))]
```

#### 问题5：数据对齐

**问题**：不同股票上市日期不同，交易日也可能不一致

```
股票A：2023-01-03 ~ 2026-03-30（750个交易日）
股票B：2023-06-15 上市（只有600个交易日）
股票C：2024年退市（数据到2024-08-30就断了）
```

**处理**：
```python
# 取所有股票交易日的交集
all_dates = set()
for stock in stocks:
    all_dates.update(df[stock].index)
common_dates = sorted(all_dates.intersection(*[set(df[s].index) for s in stocks]))
```

### Step 5：存储清洗后的数据

```python
# 保存为本地文件
df_clean.to_csv('sh300_daily_clean.csv')

# 或用数据库（推荐，数据量大时）
import sqlite3
conn = sqlite3.connect('stock_data.db')
df_clean.to_sql('daily_price', conn, if_exists='replace')
```

### Step 6：简单验证

```python
# 1. 检查贵州茅台的收盘价是否合理
assert df[df['ts_code']=='600519.SH'].iloc[-1]['close'] > 1000

# 2. 每天交易股票数是否合理
daily_count = df.groupby('trade_date')['ts_code'].nunique()
assert daily_count.mean() > 250

# 3. 对比公开数据源（同花顺/东方财富）确认准确性
```

### 数据清洗流程图

```
原始数据
  ├─ 去除重复行
  ├─ 处理缺失值（ffill/删除）
  ├─ 复权处理（qfq/hfq）
  ├─ 标记停牌日
  ├─ 过滤异常值（涨跌幅、成交量）
  ├─ 数据对齐（统一时间轴）
  ├─ 存储清洗后数据
  └─ 验证数据正确性
```

> **经验法则**：数据清洗通常占量化开发 **60-70%** 的时间。

---

## 2. 因子开发与策略构思

### 什么是因子？

**因子（Factor）** 就是一个能预测未来收益的可量化信号。你可以把它理解为"选股的打分标准"。

比如你选股票时说"我要买PE低的"，PE就是一个因子。

### 常见因子类型

| 类型 | 举例 | 逻辑 |
|------|------|------|
| **动量因子** | 过去20天涨幅 | 涨得好的继续涨 |
| **反转因子** | 过去20天跌幅 | 跌多了会反弹 |
| **价值因子** | PE、PB、股息率 | 便宜的就是好的 |
| **质量因子** | ROE、毛利率 | 好公司值得买 |
| **波动率因子** | 20日波动率 | 低波动股票长期收益更好 |
| **技术因子** | MACD、RSI、布林带 | 价格形态有规律 |
| **情绪因子** | 换手率变化、北向资金流向 | 聪明钱知道方向 |

### 详细举例：构建一个多因子动量策略

假设我们想构建一个策略，**从沪深300中选出最可能上涨的10只股票**。

#### 因子1：价格动量（权重40%）

**逻辑**：过去涨得好的股票，短期内有惯性继续上涨。

```python
import pandas as pd
import numpy as np

# 计算每只股票过去20天的收益率
df['momentum_20d'] = df.groupby('ts_code')['close'].pct_change(20)

# 示例结果：
# 600519.SH  动量：+8.5%  → 打分：85
# 000858.SZ  动量：-3.2%  → 打分：32
# 603121.SH  动量：+12.1% → 打分：121
```

**为什么要标准化？**
> 不同因子的量纲不同（动量是百分比，换手率是次数），需要标准化到同一尺度才能比较。

```python
# Z-Score标准化：让每个因子的均值为0，标准差为1
from scipy import stats
df['momentum_score'] = df.groupby('trade_date')['momentum_20d'].transform(
    lambda x: stats.zscore(x, nan_policy='omit')
)
```

#### 因子2：成交额动量（权重30%）

**逻辑**：成交额放大说明资金在涌入，配合价格上涨说明趋势强劲。

```python
# 过去5天平均成交额 / 过去20天平均成交额
df['vol_ratio'] = (
    df.groupby('ts_code')['amount'].rolling(5).mean() /
    df.groupby('ts_code')['amount'].rolling(20).mean()
).reset_index(level=0, drop=True)

# vol_ratio > 1.5 说明近期资金明显放大
```

**举例**：
```
603121.SH（威帝股份）：
  过去5天日均成交额：2.8亿
  过去20天日均成交额：1.5亿
  vol_ratio = 2.8 / 1.5 = 1.87  → 资金明显涌入，加分 ✅
```

#### 因子3：波动率（权重30%）

**逻辑**：波动率太高的股票风险大，排除极端波动的股票。

```python
# 计算过去20天的收益率标准差
df['volatility_20d'] = df.groupby('ts_code')['close'].pct_change().rolling(20).std()

# 排除波动率最高的20%股票
threshold = df.groupby('trade_date')['volatility_20d'].quantile(0.80)
df = df[df['volatility_20d'] < threshold]
```

#### 综合打分

```python
# 加权综合得分
df['total_score'] = (
    df['momentum_score'] * 0.4 +
    df['vol_ratio_score'] * 0.3 +
    (-df['volatility_score']) * 0.3  # 波动率取负，越低越好
)

# 每天选出得分最高的10只股票
selected = df.groupby('trade_date').nlargest(10, 'total_score')
```

### 因子有效性检验

构建好因子后，不能直接用，要检验它是否真的能预测收益：

**IC（Information Coefficient）分析法**：
```python
# 计算今天的因子值与未来5天收益的相关系数
df['future_return_5d'] = df.groupby('ts_code')['close'].pct_change(5).shift(-5)

# 因子值与未来收益的相关系数
ic = df[['momentum_score', 'future_return_5d']].corr().iloc[0, 1]
# IC > 0.03 认为因子有效（经验值）
```

**IC序列分析**：
```
2026-01-02  IC: 0.045  ✅ 因子今天有效
2026-01-03  IC: 0.038  ✅
2026-01-06  IC: -0.012 ❌ 因子今天失效
2026-01-07  IC: 0.052  ✅
...
平均 IC: 0.032  → 因子整体有效 ✅
IC > 0 的比例: 65%  → 胜率还行
```

> **IC的经验标准**：
> - IC < 0.02：因子几乎无效
> - IC 0.02-0.05：因子有一定预测能力
> - IC > 0.05：因子预测能力较强
> - IC > 0.10：非常强的因子（现实中很少见）

---

## 3. 回测（Backtesting）

### 什么是回测？

回测就是用**历史数据**模拟策略在过去的表现，回答一个问题："如果我从2023年开始用这个策略交易，到现在能赚多少？"

### 场景设定

继续用上面的多因子动量策略：
- 每月末选出综合得分最高的10只股票
- 等权重买入（每只占10%仓位）
- 持有1个月后换仓（卖出旧股，买入新股）

### 回测代码框架

```python
import pandas as pd
import numpy as np

class Backtester:
    def __init__(self, initial_capital=1000000):
        self.capital = initial_capital  # 初始资金100万
        self.positions = {}             # 当前持仓 {股票代码: {数量, 成本, 买入价}}
        self.cash = initial_capital     # 可用现金
        self.history = []               # 每日净值记录

    def run(self, start_date, end_date, rebalance_freq='M'):
        """
        start_date: 回测开始日期
        end_date:   回测结束日期
        rebalance_freq: 换仓频率 M=每月, W=每周
        """
        current_date = start_date

        while current_date <= end_date:
            # 获取当天所有股票的收盘价
            prices = self.get_prices(current_date)

            # 更新持仓市值
            portfolio_value = self.cash
            for stock, pos in self.positions.items():
                portfolio_value += pos['quantity'] * prices[stock]

            # 记录每日净值
            self.history.append({
                'date': current_date,
                'nav': portfolio_value / self.capital,  # 净值=当前总资产/初始资金
                'cash': self.cash,
                'positions': len(self.positions)
            })

            # 检查是否需要换仓（每月第一个交易日）
            if self.should_rebalance(current_date, rebalance_freq):
                self.rebalance(current_date, prices)

            current_date += pd.Timedelta(days=1)

        return pd.DataFrame(self.history)

    def rebalance(self, date, prices):
        """换仓：卖出旧股，买入新股"""
        # 1. 计算当前总资产
        total_value = self.cash
        for stock, pos in self.positions.items():
            total_value += pos['quantity'] * prices[stock]

        # 2. 卖出所有旧持仓
        for stock in list(self.positions.keys()):
            self.sell(stock, prices[stock])

        # 3. 选出得分最高的10只股票
        selected = self.select_stocks(date)  # 返回10只股票代码列表

        # 4. 等权重买入
        weight = 1.0 / len(selected)  # 每只10%
        for stock in selected:
            target_value = total_value * weight
            quantity = int(target_value / prices[stock] / 100) * 100  # A股最少买100股
            self.buy(stock, quantity, prices[stock])

    def buy(self, stock, quantity, price):
        """买入股票"""
        cost = quantity * price * 1.0003  # 手续费约0.03%
        if cost <= self.cash:
            self.positions[stock] = {
                'quantity': quantity,
                'cost': cost,
                'buy_price': price
            }
            self.cash -= cost

    def sell(self, stock, price):
        """卖出股票"""
        pos = self.positions[stock]
        revenue = pos['quantity'] * price * 0.9997  # 手续费约0.03%
        self.cash += revenue
        del self.positions[stock]
```

### 回测过程逐日模拟

**第1天：2023-01-03（第一个交易日）**

```
资金状态：
  总资产：1,000,000
  现金：1,000,000
  持仓：空仓

操作：第一次选股
  综合得分Top 10：
  1. 603121.SH（华培动力）  得分 2.31  → 买入 1000股 × 15.20 = 15,200
  2. 600036.SH（招商银行）  得分 2.15  → 买入 3000股 × 35.80 = 107,400
  3. 601628.SH（中国人寿）  得分 1.98  → 买入 1000股 × 38.50 = 38,500
  4. 000858.SZ（五粮液）    得分 1.87  → 买入  700股 × 138.50 = 96,950
  5. 600519.SH（贵州茅台）  得分 1.76  → 买入   50股 × 1720 = 86,000
  ...（其余5只类似）

  总买入金额：约 997,000（含手续费）
  剩余现金：约 3,000
  每只股票仓位：约 10%
```

**第2天：2023-01-04**

```
市场变化：
  603121.SH  15.20 → 15.35  (+0.99%)
  600036.SH  35.80 → 35.50  (-0.84%)
  601628.SH  38.50 → 38.80  (+0.78%)
  ...

  不操作（未到换仓日）
  总资产：1,001,200（+0.12%）
```

**第20天：2023-01-31（月末换仓日）**

```
持仓情况：
  603121.SH  1000股 × 16.50  市值 16,500  (+8.55%) ✅ 大涨
  600036.SH  3000股 × 34.20  市值 102,600 (-4.47%) ❌
  601628.SH  1000股 × 39.10  市值 39,100  (+1.56%)
  ...

  总资产：1,025,300（+2.53%）本月收益不错

  操作：换仓
  1. 卖出全部10只股票
  2. 重新计算得分，选出新的Top 10
  3. 等权重买入新股

  新持仓：
  1. 002475.SH  得分 2.45  买入...
  2. 600809.SH  得分 2.30  买入...
  ...（可能保留了部分得分仍然高的股票）
```

### 模拟2年的回测结果

```
日期          净值      日收益率    累计收益    回撤
2023-01-03   1.0000    -          0.00%      0.00%
2023-02-03   1.0350    +0.12%     +3.50%     -1.20%
2023-03-03   1.0120    -0.45%     +1.20%     -5.80%  ← 回撤
2023-06-03   1.0890    +0.30%     +8.90%     -2.10%
2023-09-03   1.0650    -0.15%     +6.50%     -8.50%  ← 较大回撤
2023-12-03   1.1520    +0.50%     +15.20%    -0.80%
2024-03-03   1.1830    +0.25%     +18.30%    -3.20%
2024-06-03   1.2100    +0.10%     +21.00%    -1.50%
2024-09-03   1.1750    -0.60%     +17.50%    -7.20%
2024-12-03   1.2850    +0.35%     +28.50%    -0.90%
2025-03-03   1.3520    +0.20%     +35.20%    -2.80%
2025-06-03   1.4200    +0.15%     +42.00%    -1.20%
```

### 回测中必须考虑的细节

#### 1. 交易成本

```python
# A股交易成本构成：
# 佣金：买卖各万2.5（最低5元）
# 印花税：卖出千1（2023年8月28日起）
# 过户费：买卖各万0.1（沪市）/万0.5（深市改万0.1）

def calc_commission(amount, direction='buy'):
    """计算交易成本"""
    commission = max(amount * 0.00025, 5)  # 佣金万2.5，最低5元
    stamp_tax = amount * 0.001 if direction == 'sell' else 0  # 印花税千1
    transfer_fee = amount * 0.00001  # 过户费万0.1
    return commission + stamp_tax + transfer_fee

# 举例：买入10万元招商银行
# 佣金：100000 × 0.00025 = 25元
# 印花税：0（买入不收）
# 过户费：100000 × 0.00001 = 1元
# 总成本：26元（0.026%）

# 卖出10万元招商银行
# 佣金：25元
# 印花税：100000 × 0.001 = 100元
# 过户费：1元
# 总成本：126元（0.126%）

# 一个完整的买卖回合成本：0.026% + 0.126% = 0.152%
# 月度换仓（一年12次）→ 年化成本：1.82%
```

> **如果忽略交易成本**，一个看似年化20%的策略，扣除成本后可能只剩15%甚至更低。

#### 2. 滑点（Slippage）

```python
# 滑点：你看到的价格和你实际成交的价格之间的差
# 原因：下单到成交之间价格可能变化

# 模拟滑点
def simulate_slippage(price, direction='buy'):
    """假设平均滑点为1个tick（0.01元）"""
    if direction == 'buy':
        return price + 0.01  # 买入时实际成交价更高
    else:
        return price - 0.01  # 卖出时实际成交价更低

# 举例：
# 你想以 16.70 买入威帝股份
# 实际成交价：16.71（多花了0.01元/股）
# 买入1000股：多花10元
```

#### 3. 涨跌停无法成交

```python
# A股涨跌停限制：±10%（ST股±5%，科创板/创业板±20%）
# 涨停时你买不到（没人卖），跌停时你卖不出（没人买）

def can_trade(price, prev_close, direction):
    """检查是否能交易"""
    limit_up = prev_close * 1.10   # 涨停价
    limit_down = prev_close * 0.90 # 跌停价

    if direction == 'buy' and price >= limit_up:
        return False  # 涨停，买不到
    if direction == 'sell' and price <= limit_down:
        return False  # 跌停，卖不出
    return True

# 举例：
# 威帝股份昨收 5.34，今日涨停价 5.87
# 策略信号说"买入"，但开盘就涨停了
# → 无法成交，跳过这只股票
```

### 常见回测陷阱

| 陷阱 | 说明 | 后果 |
|------|------|------|
| **过拟合** | 调参调到历史数据完美，实盘失效 | 回测年化50%，实盘亏损20% |
| **未来函数** | 用了当时不可能知道的数据 | 虚假的超额收益 |
| **幸存者偏差** | 只回测现在还存在的股票 | 忽略了退市股的亏损 |
| **忽略流动性** | 买入了成交量很小的股票 | 实盘滑点巨大，无法按预期价格成交 |
| **忽略涨跌停** | 回测中总能买入涨停股 | 实盘中根本买不到 |

---

## 4. 绩效评估

### 为什么要评估？

回测得到了收益数字，但**收益高不等于策略好**。一个年化50%但最大回撤-60%的策略，你大概率坚持不住。绩效评估就是全面衡量策略的"性价比"。

### 核心评估指标

#### 1. 年化收益率（Annualized Return）

```python
def annualized_return(total_return, years):
    """
    total_return: 总收益率（如 0.35 表示 35%）
    years: 回测年数
    """
    return (1 + total_return) ** (1 / years) - 1

# 举例：
# 2年总收益 42%
# 年化收益 = (1.42)^(1/2) - 1 = 19.2%
```

**基准对比**：
```
策略年化收益：19.2%
沪深300年化收益：12.0%
超额收益：7.2%  ✅ 策略跑赢基准
```

#### 2. 最大回撤（Maximum Drawdown）

**最大回撤** = 从历史最高点到最低点的最大跌幅

```
净值曲线：
1.00 → 1.15 → 1.30 → 1.25 → 1.10 → 1.20 → 1.35
                          ↑         ↑
                      局部高1.30  局部低1.10

最大回撤 = (1.10 - 1.30) / 1.30 = -15.38%
```

```python
def max_drawdown(nav_series):
    """计算最大回撤"""
    cummax = nav_series.cummax()         # 历史最高净值
    drawdown = (nav_series - cummax) / cummax  # 每日回撤
    return drawdown.min()               # 最深回撤
```

**实际意义**：
> 最大回撤-15%意味着：如果你在最高点入场，最多要承受15%的亏损。
> 心理承受能力测试：你能接受账户缩水15%吗？20%？30%？

#### 3. 夏普比率（Sharpe Ratio）

**夏普比率 = (策略收益 - 无风险收益) / 策略波动率**

衡量**每承担1单位风险获得多少超额收益**。

```python
def sharpe_ratio(daily_returns, risk_free_rate=0.02):
    """
    daily_returns: 每日收益率序列
    risk_free_rate: 无风险利率（如银行理财约2%）
    """
    excess_returns = daily_returns - risk_free_rate / 252  # 日化无风险利率
    return np.sqrt(252) * excess_returns.mean() / excess_returns.std()

# 举例：
# 策略日均收益 0.08%，波动率 1.2%
# 无风险利率 2%
# 夏普比率 = sqrt(252) × (0.08% - 0.02%/252) / 1.2% = 1.06
```

**夏普比率参考**：
| 夏普比率 | 评价 |
|---------|------|
| < 0.5 | 较差 |
| 0.5 - 1.0 | 一般 |
| 1.0 - 2.0 | 良好 |
| > 2.0 | 优秀（现实中超2.0的很少） |

#### 4. 胜率与盈亏比

```python
# 胜率：盈利交易次数 / 总交易次数
win_rate = winning_trades / total_trades

# 盈亏比：平均每笔盈利 / 平均每笔亏损
profit_loss_ratio = avg_profit_per_trade / avg_loss_per_trade

# 举例：
# 12个月换仓12次
# 盈利8次，亏损4次 → 胜率 66.7%
# 平均盈利：3.2%
# 平均亏损：2.1%
# 盈亏比：1.52
```

**一个有趣的公式**：
```
期望收益 = 胜率 × 平均盈利 - (1-胜率) × 平均亏损

策略A：胜率60%，盈亏比1.5
期望 = 0.6 × 1.5% - 0.4 × 1.0% = 0.5%（每次换仓）

策略B：胜率40%，盈亏比3.0
期望 = 0.4 × 3.0% - 0.6 × 1.0% = 0.6%（每次换仓）
```
> 胜率低不一定是坏事，只要盈亏比够高。

#### 5. Calmar比率

```python
def calmar_ratio(annualized_return, max_drawdown):
    """
    Calmar = 年化收益 / 最大回撤（取正数）
    """
    return annualized_return / abs(max_drawdown)

# 举例：
# 年化收益 19.2%，最大回撤 -15.4%
# Calmar = 19.2 / 15.4 = 1.25

# 参考标准：Calmar > 1.0 算不错，> 2.0 算优秀
```

#### 6. Sortino比率

```python
def sortino_ratio(daily_returns, risk_free_rate=0.02):
    """
    与夏普比率类似，但只惩罚下行波动
    区别：夏普把上涨波动也算作"风险"，Sortino只看下跌波动
    """
    excess_returns = daily_returns - risk_free_rate / 252
    downside_std = excess_returns[excess_returns < 0].std()  # 只算负收益的标准差
    return np.sqrt(252) * excess_returns.mean() / downside_std
```

### 综合评估报告示例

```
╔══════════════════════════════════════════════╗
║          多因子动量策略 回测报告                ║
║          回测期：2023-01 ~ 2025-06             ║
╠══════════════════════════════════════════════╣
║                                              ║
║  收益指标                                     ║
║  ├─ 总收益率：      +42.00%                   ║
║  ├─ 年化收益率：    +19.2%                    ║
║  ├─ 基准收益（沪深300）：+24.0%               ║
║  └─ 超额收益：      +7.2% ✅                  ║
║                                              ║
║  风险指标                                     ║
║  ├─ 年化波动率：    18.5%                     ║
║  ├─ 最大回撤：      -15.4%                    ║
║  └─ 最大回撤天数：  45天                       ║
║                                              ║
║  综合指标                                     ║
║  ├─ 夏普比率：      1.06 ✅                   ║
║  ├─ Sortino比率：   1.42 ✅                   ║
║  ├─ Calmar比率：    1.25 ✅                   ║
║  ├─ 胜率：          66.7%                     ║
║  └─ 盈亏比：        1.52                      ║
║                                              ║
║  交易统计                                     ║
║  ├─ 总交易次数：    30次（12个月 × ~2.5次）    ║
║  ├─ 平均持仓天数：  22天                      ║
║  └─ 年化换手率：    600%                      ║
║                                              ║
║  结论：策略有效，超额收益稳定，但回撤偏大       ║
║  建议：加入止损规则控制回撤                    ║
╚══════════════════════════════════════════════╝
```

---

## 5. 风险管理

### 为什么风险管理是量化交易的生命线？

回测表现再好，如果实盘中一次极端事件把你击穿（爆仓），之前的所有收益都归零。**风险管理确保你能活到明天。**

### 详细举例：构建完整的风险管理体系

#### 1. 单笔止损（Stop Loss）

**规则**：每笔交易的亏损不超过总资金的2%。

```python
class RiskManager:
    def __init__(self, total_capital, max_loss_per_trade=0.02):
        self.total_capital = total_capital
        self.max_loss_per_trade = max_loss_per_trade

    def calc_position_size(self, entry_price, stop_loss_price):
        """
        根据止损位计算最大买入数量
        """
        # 每笔最大亏损金额
        max_loss_amount = self.total_capital * self.max_loss_per_trade
        # 每股亏损 = 买入价 - 止损价
        loss_per_share = entry_price - stop_loss_price
        # 最大买入股数
        max_shares = int(max_loss_amount / loss_per_share / 100) * 100  # A股100股整数倍
        return max_shares

# 举例：
rm = RiskManager(total_capital=1000000)

# 想买威帝股份，买入价 5.34，止损位 4.80
position = rm.calc_position_size(5.34, 4.80)
# max_loss_amount = 1,000,000 × 2% = 20,000
# loss_per_share = 5.34 - 4.80 = 0.54
# max_shares = 20,000 / 0.54 = 37,037 → 取整 37,000股
# 买入金额 = 37,000 × 5.34 = 197,580（约占总资金19.8%）

print(f"最多买入 {position} 股，金额约 {position * 5.34:.0f} 元")
```

#### 2. 仓位上限控制

```python
class RiskManager:
    # ...（续上面的类）

    def check_position_limit(self, new_position_value):
        """
        检查单只股票仓位是否超过上限
        """
        max_single_position = self.total_capital * 0.20  # 单只股票不超过20%
        if new_position_value > max_single_position:
            print(f"⚠️ 仓位超限！{new_position_value} > {max_single_position}")
            return False
        return True

    def check_total_exposure(self, current_positions_value):
        """
        检查总仓位是否超过上限
        """
        max_exposure = self.total_capital * 0.80  # 总仓位不超过80%，留20%现金
        if current_positions_value > max_exposure:
            print(f"⚠️ 总仓位超限！")
            return False
        return True
```

**举例**：
```
总资金100万，持仓情况：
  威帝股份：197,580（19.8%）✅ < 20%
  招商银行：180,000（18.0%）✅
  中国人寿：150,000（15.0%）✅
  五粮液：  120,000（12.0%）✅
  ...（更多持仓）
  总仓位：780,000（78%）✅ < 80%
  现金：  220,000（22%）→ 安全垫充足
```

#### 3. 组合相关性控制

```python
import numpy as np

def check_correlation(positions, returns_data):
    """
    检查持仓股票之间的相关性
    目标：持仓股票之间的平均相关系数不超过 0.6
    """
    # 获取持仓股票的收益率序列
    stock_returns = returns_data[list(positions.keys())]

    # 计算相关系数矩阵
    corr_matrix = stock_returns.corr()

    # 取上三角（避免重复计算）
    upper_triangle = corr_matrix.values[np.triu_indices(len(corr_matrix), k=1)]
    avg_corr = upper_triangle.mean()

    print(f"持仓平均相关性：{avg_corr:.2f}")

    if avg_corr > 0.6:
        print("⚠️ 持仓高度相关，分散度不足！")
        print("建议：减少同板块股票，加入不同行业")
        return False
    return True

# 举例：
# 持仓：招商银行、工商银行、建设银行、中国银行
# 平均相关性：0.85 ❌ 太高了！
# 问题：四只全是银行股，大盘跌一起跌
# 解决：卖出两只银行股，换成科技股和消费股

# 调整后持仓：
# 招商银行、贵州茅台、宁德时代、比亚迪、迈瑞医疗
# 平均相关性：0.35 ✅ 分散度良好
```

#### 4. 动态止损（Trailing Stop）

```python
def trailing_stop(positions, current_prices, atr_values, multiplier=2.0):
    """
    基于ATR（平均真实波幅）的动态止损
    止损位 = 最高价 - multiplier × ATR
    """
    stop_orders = {}

    for stock, pos in positions.items():
        highest = pos['highest_since_buy']  # 持仓期间最高价
        atr = atr_values[stock]             # 当前ATR值
        stop_price = highest - multiplier * atr

        if current_prices[stock] <= stop_price:
            stop_orders[stock] = {
                'action': 'SELL',
                'reason': f'触及动态止损 {stop_price:.2f}（ATR={atr:.2f}）'
            }
        else:
            # 更新止损位（只能上移，不能下移）
            if stop_price > pos.get('stop_loss', 0):
                pos['stop_loss'] = stop_price

    return stop_orders

# 举例：威帝股份持仓
# 买入价：5.34
# ATR(14)：0.15
# 持仓期间最高价：5.80
#
# 止损位 = 5.80 - 2 × 0.15 = 5.50
#
# Day 1: 价格 5.80 → 止损 5.50（保护利润 +3.0%）
# Day 2: 价格 5.90 → 最高 5.90 → 止损上移到 5.60
# Day 3: 价格 5.45 → 触发止损 5.50！卖出 ✅
#
# 结果：盈利 (5.50 - 5.34) / 5.34 = +3.0%
# 如果没有动态止损，可能一直拿到 4.70（亏损-12%）
```

#### 5. 大盘熔断机制

```python
def market_circuit_breaker(index_price, index_ma20, index_ma60):
    """
    当大盘跌破关键均线时，停止买入
    """
    if index_price < index_ma20:
        print("⚠️ 大盘跌破20日均线，暂停新开仓")
        return 'pause_new'  # 不开新仓，但不止盈止损

    if index_price < index_ma60:
        print("🚨 大盘跌破60日均线，全部清仓")
        return 'close_all'  # 清空所有仓位

    return 'normal'  # 正常交易

# 举例：
# 2026-03-20：
# 上证指数 3,957（跌破20日均线 3,980）
# → 触发 pause_new，不再买入新股票
# 但威帝股份 4.70 已经跌破止损位 4.80
# → 程序自动执行止损卖出
#
# 如果当天程序还在运行，就不会犯 3/20 没止损的错误
```

### 风险管理规则总结

```
╔══════════════════════════════════════════════════╗
║              风险管理体系                           ║
╠══════════════════════════════════════════════════╣
║                                                  ║
║  单笔风险                                         ║
║  ├─ 单笔最大亏损：≤ 总资金 2%                     ║
║  ├─ 止损方式：固定止损 + ATR动态止损               ║
║  └─ 止损执行：程序自动，不依赖人工                  ║
║                                                  ║
║  仓位管理                                         ║
║  ├─ 单股上限：≤ 总资金 20%                        ║
║  ├─ 总仓位上限：≤ 总资金 80%                      ║
║  └─ 现金比例：≥ 总资金 20%                        ║
║                                                  ║
║  组合风险                                         ║
║  ├─ 持仓相关性：≤ 0.6（平均）                     ║
║  ├─ 行业集中度：单一行业 ≤ 30%                    ║
║  └─ 大盘保护：跌破MA20暂停，跌破MA60清仓          ║
║                                                  ║
║  极端风险                                         ║
║  ├─ 单日亏损 ≤ 5% → 减仓至50%                    ║
║  ├─ 单日亏损 ≤ 8% → 清仓                         ║
║  └─ 连续亏损3天 → 暂停交易1天                     ║
║                                                  ║
╚══════════════════════════════════════════════════╝
```

---

## 6. 实盘交易执行

### 从回测到实盘的跨越

回测只是"纸上谈兵"，实盘才是真金白银。这一步是**量化交易最关键、也最容易出现问题的一步**。

### 实盘交易平台选择

| 平台 | 类型 | 费用 | 特点 |
|------|------|------|------|
| **QMT（迅投）** | 券商终端 | 免费（开户即用） | 功能全面，支持Python |
| **Ptrade（恒生）** | 券商终端 | 免费（部分券商） | 速度快，适合高频 |
| **聚宽（JoinQuant）** | 在线平台 | 免费/付费 | 适合初学者，社区活跃 |
| **米筐（RiceQuant）** | 在线平台 | 付费 | 机构级回测质量 |
| **vn.py / 开源框架** | 自建系统 | 免费 | 灵活但需要开发能力 |

### 场景设定：用 QMT 实盘执行

假设你选择了某券商的 QMT 终端，将策略部署上去。

#### Step 1：环境搭建

```python
# QMT 中的 Python 环境（与普通Python基本一致）
# 在QMT中创建一个新的策略文件：momentum_strategy.py

from xtquant import xttrader  # QMT交易接口
from xtquant import xtdata    # QMT数据接口

# 初始化连接
trader = xttrader.XtQuantTrader(path='/QMT/userdata_mini', session_id=12345)
trader.start()

# 连接资金账号
account_id = xttrader.StockAccount('你的资金账号')
trader.register_stock_account(account_id)
```

#### Step 2：实盘策略主循环

```python
import time
from datetime import datetime, time as dt_time

class LiveStrategy:
    def __init__(self, trader, account_id, risk_manager):
        self.trader = trader
        self.account = account_id
        self.rm = risk_manager
        self.positions = {}
        self.last_rebalance = None

    def run(self):
        """策略主循环"""
        while True:
            now = datetime.now()

            # 只在交易时间运行（9:30-15:00）
            if not self.is_trading_time(now):
                time.sleep(10)
                continue

            try:
                # 每分钟检查一次
                self.on_bar()

            except Exception as e:
                self.log_error(f"策略异常：{e}")
                time.sleep(60)

            time.sleep(60)  # 每分钟执行一次

    def on_bar(self):
        """每分钟执行的逻辑"""

        # 1. 获取当前持仓
        self.sync_positions()

        # 2. 检查止损（优先级最高！）
        self.check_stop_loss()

        # 3. 检查是否需要换仓（每月第一个交易日）
        if self.should_rebalance():
            self.rebalance()

    def check_stop_loss(self):
        """检查并执行止损"""
        for stock, pos in self.positions.items():
            # 获取最新价
            current_price = xtdata.get_full_tick([stock])[stock]['lastPrice']

            # 检查固定止损
            if current_price <= pos['stop_loss']:
                self.log(f"🚨 触发止损！{stock} 价格 {current_price} ≤ 止损 {pos['stop_loss']}")
                self.send_order(stock, -pos['quantity'], current_price, 'sell')
                print(f"已发送卖出订单：{stock} {pos['quantity']}股 @ {current_price}")

                # 同时通知
                self.send_notification(
                    f"⚠️ 止损触发\n"
                    f"股票：{stock}\n"
                    f"当前价：{current_price}\n"
                    f"止损位：{pos['stop_loss']}\n"
                    f"买入价：{pos['entry_price']}\n"
                    f"亏损：{(current_price - pos['entry_price'])/pos['entry_price']:.2%}"
                )
```

#### Step 3：下单执行

```python
    def send_order(self, stock_code, quantity, price, direction):
        """发送订单"""
        order = xttrader.XtOrder(
            stock_code=stock_code,
            order_type=xttrader.MARKET_ORDER,  # 市价单（确保成交）
            order_volume=abs(quantity),
            price_type=xttrader.FIX_PRICE,
            price=price
        )

        if quantity > 0:  # 买入
            order_id = self.trader.order_stock(
                self.account, stock_code,
                xttrader.STOCK_BUY, abs(quantity),
                xttrader.MARKET_ORDER, price, strategy_name='momentum',
                order_remark='动量策略买入'
            )
        else:  # 卖出
            order_id = self.trader.order_stock(
                self.account, stock_code,
                xttrader.STOCK_SELL, abs(quantity),
                xttrader.MARKET_ORDER, price, strategy_name='momentum',
                order_remark='动量策略止损'
            )

        self.log(f"订单已发送：order_id={order_id} {direction} {stock_code} {abs(quantity)}股")
        return order_id
```

#### Step 4：订单确认与异常处理

```python
    def on_order_callback(self, order):
        """订单状态回调"""
        order_id = order.order_id
        status = order.order_status

        if status == xttrader.ORDER_SUCCEEDED:
            self.log(f"✅ 订单成交：{order.stock_code} {order.traded_volume}股 @ {order.traded_price}")
        elif status == xttrader.ORDER_CANCELED:
            self.log(f"❌ 订单被撤销：{order.stock_code}")
            # 检查是否需要重新下单
        elif status == xttrader.ORDER_REJECTED:
            self.log(f"⚠️ 订单被拒绝：{order.stock_code} 原因：{order.order_remark}")
            # 涨跌停无法成交等

    def on_trade_callback(self, trade):
        """成交回调"""
        self.log(
            f"💰 成交确认：{trade.stock_code} "
            f"{trade.traded_volume}股 @ {trade.traded_price} "
            f"{'买入' if trade.trade_type == 'buy' else '卖出'}"
        )

        # 更新持仓记录
        if trade.trade_type == 'buy':
            self.positions[trade.stock_code] = {
                'quantity': trade.traded_volume,
                'entry_price': trade.traded_price,
                'stop_loss': trade.traded_price * 0.95,  # 默认-5%止损
                'entry_time': datetime.now()
            }
        else:
            if trade.stock_code in self.positions:
                del self.positions[trade.stock_code]
```

#### Step 5：日志与通知

```python
    def log(self, message):
        """记录日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_msg = f"[{timestamp}] {message}"
        print(log_msg)
        with open('/QMT/strategy/logs/momentum.log', 'a') as f:
            f.write(log_msg + '\n')

    def send_notification(self, message):
        """发送通知到飞书/微信/钉钉"""
        import requests
        # 飞书 Webhook
        webhook_url = 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx'
        requests.post(webhook_url, json={
            "msg_type": "text",
            "content": {"text": f"[量化策略] {message}"}
        })
```

### 实盘运行示例（某一天）

```
[2026-03-30 09:30:01] 策略启动，检查持仓...
[2026-03-30 09:30:02] 当前持仓：5只股票
[2026-03-30 09:30:03] 检查止损...
[2026-03-30 09:30:05] 603121.SH 当前价 16.70，止损位 16.10 → 安全 ✅
[2026-03-30 09:30:05] 600036.SH 当前价 38.50，止损位 36.60 → 安全 ✅
[2026-03-30 09:30:06] 601628.SH 当前价 36.66，止损位 35.80 → 安全 ✅
[2026-03-30 09:30:06] 今天不是换仓日，等待...

[2026-03-30 14:25:03] 603121.SH 当前价 16.15，止损位 16.10 → 接近止损 ⚠️
[2026-03-30 14:26:01] 603121.SH 当前价 16.08，止损位 16.10 → 触发止损！🚨
[2026-03-30 14:26:02] 发送卖出订单：603121.SH 400股 市价单
[2026-03-30 14:26:03] 已发送通知到飞书 ⚠️ 止损触发
[2026-03-30 14:26:05] ✅ 订单成交：603121.SH 400股 @ 16.06
[2026-03-30 14:26:05] 亏损：(16.06 - 19.73) / 19.73 = -18.60%
[2026-03-30 14:26:06] 已更新持仓：4只股票
```

---

## 7. 监控与迭代优化

### 为什么要监控？

市场是变化的。一个2023年有效的策略，到2025年可能完全失效（市场环境变化、参与者结构变化、监管政策变化等）。持续监控能让你及时发现问题。

### 监控仪表盘

```python
class StrategyMonitor:
    def __init__(self, strategy_name):
        self.strategy_name = strategy_name
        self.daily_pnl = []       # 每日盈亏
        self.trades = []          # 所有交易记录
        self.alerts = []          # 告警记录

    def daily_report(self):
        """生成每日报告"""
        today = datetime.now().strftime('%Y-%m-%d')

        # 计算今日盈亏
        today_pnl = self.calc_today_pnl()

        # 计算累计指标
        total_return = (current_nav - initial_nav) / initial_nav
        max_dd = self.calc_max_drawdown()
        sharpe = self.calc_sharpe()
        win_rate = self.calc_win_rate()

        report = f"""
╔══════════════════════════════════════╗
║  {self.strategy_name} 每日报告        ║
║  日期：{today}                         ║
╠══════════════════════════════════════╣
║  今日盈亏：{today_pnl:+.2%}              ║
║  累计收益：{total_return:+.2%}             ║
║  当前净值：{current_nav/initial_nav:.4f}       ║
║  最大回撤：{max_dd:.2%}                 ║
║  夏普比率：{sharpe:.2f}                 ║
║  胜率：    {win_rate:.1%}                ║
║  持仓数量：{len(self.positions)}只           ║
║  总仓位：  {self.total_exposure:.1%}        ║
╚══════════════════════════════════════╝
        """
        return report
```

### 策略失效检测

```python
def check_strategy_health(daily_returns, benchmark_returns, lookback=60):
    """
    检测策略是否健康
    如果最近60个交易日表现显著差于基准，发出警告
    """
    # 最近60天的策略收益 vs 基准收益
    strategy_cumulative = (1 + daily_returns.tail(60)).cumprod() - 1
    benchmark_cumulative = (1 + benchmark_returns.tail(60)).cumprod() - 1

    # 跟踪误差（策略与基准的偏差）
    tracking_error = (daily_returns - benchmark_returns).tail(60).std() * np.sqrt(252)

    # 信息比率
    excess_return = strategy_cumulative - benchmark_cumulative
    information_ratio = excess_return / tracking_error if tracking_error > 0 else 0

    # 诊断
    if information_ratio < -0.5:
        return "🚨 策略严重跑输基准，建议暂停并分析原因"
    elif information_ratio < -0.2:
        return "⚠️ 策略短期跑输基准，密切观察"
    elif information_ratio < 0.2:
        return "😐 策略表现与基准持平"
    else:
        return "✅ 策略跑赢基准，运行正常"

# 举例：
# 最近2个月策略收益 -5%，沪深300收益 +3%
# 跟踪误差：15%
# IR = (-5% - 3%) / 15% = -0.53
# → 🚨 策略严重跑输，暂停！
```

### 策略迭代优化流程

```
发现策略表现下滑
    ↓
分析原因：
  ├─ 市场环境变化？（牛转熊、政策调整）
  ├─ 因子失效？（动量因子不再有效）
  ├─ 持仓拥挤？（太多人用类似策略）
  ├─ 代码bug？（数据源问题、下单逻辑错误）
  └─ 交易成本增加？（滑点变大、手续费调整）
    ↓
针对性优化：
  ├─ 市场环境变化 → 加入市场状态判断模块
  ├─ 因子失效 → 更新因子或替换新因子
  ├─ 持仓拥挤 → 降低换仓频率或分散因子
  ├─ 代码bug → 修复代码
  └─ 交易成本 → 优化下单算法（TWAP/VWAP）
    ↓
重新回测验证
    ↓
小资金试运行1-3个月
    ↓
确认有效后恢复正常仓位
```

### 具体优化举例

#### 优化1：加入市场状态判断

```python
def market_regime(index_data):
    """判断当前市场状态"""
    ma20 = index_data['close'].rolling(20).mean()
    ma60 = index_data['close'].rolling(60).mean()

    current = index_data['close'].iloc[-1]

    if current > ma20 > ma60:
        return 'BULL'     # 牛市：均线多头排列
    elif current < ma20 < ma60:
        return 'BEAR'     # 熊市：均线空头排列
    else:
        return 'RANGE'    # 震荡：均线缠绕

# 不同市场状态使用不同策略参数：
regime = market_regime(shanghai_index)
params = {
    'BULL':  {'momentum_weight': 0.5, 'lookback': 20},   # 牛市加强动量
    'BEAR':  {'momentum_weight': 0.2, 'lookback': 10},   # 熊市减弱动量
    'RANGE': {'momentum_weight': 0.3, 'lookback': 15},   # 震荡市中性
}
```

#### 优化2：VWAP下单算法

```python
def vwap_order(stock_code, total_quantity, duration_minutes=30):
    """
    VWAP（成交量加权平均价）下单
    将大单拆成小单，在一段时间内均匀买入
    减少滑点和冲击成本
    """
    slices = 6  # 分6笔
    quantity_per_slice = total_quantity // slices
    interval = duration_minutes * 60 / slices  # 每笔间隔秒数

    for i in range(slices):
        current_price = get_current_price(stock_code)
        order_id = send_limit_order(stock_code, quantity_per_slice, current_price)
        log(f"VWAP 第{i+1}/{slices}笔：{quantity_per_slice}股 @ {current_price}")

        if i < slices - 1:
            time.sleep(interval)

    avg_price = calc_average_execution_price(order_ids)
    log(f"VWAP执行完成：{total_quantity}股，均价 {avg_price}")

# 举例：
# 要买入 10,000 股招商银行
# 如果一次性市价单买入：成交价可能被推高到 38.80（目标 38.50）
# 用VWAP分6笔，30分钟内均匀买入：均价 38.53
# 节省滑点：0.27元/股 × 10,000 = 2,700元
```

---

## 总结：完整流程图

```
┌─────────────────────────────────────────────────────────┐
│                   量化交易完整流程                         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ① 数据获取与清洗                                        │
│     获取行情/财务数据 → 清洗缺失/异常/复权/停牌 → 存储     │
│                    ↓                                    │
│  ② 因子开发与策略构思                                     │
│     构建因子 → 标准化 → IC检验 → 组合成策略信号            │
│                    ↓                                    │
│  ③ 回测                                                 │
│     用历史数据模拟交易 → 扣除成本/滑点 → 考虑涨跌停        │
│                    ↓                                    │
│  ④ 绩效评估                                             │
│     年化收益/最大回撤/夏普比率/胜率/盈亏比                 │
│                    ↓                                    │
│  ⑤ 风险管理                                             │
│     止损/仓位控制/相关性管理/大盘保护/动态调整              │
│                    ↓                                    │
│  ⑥ 实盘执行                                             │
│     部署到交易平台 → 自动下单 → 订单管理 → 异常处理        │
│                    ↓                                    │
│  ⑦ 监控与迭代                                           │
│     每日报告 → 策略健康检测 → 分析原因 → 优化迭代          │
│                    ↓                                    │
│     └──────→ 回到②，循环往复                             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 附录：常用工具与资源

### Python 量化库

| 库名 | 用途 |
|------|------|
| `pandas` | 数据处理（量化必备） |
| `numpy` | 数值计算 |
| `tushare` | A股数据接口 |
| `akshare` | 开源金融数据接口 |
| `backtrader` | 回测框架 |
| `zipline` | Quantopian开源回测框架 |
| `vnpy` | 开源量化交易框架 |
| `ta-lib` | 技术指标计算库 |
| `empyrical` | 绩效评估指标库 |

### 学习资源

- **书籍**：《量化交易：如何建立自己的算法交易事业》（Ernest Chan）
- **书籍**：《打开量化投资的黑箱》（Rishi Narang）
- **在线**：聚宽研究社区（joinquant.com）
- **在线**：米筐研究笔记（ricequant.com）

---

*文档生成时间：2026-03-30*
*作者：周六（AI助手）*

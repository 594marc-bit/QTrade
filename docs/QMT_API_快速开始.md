# QMT 极速策略交易系统 — API 快速开始

> 来源: 迅投知识库 `dict.thinktrader.net/innerApi/start_now.html`
> 最后更新: 2025/5/15

---

## 一、概述

QMT 极速策略交易系统内置了 **3.6 版本**的 Python 运行环境，提供**行情数据**与**交易下单**两大核心功能。

---

## 二、场景需求

QMT 支持**回测模型**与**实盘模型**：

- **回测模型**: 在历史 K 线上自左向右逐根遍历，以模拟资金账号记录买卖信号、持仓盈亏，展示净值走势
- **实盘模型**: 盘中收取最新动态行情，即时发送买卖信号到交易所，判断委托状态

### 回测模型注意事项

1. 首先下载历史行情：`操作` → `数据管理` → 选择周期(如`日线`) → 板块(如`沪深A股`) → `全部`
2. 设置每日定时更新：右下角`行情` → `批量下载` → 勾选`定时下载`
3. 回测取本地数据，使用 `get_market_data_ex`，指定 `subscribe=False`
4. 撮合规则：指定价格在K线高低点间按指定价撮合，超出按收盘价撮合，数量不足按可用数量
5. 回测必须以**副图模式**执行

### 实盘模型注意事项

**两种交易模式：**

**模式一：逐K线生效** (`quickTrade=0`，默认)
- 下单函数放在 `handlebar` 内
- 盘中每个分笔(3秒)触发一次 `handlebar`
- 信号在当前K线最后一个分笔发出，之前分笔的信号被丢弃
- 交易记录可保存在 `ContextInfo` 对象属性中

**模式二：立即下单** (`quickTrade=2`)
- 立刻发出委托，不等待K线完成
- **必须用普通全局变量**保存状态（如 `class a()`），不能存在 `ContextInfo` 属性里

实盘撮合以交易所为准：股票价格不能超过2%价格笼子，数量超可用会废单。

运行模式可选`模拟`或`实盘`，与账号实际是实盘/模拟柜台无关。

---

## 三、运行机制对比

QMT 提供两大类（事件驱动 + 定时任务），三种运行机制：

| 机制 | 分类 | 特点 | 匹配需求 |
|------|------|------|----------|
| 逐K线运行 `handlebar` | 事件驱动 | 同时支持历史回测和盘中模拟逐K线 | 模拟逐K线效果 |
| 订阅推送 `subscribe` | 事件驱动 | 盘中行情分笔触发回调 | 随分笔行情判断交易 |
| 定时运行 `run_time` | 定时任务 | 固定间隔触发回调 | 固定时间间隔判断交易 |

---

## 四、逐K线驱动（handlebar）示例

**重要规则：**
- 代码第一行必须写 `#coding:gbk`
- 缩进统一

### 回测示例 — handlebar

```python
#coding:gbk
import pandas as pd
import numpy as np
import talib

# 双均线策略：快慢均线金叉买入，死叉卖出

def init(C):
    C.stock = C.stockcode + '.' + C.market
    C.line1 = 10   # 快线
    C.line2 = 20   # 慢线
    C.accountid = "testS"

def handlebar(C):
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    local_data = C.get_market_data_ex(['close'], [C.stock],
        end_time=bar_date, period=C.period,
        count=max(C.line1, C.line2), subscribe=False)
    close_list = list(local_data[C.stock].iloc[:, 0])

    line1_mean = round(np.mean(close_list[-C.line1:]), 2)
    line2_mean = round(np.mean(close_list[-C.line2:]), 2)

    # 查询账户和持仓
    account = get_trade_detail_data('test', 'stock', 'account')[0]
    available_cash = int(account.m_dAvailable)
    holdings = get_trade_detail_data('test', 'stock', 'position')
    holdings = {i.m_strInstrumentID + '.' + i.m_strExchangeID: i.m_nVolume for i in holdings}
    holding_vol = holdings.get(C.stock, 0)

    # 金叉买入
    if holding_vol == 0 and line1_mean > line2_mean:
        vol = int(available_cash / close_list[-1] / 100) * 100
        passorder(23, 1101, C.accountid, C.stock, 5, -1, vol, C)
        print(f"{bar_date} 开仓")

    # 死叉卖出
    elif holding_vol > 0 and line1_mean < line2_mean:
        passorder(24, 1101, C.accountid, C.stock, 5, -1, holding_vol, C)
        print(f"{bar_date} 平仓")
```

### 实盘示例 — handlebar（立即下单模式）

```python
#coding:gbk
import pandas as pd
import numpy as np
import datetime

"""
双均线实盘策略 — 立即下单模式 (quickTrade=2)
"""

class a():
    pass

A = a()  # 用全局类实例保存委托状态，不能用ContextInfo

def init(C):
    A.stock = C.stockcode + '.' + C.market
    A.acct = account
    A.acct_type = accountType
    A.amount = 10000        # 单笔买入金额
    A.line1 = 17            # 快线周期
    A.line2 = 27            # 慢线周期
    A.waiting_list = []     # 未查到委托列表
    # 区分股票/两融账号
    A.buy_code = 23 if A.acct_type == 'STOCK' else 33
    A.sell_code = 24 if A.acct_type == 'STOCK' else 34

def handlebar(C):
    # 跳过历史K线
    if not C.is_last_bar():
        return

    now = datetime.datetime.now()
    now_time = now.strftime('%H%M%S')
    # 跳过非交易时间
    if now_time < '093000' or now_time > "150000":
        return

    # 查询账户
    account = get_trade_detail_data(A.acct, A.acct_type, 'account')
    if len(account) == 0:
        print(f'账号{A.acct} 未登录')
        return
    account = account[0]
    available_cash = int(account.m_dAvailable)

    # 如果有未查到成交的，先查询成交
    if A.waiting_list:
        found_list = []
        deals = get_trade_detail_data(A.acct, A.acct_type, 'deal')
        for deal in deals:
            if deal.m_strRemark in A.waiting_list:
                found_list.append(deal.m_strRemark)
        A.waiting_list = [i for i in A.waiting_list if i not in found_list]
        if A.waiting_list:
            return  # 暂停后续报单

    # 查询持仓
    holdings = get_trade_detail_data(A.acct, A.acct_type, 'position')
    holdings = {i.m_strInstrumentID + '.' + i.m_strExchangeID: i.m_nCanUseVolume for i in holdings}

    # 获取行情
    data = C.get_market_data_ex(["close"], [A.stock], period='1d', count=max(A.line1, A.line2)+1)
    close_list = data[A.stock].values

    pre_line1 = np.mean(close_list[-A.line1-1: -1])
    pre_line2 = np.mean(close_list[-A.line2-1: -1])
    current_line1 = np.mean(close_list[-A.line1:])
    current_line2 = np.mean(close_list[-A.line2:])

    # 金叉买入
    vol = int(A.amount / close_list[-1] / 100) * 100
    if (A.amount < available_cash and vol >= 100
        and A.stock not in holdings
        and pre_line1 < pre_line2 and current_line1 > current_line2):
        msg = f"双均线实盘 {A.stock} 上穿均线 买入 {vol}股"
        passorder(A.buy_code, 1101, A.acct, A.stock, 14, -1, vol, '双均线实盘', 2, msg, C)
        print(msg)
        A.waiting_list.append(msg)

    # 死叉卖出
    if (A.stock in holdings and holdings[A.stock] > 0
        and pre_line1 > pre_line2 and current_line1 < current_line2):
        msg = f"双均线实盘 {A.stock} 下穿均线 卖出 {holdings[A.stock]}股"
        passorder(A.sell_code, 1101, A.acct, A.stock, 14, -1, holdings[A.stock], '双均线实盘', 2, msg, C)
        print(msg)
        A.waiting_list.append(msg)
```

> **警告**: 立即下单模式必须用普通全局变量保存状态，不能用 `ContextInfo` 存。

---

## 五、事件驱动（subscribe）示例

### 实盘示例 — subscribe

```python
#coding:gbk

class a(): pass
A = a()
A.bought_list = []
account = 'testaccount'

def init(C):
    def callback_func(data):
        for stock in data:
            current_price = data[stock]['close']
            pre_price = data[stock]['preClose']
            ratio = current_price / pre_price - 1
            print(stock, C.get_stock_name(stock), '当前涨幅', ratio)
            if ratio > 0 and stock not in A.bought_list:
                msg = f"当前涨幅 {ratio} 大于0 买入100股"
                print(msg)
                # 实际下单时取消注释:
                # passorder(23, 1101, account, stock, 5, -1, 100, '订阅下单示例', 2, msg, C)
                A.bought_list.append(stock)

    stock_list = ['600000.SH', '000001.SZ']
    for stock in stock_list:
        C.subscribe_quote(stock, period='1d', callback=callback_func)
```

---

## 六、定时任务（run_time）示例

### 实盘示例 — run_time

```python
#coding:gbk
import time, datetime

class a():
    pass
A = a()

def init(C):
    A.hsa = C.get_stock_list_in_sector('沪深A股')
    A.vol_dict = {}
    for stock in A.hsa:
        A.vol_dict[stock] = C.get_last_volume(stock)
    A.bought_list = []
    # run_time(回调函数名, 时间间隔, 起始时间)
    C.run_time("f", "1nSecond", "2019-10-14 13:20:00")

def f(C):
    t0 = time.time()
    now = datetime.datetime.now()
    full_tick = C.get_full_tick(A.hsa)

    total_market_value = 0
    total_ratio = 0
    count = 0

    for stock in A.hsa:
        ratio = full_tick[stock]['lastPrice'] / full_tick[stock]['lastClose'] - 1
        if ratio > 0.09 and stock not in A.bought_list:
            msg = f"{now} {stock} {C.get_stock_name(stock)} 涨幅>9% 买入100股"
            # 实际下单时取消注释:
            # passorder(23, 1101, account, stock, 5, -1, 100, '示例策略', 2, msg, C)
            A.bought_list.append(stock)

        market_value = full_tick[stock]['lastPrice'] * A.vol_dict[stock]
        total_ratio += ratio * market_value
        total_market_value += market_value
        count += 1

    total_ratio /= total_market_value
    total_ratio *= 100
    print(f'{now} A股加权涨幅 {round(total_ratio, 2)}% 耗时{round(time.time()-t0, 5)}秒')
```

---

## 关键 API 速查

### passorder() — 下单函数

```
passorder(opType, orderType, accountID, stockCode, prType,
          price, volume, strategyName, quickTrade, remark, ContextInfo)
```

| 参数 | 说明 | 常用值 |
|------|------|--------|
| opType | 操作类型 | `23`=股票买入, `24`=股票卖出, `33`=两融买入, `34`=两融卖出 |
| orderType | 委托类型 | `1101`=按股数 |
| accountID | 资金账号 | `ContextInfo.accID` 或 `g.accID` |
| stockCode | 股票代码 | 如 `'600036.SH'` |
| prType | 价格类型 | `5`=最新价, `11`=限价, `14`=卖一价 |
| price | 价格 | 市价时填 `-1` |
| volume | 数量(股) | 100 的整数倍 |
| strategyName | 策略名 | 任意字符串 |
| quickTrade | 快速交易 | `0`=逐K线, `2`=立即下单 |
| remark | 备注 | 可用于后续查成交 |
| ContextInfo | 上下文 | 直接传入 `C` |

### run_time() — 定时任务

```python
ContextInfo.run_time("回调函数名", "时间间隔", "起始时间")
```

- 第一个参数：回调函数名字符串
- 第二个参数：时间间隔，如 `"1nSec"`, `"60nSec"`, `"1nMilliSec"`
- 第三个参数：起始时间，填 `"2019-10-14 13:20:00"` 格式，或 `""` 表示立即开始
- **注意**：`run_time` 是一次性的，需要在回调末尾重新注册才能循环

### ContextInfo 常用属性/方法

| 属性/方法 | 说明 |
|-----------|------|
| `C.stockcode` | 主图品种代码 |
| `C.market` | 主图市场 |
| `C.accID` | 资金账号 |
| `C.barpos` | 当前K线位置 |
| `C.is_last_bar()` | 是否最后一根K线(实盘判断) |
| `C.get_bar_timetag(n)` | 获取第n根K线的时间 |
| `C.get_stock_list_in_sector('沪深A股')` | 获取板块成分股 |
| `C.get_market_data_ex(fields, stocks, ...)` | 获取行情数据 |
| `C.get_full_tick(stock_list)` | 获取全推行情 |
| `C.get_last_volume(stock)` | 获取最新成交量 |
| `C.subscribe_quote(stock, period, callback)` | 订阅行情 |
| `C.run_time(func, interval, start)` | 注册定时任务 |
| `C.draw_text(row, col, text)` | 在K线图上绘制文字 |

### 其他全局函数

| 函数 | 说明 |
|------|------|
| `get_trade_detail_data(account, type, 'account')` | 查询账户信息 |
| `get_trade_detail_data(account, type, 'position')` | 查询持仓 |
| `get_trade_detail_data(account, type, 'deal')` | 查询成交 |
| `timetag_to_datetime(timetag, format)` | 时间戳转日期时间 |

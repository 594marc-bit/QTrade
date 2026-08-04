# QTrade + 大QMT 自动化量化交易系统

## 系统架构总览

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           QTrade + QMT 自动化量化交易系统                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────┐                                                │
│  │      第一层：数据层        │                                                │
│  │                          │                                                │
│  │  ┌──────────┐ ┌────────┐ │   AKShare / Tushare / 腾讯财经 API              │
│  │  │ 日线行情   │ │ 基本面  │ │   (开盘/高/低/收/量/额)                         │
│  │  │ daily_price│ │ PE PB  │ │   复权因子、行业分类、财务指标                   │
│  │  └──────────┘ └────────┘ │                                                │
│  │  ┌──────────┐ ┌────────┐ │   SQLite 持久化 (data/stock_data.db)           │
│  │  │ 财务指标   │ │ 复权因子 │ │   增量UPSERT，秒级同步                          │
│  │  │ ROE ROE_YoY│ │adj_factor│ │                                              │
│  │  └──────────┘ └────────┘ │                                                │
│  └────────────┬─────────────┘                                                │
│               │                                                              │
│               ▼                                                              │
│  ┌──────────────────────────┐                                                │
│  │    第二层：策略 & 回测 & 信号生成 │                                          │
│  │                          │                                                │
│  │  ┌──────────┐ ┌────────┐ │   18+ 因子计算 (动量/估值/波动率/ROE/流动性...)    │
│  │  │ 因子计算   │ │ 数据清洗 │ │   数据清洗 (缺失值/极端值/ST/新股过滤)           │
│  │  └──────────┘ └────────┘ │                                                │
│  │  ┌──────────┐ ┌────────┐ │   综合评分 (标准化 + 加权 + 排名)                  │
│  │  │ 综合评分   │ │ IC 分析 │ │   IC 分析评估因子有效性                          │
│  │  └──────────┘ └────────┘ │                                                │
│  │  ┌──────────┐ ┌────────┐ │   schemes.yaml 管理因子组合与权重                 │
│  │  │ 回测引擎   │ │方案管理  │ │   风控: 止损/止盈/回撤止损/冷冻期                │
│  │  └──────────┘ └────────┘ │                                                │
│  │  ┌──────────────────┐    │                                                │
│  │  │  信号生成器        │    │   调仓日 Top N 选股 → Diff 当前持仓 → BUY/SELL    │
│  │  │  SignalGenerator  │    │   外部信号亦可: Agent / 手动 / 其他程序            │
│  │  └────────┬─────────┘    │                                                │
│  └───────────┼──────────────┘                                                │
│              │                                                               │
│              ▼                                                               │
│  ┌──────────────────────────┐                                                │
│  │    第三层：交易信号中转     │   运行在 Mac 上 (本机)                            │
│  │                          │                                                │
│  │  ┌────────────────────┐  │   FastAPI REST Server (Port 8000)              │
│  │  │  Live API Server    │  │   ├─ GET  /api/health      健康检查            │
│  │  │  (FastAPI + uvicorn)│  │   ├─ GET  /api/trade/pending 获取待执行信号     │
│  │  │                    │  │   ├─ POST /api/trade/signals  创建信号 (CRUD)   │
│  │  │  ┌──────────────┐  │  │   ├─ PUT  /api/trade/{id}/status 状态机更新     │
│  │  │  │ REST Endpoints│  │  │   ├─ DELETE /api/trade/signals/{id} 删除信号  │
│  │  │  └──────────────┘  │  │   ├─ POST /api/portfolio/sync  持仓同步         │
│  │  │  ┌──────────────┐  │  │   └─ WS  /ws/live         实时推送             │
│  │  │  │ WebSocket     │  │  │                                              │
│  │  │  │ 实时信号推送   │  │  │   状态机: pending → sent → filled/partial/rejected│
│  │  │  └──────────────┘  │  │   SQLite trade_signals 表持久化                 │
│  │  └────────┬───────────┘  │   Bearer Token 鉴权                            │
│  └───────────┼──────────────┘                                                │
│              │                                                               │
│              │  局域网 HTTP Poll 或 WebSocket Push                             │
│              │  (192.168.50.229:8000)                                        │
│              ▼                                                               │
│  ┌──────────────────────────┐                                                │
│  │    第四层：QMT 执行层      │   运行在 Windows 上 (大QMT / miniQMT)             │
│  │                          │                                                │
│  │  ┌────────────────────┐  │   qtrade_bridge.py (大QMT 策略模式)              │
│  │  │  大QMT 桥接策略      │  │   ├─ run_time 定时轮询 (60s)                    │
│  │  │  qtrade_bridge.py  │  │   ├─ _http_get /api/trade/pending              │
│  │  └────────────────────┘  │   ├─ passorder() 下单 (23买入/24卖出)           │
│  │                          │   └─ _http_put 回传状态                         │
│  │  ┌────────────────────┐  │                                                │
│  │  │  miniQMT 执行器     │  │   qmt_executor.py (miniQMT 模式)                │
│  │  │  qmt_executor.py   │  │   ├─ WebSocket 实时接收信号                      │
│  │  └────────────────────┘  │   ├─ xtquant.xttrader 下单                     │
│  │                          │   └─ HTTP PUT 回传状态                          │
│  │  ┌────────────────────┐  │                                                │
│  │  │  模拟测试脚本        │  │   TraderTest.py — 云端 API 模式                │
│  │  │  TraderTest.py     │  │   reset_pending.py — 信号重置工具               │
│  │  └────────────────────┘  │                                                │
│  └──────────────────────────┘                                                │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 第一部分：架构分层

### 第一层：数据层

负责从外部数据源获取、清洗、存储 A 股历史交易数据。

**数据源：**
- **Tushare Pro** (主数据源，需 Token)：按日期全市场批量拉取日线行情 (`daily`)、日线基本面 (`daily_basic`，PE/PB/PS)、财务指标 (`fina_indicator`，ROE/ROE同比)、复权因子 (`adj_factor`)
- **AKShare** (备选数据源)：免费但有频率限制，达到上限会被封 IP
- **腾讯财经 API** (`qt.gtimg.cn`)：实时报价，用于心跳任务中的即时价格查询

**存储：**
- SQLite 数据库 (`data/stock_data.db`)，WAL 模式
- 表结构：`daily_price` (日线 OHLCVA)、`daily_basic` (PE/PB/PS)、`fina_indicator` (ROE)、`adj_factor` (复权因子)
- 采用 `INSERT OR REPLACE` UPSERT 策略，新增数据增量写入，已有数据自动覆盖，秒级完成同步

**数据清洗：**
- 连续缺失值处理 (连续缺失 >5 天 → 剔除)
- 极端价格变化过滤 (单日涨跌 >20% → 标记)
- ST 股票 / 退市股票 / 北交所股票过滤
- 新股过滤 (上市不足 6 个月排除)
- 最低交易天数要求 (≥240 天)

---

### 第二层：策略计算 + 回测 + 交易信号生成

#### 2.1 因子计算

系统中已实现 **18 个因子**，通过 `src/factors/` 下的插件式架构注册：

| 因子 | 模块 | 说明 |
|------|------|------|
| `momentum_20d` | `momentum.py` | 20日收益率动量 |
| `return_20d` | `return_20d.py` | 20日累计收益 |
| `trend_60d` | `trend_60d.py` | 60日价格趋势 |
| `rsi_14d` | `rsi.py` | 14日 RSI 相对强弱 |
| `volatility_20d` | `volatility.py` | 20日波动率 |
| `short_reversal` | `short_reversal.py` | 短期反转效应 |
| `intraday_range_10d` | `intraday_range.py` | 10日日内振幅 |
| `pb_rank` | `valuation.py` | 市净率排名 |
| `pe_ttm_rank` | `valuation.py` | 市盈率排名 |
| `roe_yoy_rank` | `roe_change.py` | ROE 同比改善排名 |
| `turnover_momentum_10d` | `turnover.py` | 换手率动量 |
| `vol_ratio` | `volume_price.py` | 量比 |
| `ma_deviation_20d` | `ma_deviation.py` | 20日均线偏离 |
| `amihud_20d` | `liquidity.py` | Amihud 非流动性指标 |
| `dollar_volume_20d` | `liquidity.py` | 成交额因子 |
| `candlestick` | `candlestick.py` | K线形态 |
| `downside_risk` | `downside_risk.py` | 下行风险 |
| `market_relative` | `market_relative.py` | 市场相对强度 |
| `profitability` | `profitability.py` | 盈利能力 |
| `return_distribution` | `return_distribution.py` | 收益分布 |
| `valuation_extended` | `valuation_extended.py` | 扩展估值 |

所有因子继承 `BaseFactor` 基类，通过 `@register_factor` 装饰器自动注册，在 `schemes.yaml` 中自由组合。

#### 2.2 综合评分

1. **标准化**：对每个因子的原始值做截面 Z-score 标准化
2. **加权评分**：`total_score = Σ (factor_zscore × weight)`
3. **Top N 选股**：按总分排名选取前 N 只，支持行业中性约束 (单行业上限可配置)

#### 2.3 方案配置 (`schemes.yaml`)

预设 8 个方案，按风格分类：

| 方案 | 风格 | 核心因子 | 权重特点 |
|------|------|---------|---------|
| `default` | 基准 | 振幅+PB+PE+趋势+波动 | 估值为主(-45%) |
| `conservative` | 低估值稳健 | PB+PE+趋势+波动+换手 | 估值为主(-55%) |
| `v1_zz1000` | 中证1000价值 | PE+振幅+波动+ROE改善 | ROE改善(+30%)主导 |
| `momentum` | 动量反转 | 动量+量比+RSI+波动+20日收益 | RSI反转(-25%)拼动量(+30%) |
| `动量增强版0610` | 均衡动量 | ROE+动量+趋势+PE+振幅+波动 | ROE(+25%)锚，多因子分散 |
| `多因子精选版0623` | 多因子精选 | 6因子，行业中性 | ROE(+25%)锚，动量反转(-15%) |
| `精简反转版0708` | 反转型 | ROE+RSI+20日收益 | 20日收益(-50%)反转主导 |
| `精简自适应版0708` | 自适应 | ROE+RSI+20日收益 | IC滚动自适应权重 |
| `流动性增强版0709` | 流动性聚焦 | ROE+Amihud+量比+成交额 | ROE(+55%)绝对锚定 + 流动性溢价 |

#### 2.4 IC 分析与自适应权重

- **IC 分析** (`ic_analyzer.py`)：滚动计算各因子的 IC 均值、ICIR、胜率，评估因子预测有效性
- **自适应权重** (`adaptive_weights.py`)：基于滚动窗口 (默认 60 日) IC 均值，动态调整下期因子权重 → IC 为负的因子降权

#### 2.5 回测引擎

- **信号生成**：每月/周/季调仓日，按 Top N 选股
- **T+1 执行**：信号日收盘出信号，次交易日开盘价成交，避免前视偏差
- **成本模型**：买入佣金 0.03%、卖出佣金 0.03%、印花税 0.1%
- **风控模块**：个股止损/止盈、组合最大回撤止损、止损后冷冻期
- **仓位管理**：等权 / 评分加权 / 风险平价
- **绩效指标**：年化收益、夏普比率、最大回撤、Calmar比率、胜率、盈亏比、月度/年度收益分布
- **可视化**：净值曲线、回撤曲线、交易标注、因子分布、IC 衰减、收益分布直方图、年度收益热力图

#### 2.6 信号生成器 (`SignalGenerator`)

实盘信号生成流程：

1. 加载最新日线数据 + 基本面数据 + 财务指标
2. 运行完整因子计算管道
3. 按方案权重评分 → Top N 选股
4. **Diff 对比**：新选股 vs 当前目标持仓快照 (`portfolio_snapshots` 表)
   - 新入选 → BUY 信号
   - 被剔除 → SELL 信号
5. 信号写入 `trade_signals` 表 (status = `pending`)
6. **WebSocket 广播**：实时推送给连接的 Windows QMT 客户端
7. 保存新的目标持仓快照

**外部信号来源**：除 SignalGenerator 自动生成外，也支持：
- AI Agent 手动创建交易信号（通过 REST API POST `/api/trade/signals`）
- 其他策略程序写入信号
- 云端同步信号 (594marc.cc 远程部署模式)
- 手动通过 API 创建/修改信号 (CRUD)

---

### 第三层：交易信号中转 (Live API Server)

**运行位置**：Mac 主机 (本机 `192.168.50.229`)

**技术栈**：FastAPI + uvicorn + Bearer Token 鉴权

**核心职责**：

| 功能 | 端点 | 说明 |
|------|------|------|
| 健康检查 | `GET /api/health` | 返回服务状态 + 待处理信号数 |
| 获取待执行信号 | `GET /api/trade/pending` | QMT 客户端轮询取信号 |
| 信号 CRUD | `GET/POST/PUT/DELETE /api/trade/signals` | 完整信号管理 |
| 状态更新 | `PUT /api/trade/{id}/status` | 带状态机校验 (pending→sent→filled) |
| 持仓同步 | `POST /api/portfolio/sync` | 接收 QMT 端实际持仓 |
| 查询持仓 | `GET /api/portfolio/actual` | 查询最近同步的实际持仓 |
| WebSocket | `WS /ws/live` | 实时信号推送 + 心跳 (30s) |

**信号状态机**：
```
pending ──→ sent ──→ filled
  │          │
  ├─→ cancelled  ├─→ partial
  └─→ rejected   └─→ rejected
```

**鉴权**：所有交易相关端点需要 `Authorization: Bearer <API_KEY>` 请求头，API Key 在 `config.ini` 的 `[live]` 段配置。

**两种部署模式的 API Server**：
- **本地模式** (本机 `8000` 端口)：QTrade 项目内置，Windows QMT 通过局域网直接访问
- **云端模式** (`https://594marc.cc`)：部署在公网服务器，通过 JWT + API Key 双重鉴权，`TraderTest.py` 和 `reset_pending.py` 连接此端点

---

### 第四层：QMT 执行层

运行在 Windows 机器上 (`192.168.50.171`)，负责实际下单执行。支持两种接入方式：

#### 4.1 大QMT 桥接策略 (`qtrade_bridge.py`)

- **运行方式**：作为 QMT 策略在策略管理器加载，选"实盘"或"模拟"运行
- **通信方式**：HTTP 轮询 (默认 60 秒)
- **工作流程**：
  1. `init()` 时注册 `run_time` 定时器，执行健康检查 + 鉴权测试
  2. 每 60 秒 `poll_signals()` 调用 `GET /api/trade/pending`
  3. 有信号 → `passorder()` 下单 (23 买入/24 卖出)
  4. 下单完成 → `PUT /api/trade/{id}/status` 回传状态
- **价格类型**：支持市价 (prType=5) 和限价 (prType=11)

#### 4.2 miniQMT 执行器 (`qmt_executor.py`)

- **运行方式**：命令行 `python qmt_executor.py`，在 miniQMT 同目录启动
- **通信方式**：WebSocket 长连接 + HTTP 回退
- **底层库**：`xtquant` (xttrader 下单)
- **特点**：
  - 实时推送，延迟更低
  - 断线自动重连 (指数退避，最大 60s)
  - 重连后 HTTP 补抓 MISS 信号
  - 无 xtquant 环境时自动切 Dry-Run 模式

#### 4.3 模拟测试工具

- **TraderTest.py**：云端模式测试脚本，轮询 → 打印 → 可选状态更新
- **reset_pending.py**：信号重置工具，将 sent 状态批量回退为 pending

---

## 实盘模拟层（Paper Trading）

零成本在真实市场节奏下验证选股方案——介于"回测"与"实盘 QMT"之间的验证层。复用第二层的因子管线与方案配置，但成交、记账、盯盘全部在本地虚拟账户进行，**与实盘 QMT 路径物理隔离**。

```
浏览器 (/paper)                              FastAPI server.py
    │ 创建/控制                                    │ /api/paper/* 端点（无 auth，本地 dashboard 用）
    ▼                                              ▼
paper_plans.status  ◄── 控制通道（按钮只 UPDATE status）──►  PaperWorker 独立进程 (src/paper/worker.py)
                                                          │ APScheduler，每 ~10s reconcile 表↔内存 jobs
                                                          │ 每 30s 写心跳 paper_worker_heartbeat
                                                          ▼ 每 tick：
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ 1. 交易日 gate（stats.trading_calendar()，非交易日跳过）                 │
   │ 2. T+1 rollover（新交易日：paper_holdings.t1_shares → free_shares）      │
   │ 3. 选股（SignalGenerator.compute_signals，复用因子管线；日线方案缓存）   │
   │    diff vs paper_holdings（经 holdings_provider 注入）                   │
   │ 4. PaperExecutor：取价(FallbackChain) → 费率(src/costs.py) → 改虚拟账户  │
   │ 5. 盯盘：刷 last_price → 写 paper_equity_history 净值快照                │
   └─────────────────────────────────────────────────────────────────────────┘
                                                          ▼
   paper_plans · paper_signals · paper_holdings · paper_transactions · paper_equity_history
   （5 张专用表，与 trade_signals 完全隔离）
```

**实时报价 fallback 链**（`src/paper/fetchers.py`）：腾讯 `qt.gtimg.cn` → 新浪 `hq.sinajs.cn`（需 Referer）→ 东财 `push2.eastmoney.com`（f43 单位分，/100）→ akshare（兜底）；Tushare `realtime_quote` 可选。任一源失败/报价≤0 自动降级。

**A 股成本模型**（`src/costs.py`）：佣金 0.03% 买/卖、印花税 0.05% 仅卖、最低 5 元兜底、过户费占位 0；金额按分（0.01）四舍五入。grid 模块维持现状，仅复用语义。

**控制语义**：`status = running`（执行 tick）/ `paused`（APScheduler job 暂停，run_tick 兜底跳过）/ `stopped`（摘除 job）。前端按钮只 `UPDATE paper_plans.status`，worker 下次 reconcile（~10s 内）生效。

**关键约束**：A 股 T+1（当日买入次日可卖，`t1_shares`/`free_shares` 拆分）；纯前向（`start_date ≥ 今天`，不支持历史回填）；非交易日 gate。**已知 v1 简化**：不模拟涨跌停封板、停牌冻结 last_price、实时价未复权（除权日跳变）。

---

## 第二部分：各层功能设计

### 数据层功能设计

#### 数据获取 (`fetcher.py` / `tushare_fetcher.py` / `akshare_fetcher.py`)

**Tushare 模式**（推荐）：
- 按日期全市场拉取：一次 API 调用获取当日所有 A 股日线数据
- 增量同步：对比本地数据库最新日期，只拉取缺失日期的数据
- 并发控制：API 调用间隔 0.5s (可调节)，建重试机制 (3 次)
- 自动处理 Tushare 积分/频率限制

**AKShare 模式**（免费备选）：
- 按股票逐个获取历史数据
- 调用间隔 2s (避免封 IP)
- 适合小规模回测

**数据拉取范围**：始终拉取全市场数据，不受选股范围 (沪深300/中证500 等) 影响。选股范围仅用于下游计算和回测。

#### 数据存储 (`storage.py`)

- **`daily_price` 表**：(trade_date, ts_code) 联合唯一索引，支持 UPSERT
- **`daily_basic` 表**：PE_TTM / PB / PS_TTM，按交易日期 + 股票代码存储
- **`fina_indicator` 表**：ROE / ROE_YoY，季度报告数据，使用时向前填充
- **`adj_factor` 表**：复权因子，用于前复权价格计算
- **`trade_signals` 表**：完整交易信号生命周期记录
- **`portfolio_snapshots` 表**：历次调仓目标持仓快照，用于 Diff 对比
- WAL 模式提升并发写入性能
- 所有表采用 CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS 幂等建表

#### 数据清洗 (`cleaner.py`)

- 去重：按 (trade_date, ts_code) 去重
- 缺失值处理：连续缺失 >5 行 → 剔除该股票
- 极端值过滤：单日涨跌幅超 ±20% → 标记
- 质量过滤：ST/退市/北交所/新股(不足6月)/交易天数不足 → 剔除
- 复权价格计算：使用 adj_factor 计算前复权 OHLC

---

### 策略层功能设计

#### 因子注册机制 (`factors/base.py`)

```
BaseFactor (基类)
├── factor_name: str        # 因子列名，如 "momentum_20d"
├── dependencies: list      # 依赖的原始列，如 ["close"]
├── calculate(df) -> df     # 计算因子值，追加到 DataFrame
└── @register_factor        # 类装饰器，自动注册到全局 registry
```

所有因子通过 `@register_factor` 装饰器自动注册，无需手动维护因子列表。新增因子只需新建 `factors/xxx.py` 并在 `signal_generator.py` 中 import。

#### 因子计算流程

1. 按日期分组，对每个交易日独立计算 (避免跨日数据泄漏)
2. 筛选股票池 (指数成分股 / 全市场 / 自定义)
3. 并行计算所有已注册因子
4. 缺失因子值的股票自动设为 NaN → 评分时排除

#### 综合评分与选股 (`scorer.py`)

1. **截面标准化**：每个交易日，每个因子做 Z-score (或 Rank 标准化)
2. **方向对齐**：反转因子 (波动率/低PE) 使用负权重，动量因子使用正权重
3. **加权求和**：`total_score = Σ(factor_zscore × weight)`
4. **行业中性** (可选)：每个行业内独立标准化后再汇总，限制单行业占比
5. **Top N 选择**：按 total_score 降序取前 N

#### 回测引擎 (`backtest/engine.py`)

主循环逻辑：
```
for 每个调仓日 (每月/周/季最后一个交易日):
    1. 获取当前持仓市值
    2. 检查风控条件 (止损/止盈/回撤)
    3. 生成选股信号 (因子评分 → Top N)
    4. 计算卖出信号 (当前持有但不在新选股中)
    5. 计算买入信号 (新选股中未持有的)
    6. T+1 开盘价执行交易
    7. 扣除交易成本
    8. 更新持仓和现金
    9. 记录交易日志
```

#### 风控模块

- **个股止损**：持仓亏损超阈值 → 次交易日开盘强制卖出
- **个股止盈**：持仓盈利超阈值 → 次交易日开盘强制卖出
- **组合回撤止损**：组合净值从高点回撤超阈值 → 清仓
- **冷冻期**：止损后 N 日内禁止买回同一股票

#### 仓位管理 (`position_sizing.py`)

- **等权** (`equal_weight`)：每只股票分配同等资金
- **评分加权** (`score_weighted`)：按 total_score 比例分配，分数越高仓位越大
- **风险平价** (`risk_parity`)：按波动率倒数分配，波动越低仓位越大

---

### 信号中转层功能设计

#### REST API 设计

遵循 RESTful 风格，全部 JSON 格式，Bearer Token 鉴权：

```
GET    /api/health                        # 公开端点
GET    /api/trade/pending                 # 获取 status=pending 的信号
GET    /api/trade/signals                 # 列表 (支持 status/ts_code/rebalance_date 过滤)
GET    /api/trade/signals/{id}            # 单条查询
POST   /api/trade/signals                 # 创建 (必填: ts_code, action, quantity, rebalance_date)
PUT    /api/trade/signals/{id}            # 修改 (管理端 CRUD，跳过状态机校验)
DELETE /api/trade/signals/{id}            # 删除
PUT    /api/trade/{id}/status             # 状态更新 (带状态机转换校验)
POST   /api/portfolio/sync                # 接收 QMT 端实际持仓上报
GET    /api/portfolio/actual              # 查询最近同步的实际持仓
WS     /ws/live?api_key=xxx               # WebSocket 实时推送
```

#### 状态机设计

```
              ┌──────────┐
              │ pending  │  信号生成器/手动创建 → 初始状态
              └────┬─────┘
      ┌────────────┼────────────┐
      ▼            ▼            ▼
  ┌──────┐   ┌──────────┐  ┌──────────┐
  │ sent │   │ cancelled│  │ rejected │
  └──┬───┘   └──────────┘  └──────────┘
     │
     ├──────────────┐
     ▼              ▼
 ┌────────┐   ┌─────────┐
 │ filled │   │ partial │  终端状态 (不可再转换)
 └────────┘   └─────────┘
```

- **sent**：QMT 已接收并执行下单，自动记录 `sent_at`
- **filled**：全部成交，自动记录 `filled_at`
- **partial**：部分成交（尚未实现追单逻辑）
- **rejected**：QMT 执行失败（含 `error_msg`）
- **cancelled**：手动撤销

#### WebSocket 实时推送

- 客户端通过 `ws://host:8000/ws/live?api_key=xxx` 连接
- 服务端维持连接列表，SignalGenerator 生成信号后广播到所有客户端
- 心跳机制：客户端发 `ping` → 服务端回 `{"type":"pong"}`
- 客户端断连自动清理，不影响其他连接

#### 云端部署模式 (594marc.cc)

- 额外提供 Admin API (`/api/admin/*`)：JWT 登录 + 全量信号管理
- Admin 登录后获取 access_token，可批量查询/操作信号
- `reset_pending.py` 支持通过 Admin API 批量重置信号状态

---

### QMT 执行层功能设计

#### 大QMT 桥接策略 (`qtrade_bridge.py`)

**设计要点**：
- 无状态设计：不依赖 ContextInfo 存储历史数据，避免 K 线回滚导致状态丢失
- `run_time` 定时器：使用 QMT 原生定时器替代 `while True` 循环，框架管理生命周期
- 失败自恢复：网络异常不崩溃，跳过本次轮询等待下次
- 状态回传：下单成功/失败均通过 HTTP PUT 回传 Mac 端

**下单参数映射**：
| 信号字段 | passorder 参数 | 说明 |
|---------|---------------|------|
| action=BUY | opType=23 | 买入 |
| action=SELL | opType=24 | 卖出 |
| - | orderType=1101 | 按股数下单 |
| price_type=MKT | prType=5, price=-1 | 最新价 (市价) |
| price_type=LIMIT | prType=11, price=limit_price | 限价 |
| - | quickTrade=2 | 立即交易 |
| quantity | volume | 股数 |

#### miniQMT 执行器 (`qmt_executor.py`)

**设计要点**：
- WS 长连接：相比轮询延迟更低，适合高频场景
- 指数退避重连：1s → 2s → 4s → ... → 60s (封顶)
- 断线补抓：重连后 HTTP GET missed signals 防止信号遗漏
- Dry-Run 模式：无 xtquant 环境时打印模拟日志，不崩溃
- 同步/异步分离：xtquant 下单是同步的，WS 接收是异步的，通过线程安全处理

#### 异常处理

- 网络故障：重连 + 补抓，不影响已持久化的信号
- QMT 下单失败：回传 rejected 状态 + 错误信息到 Mac 端
- Mac 服务不可用：QMT 端轮询/重连等待，不丢失本地状态

---

## 网络拓扑

```
┌──────────────────────────────────────────────────────────┐
│                    局域网 192.168.50.0/24                 │
│                                                          │
│   Mac (192.168.50.229)          Windows (192.168.50.171) │
│   ┌─────────────────────┐       ┌──────────────────────┐ │
│   │ Live API Server     │◄──────│ QMT 策略/执行器       │ │
│   │ Port 8000           │ HTTP  │ qtrade_bridge.py     │ │
│   │ (FastAPI + uvicorn) │ 轮询  │ 或 qmt_executor.py   │ │
│   └─────────────────────┘  或   │ (Port: 无需监听)     │ │
│         ▲                   WS  └──────────────────────┘ │
│         │                  推送                          │
│         │                                               │
│   ┌─────┴───────────┐                                   │
│   │ SignalGenerator  │                                   │
│   │ (定时/手动触发)   │                                   │
│   └─────────────────┘                                   │
└──────────────────────────────────────────────────────────┘

外网 (可选):
  594marc.cc 云端 API ──── TraderTest.py (测试用)
  qt.gtimg.cn ──── 实时报价 (心跳任务)
  Tushare API ──── 历史数据下载
```

---

## 系统启动流程

### 1. 启动 Mac 端 API 服务

```bash
cd scripts/QTrade
nohup python3 -m uvicorn src.live.server:app --host 0.0.0.0 --port 8000 > /tmp/qtrade-server.log 2>&1 &
```

### 2. 生成交易信号

```bash
# 方式一：程序化生成 (Python)
python3 -c "
from src.live.signal_generator import SignalGenerator
gen = SignalGenerator(scheme_name='精简自适应版0708', top_n=10)
signals = gen.generate_signals('20260715')
"

# 方式二：REST API 手动创建
curl -X POST http://localhost:8000/api/trade/signals \
  -H 'Authorization: Bearer Trader88888888' \
  -H 'Content-Type: application/json' \
  -d '{"ts_code":"600036.SH","action":"BUY","quantity":1000,"rebalance_date":"20260715"}'
```

### 3. Windows 端启动 QMT 执行

**大QMT 方式**：
1. 确保 `qtrade_bridge.py` 已复制到 QMT 策略目录
2. QMT 策略管理器 → 加载策略 → 选择"模拟"或"实盘" → 运行

**miniQMT 方式**：
```bash
python qmt_executor.py --config config_windows.json
```

---

## 配置关键参数

### `config.ini` — `[live]` 段

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `api_port` | API 服务监听端口 | 8000 |
| `api_key` | Bearer Token 鉴权密钥 | 自定义随机字符串 |
| `rebalance_schedule` | 调仓频率: monthly / biweekly | monthly |
| `scheme_name` | 使用的方案名称 (对应 schemes.yaml) | default |
| `top_n` | 持仓数量 | 10 |
| `total_capital` | 总资金 (用于仓位计算) | 1,000,000 |

### `config_windows.json` (Windows 端)

```json
{
    "mac_host": "http://192.168.50.229:8000",
    "api_key": "Trader88888888"
}
```

**注意**：两边 `api_key` 必须一致，Mac 端 IP 按实际局域网地址配置。

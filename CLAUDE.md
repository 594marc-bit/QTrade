# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

## Python 环境

**`.venv` 虚拟环境**（python3.14，清华源）：

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PyYAML   # 偶有漏装，补一手
```

运行时用 `.venv/bin/python` 或先 `source .venv/bin/activate`。本机系统 `python3` (3.10) 和直接 `python3.14` (全局安装) 均可工作，但推荐 venv 隔离。

## 常用命令

```bash
# 激活虚拟环境
source .venv/bin/activate

# 交互式向导
python run.py

# CLI 回测（完整参数，无交互）
python run.py --index 000300 --start 20240101 --end 20260717 \
    --source qmt --scheme momentum --yes

# 运行测试
python -m pytest tests/ -v

# 启动实盘 API 服务（Mac 端，端口 8000）
python -m uvicorn src.live.server:app --host 0.0.0.0 --port 8000

# 实盘模拟 dashboard（API 服务需先启动，浏览器访问）
#   http://<mac-ip>:8000/paper
# 实盘模拟 worker（独立进程，承载定时调度；与 API 服务分开跑）
bash scripts/start_paper_worker.sh        # 或 python -m src.paper.worker

# Windows 端：启动数据 API 服务（在 Windows 上运行）
# uvicorn qmt_api_server:app --host 0.0.0.0 --port 8001
```

## 架构

四层量化交易系统。详细架构见 `docs/QTRADE_ARCHITECTURE.md`。

**第一层 — 数据**（`src/data/`）：从多个数据源获取 OHLCV 及基本面数据，清洗后存入 SQLite（`data/stock_data.db`，WAL 模式）。日线价格当前使用 `qmt` 源；`daily_basic`/`fina_indicator`/指数行情仍来自 Tushare。

**第二层 — 策略与回测**（`src/factors/`、`src/backtest/`、`src/scheme.py`）：约 20 个可插拔因子（通过 `@register_factor` 装饰器注册）。方案在 `schemes.yaml` 中定义因子组合与权重。向量化回测引擎，T+1 执行，包含交易成本模型（佣金 0.03% 买/卖，印花税 0.05% 卖）和风控模块。

**第三层 — 信号中转**（`src/live/`）：Mac 端 FastAPI REST + WebSocket 服务（端口 8000）。SignalGenerator 运行因子管道 → Top-N 选股 → 与当前持仓快照 diff → 生成 BUY/SELL 信号。QMT 端通过轮询或 WebSocket 推送获取信号。

**第四层 — QMT 执行**（`QMTScripts/`）：在 Windows 上运行（192.168.50.171）。策略文件首行必须为 `#coding:gbk`，在 QMT 平台内加载运行。

## QMT 日线数据链路（当前数据源）

```
Windows QMT 策略（QMTScripts/qmt_strategy_数据导出.py）
    → C:\quant_data\stock_data.db（daily_price 表，9 列，WAL 模式）
    → FastAPI（QMTScripts/qmt_api_server.py）@ 192.168.50.171:8001
    → Mac 端 src/data/qmt_fetcher.py（HTTP 请求，无需 adj_factor/qfq——价格已是前复权）
    → src/data/storage.py save_daily_price → data/stock_data.db
```

**字段约定**（关键——这是 `daily_price` 表跨组件的合约）：
- `trade_date`：TEXT `'YYYYMMDD'` 格式
- `ts_code`：TEXT `'000001.SZ'` 格式
- `open/high/low/close`：REAL，前复权，单位元
- `vol`：REAL，单位**手**（不是股）
- `amount`：REAL，单位**千元**（QMT 原始值为元，导出时需除以 1000）
- `is_trading`：REAL，1.0=正常 / 0.0=停牌（QMT 源按 amount>0 判断）
- 唯一约束 `(trade_date, ts_code)`，通过 `INSERT OR REPLACE` 实现 UPSERT

**复权基准漂移**：除权后历史前复权价会整体变化，增量模式下库中旧数据不会自动刷新。需在 QMT 策略中设置 `FULL_REFRESH=True` 全量重建，然后 Mac 端清空 `daily_price` 重拉。与旧 Tushare 流程行为一致。

**旧 Tushare 库备份**：`data/stock_data.db.backup_tushare_20260718`（942MB，全市场 2023-2026）。

## 实盘模拟（`src/paper/`）

零成本在真实市场节奏下验证选股方案：基于 `schemes.yaml` 的方案创建可定时运行的模拟实例，用免费实时报价模拟成交，沉淀交易流水与净值曲线。**与实盘 QMT 路径完全物理隔离**（独立 5 张表，不触 `trade_signals`）。

```
FastAPI dashboard（src/live/server.py /api/paper/*）  ──浏览器──► /paper
   │ 创建/控制只 UPDATE paper_plans.status
   ▼
paper_plans.status（控制通道）
   ▼ 每 ~10s reconcile
PaperWorker 独立进程（src/paper/worker.py，APScheduler）
   │ 每 tick：交易日 gate → T+1 rollover → 选股(可缓存) → 模拟成交 → 盯盘
   ▼
SignalGenerator.compute_signals（复用因子管线，diff vs paper_holdings）
   ▼
PaperExecutor（src/paper/executor.py）：取价(FallbackChain) → 费率(src/costs.py) → 改虚拟账户
   ▼
paper_holdings / paper_signals / paper_transactions / paper_equity_history（5 张表）
```

**模块**：`storage.py`（5 表 CRUD）｜`fetchers.py`（腾讯→新浪→东财→akshare fallback 链）｜`executor.py`（成交+T+1+盯盘）｜`tick.py`（单 tick 编排+选股缓存+交易日 gate）｜`worker.py`（APScheduler+reconcile+心跳）｜`holdings_provider.py`（paper_holdings→SignalGenerator 适配）。`src/costs.py` 是统一 A 股费率模型（佣金/印花税/最低 5 元）。

**关键约定**：
- **T+1**：`paper_holdings.t1_shares`（当日买入锁定）/ `free_shares`（可卖）；新交易日 worker rollover 解锁。SELL 只消耗 `free_shares`，不足则 reject。
- **控制语义**：`status = running`（执行 tick）/ `paused`（定时器照走、跳过执行）/ `stopped`（摘 job）。
- **选股缓存**：日线方案（`uses_intraday_factors=0`，因子名不含 `_5m`）同日只跑一次因子管线，后续 tick 复用 top_picks 重 diff；分钟因子方案每 tick 全跑。
- **已知 v1 简化**：不模拟涨跌停封板、停牌冻结 last_price、实时价未复权（除权日有跳变）、纯前向不支持历史回填（`start_date ≥ 今天`）。

## 非显而易见的坑

1. **`wizard.py:run_pipeline()` 有自己独立的数据源绑定**（约第 608-624 行），绕过了 `src/data/fetcher.py` 的分发。新增数据源时，**两个地方都要改**。wizard 将 `_sync_stocks_data`、`_get_index_constituents`、`_get_all_stocks`、`_fetch_daily_basic`、`_get_index_daily` 绑定为模块级函数。

2. **QMT 策略文件**：首行 `#coding:gbk` 是 QMT 平台强制要求，但文件本身按 UTF-8 存储入库。QMT 在导入时会自行转码。在 Mac 上用 `python -m py_compile` 检查这些文件**会报错**（这属于正常现象）。

3. **QMT 的 `run_time` 是一次性的**：回调函数末尾必须重新注册（`ContextInfo.run_time("函数名", "3600nSecond", "")`）才能持续循环。

4. **config.ini 被 gitignore 排除**：`config.ini` 包含 API 密钥和数据源配置。`config.ini.example` 是模板。QMT API 地址在 `config.ini` 的 `[qmt_api]` 段。

5. **两个不同的 8000 端口**：Mac 端 `src/live/server.py` 监听 8000（面向 QMT 的信号 API）。Windows 端 `qmt_api_server.py` 监听 8001（面向 Mac 的日线数据 API）。不要混淆。

6. **qmt 模式下的 ROE 数据**：wizard 原本只在 `data_source == "tushare"` 时才拉取 `fina_indicator`。现已改为 `("tushare", "qmt")`——数据仍然来自 Tushare（不走 QMT）。如果未设置 Tushare token，依赖 ROE 的方案（如"精简自适应版0708"）的 ROE 值将为空。

7. **实盘模拟 vs 实盘信号严格隔离**：paper 走独立表（`paper_*`），绝不写 `trade_signals`，也不经 WebSocket 推给 QMT。`SignalGenerator.generate_signals()`（写 `trade_signals`+广播+存快照）是 **live 专用**；paper 只调 `compute_signals()`/`compute_selection()`+`diff_holdings()`（无副作用）。

8. **`LIVE_API_KEY` 按值导入的坑**：`src/live/server.py` 用 `from src.config import LIVE_API_KEY` 按值绑定，运行时改 `cfg.LIVE_API_KEY` **不会**影响 server 已绑定的值。测试需直接 `srv.LIVE_API_KEY = ...`。

9. **paper worker 是独立进程**：必须单独启动（`python -m src.paper.worker`），FastAPI server 不带调度。worker 崩了重启会从 `paper_plans.status` 自动恢复所有 running/paused 方案。dashboard 的 worker 红绿灯看心跳（`paper_worker_heartbeat` 表，90s 内=在线）。

10. **`data/` 被 gitignore**：`paper.html` 等前端静态页**不放** `data/results/dashboard_demo/`（不会被提交），而是放版本化的 `src/live/static/`，经 `/paper-static` 挂载、`/paper` 重定向。

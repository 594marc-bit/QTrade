"""实盘模拟 (paper trading) — 基于选股方案的定时模拟交易层。

子模块：
- storage.py        5 张表的 DDL 与 CRUD
- fetchers.py       免费实时报价 fetcher + fallback 链
- holdings_provider 读 paper_holdings 的 SignalGenerator 适配器
- executor.py       模拟成交引擎（费率 + T+1）
- worker.py         独立调度进程（APScheduler + DB 控制通道）

与实盘路径完全隔离（独立 paper_* 表，不触 trade_signals / QMT）。
"""

# 新机器部署指南

## 前置条件

- Windows 系统
- Python 3.10+
- QMT / miniQMT 客户端（如需运行交易执行器）

---

## 1. 创建虚拟环境

项目里已有的 `.venv` 不要直接复制（虚拟环境绑定了原机器的 Python 路径）。只复制源代码文件到新机器，然后重建：

```bash
python -m venv .venv
.venv\Scripts\activate
```

## 2. 安装依赖

```bash
pip install -r requirements.txt
```

> `xtquant` 不需要 pip 安装，它是 QMT 客户端自带的库，`qmt_executor.py` 运行时会从 QMT 安装目录自动加载。

## 3. 配置文件

```bash
copy config_windows.example.json config_windows.json
```

然后编辑 `config_windows.json`，修改 Mac 端 IP 地址、API 密钥等参数。

## 4. 启动服务

根据需要选择要启动的服务：

| 角色 | 脚本 | 启动命令 | 说明 |
|------|------|----------|------|
| 数据 API 服务 | `qmt_api_server.py` | `uvicorn qmt_api_server:app --host 0.0.0.0 --port 8001` | 向 Mac 端提供日线 / 分钟线数据 |
| miniQMT 执行器 | `qmt_executor.py` | `python qmt_executor.py` | 接收 Mac 端 WebSocket 信号并下单 |
| 大 QMT 桥接 | `qtrade_bridge.py` | 在 QMT 客户端 GUI 中加载 | 轮询 HTTP API 并执行交易 |
| 模拟券商测试 | `TraderTest.py` | `python TraderTest.py` | 轮询云端 pending 信号，模拟券商端行为 |
| 重置信号状态 | `reset_pending.py` | `python reset_pending.py` | 将卡住的信号状态重置为 pending |

## 5. 典型启动顺序

```bash
# 终端 1 — 数据 API 服务
.venv\Scripts\activate
uvicorn qmt_api_server:app --host 0.0.0.0 --port 8001

# 终端 2 — 交易执行器
.venv\Scripts\activate
python qmt_executor.py
```

QMT 策略脚本（`qtrade_bridge.py`、`JQ0707_QMT.py`、`网格交易策略.py`）在 QMT 客户端 GUI 中加载运行，不需要手动启动。

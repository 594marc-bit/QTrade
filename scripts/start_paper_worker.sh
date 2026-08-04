#!/usr/bin/env bash
# 启动实盘模拟 worker 进程（独立于 FastAPI server）。
# 用法：bash scripts/start_paper_worker.sh
#
# worker 读 paper_plans.status 作为控制通道（前端按钮 UPDATE 该字段），
# 进程内跑 APScheduler。Ctrl-C 优雅退出。

set -euo pipefail
cd "$(dirname "$0")/.."   # 切到项目根（scripts/ 的上一级）

# 激活 venv（若存在）
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

exec python -m src.paper.worker

"""实盘模拟独立 worker 进程。

进程内跑 APScheduler ``BackgroundScheduler``，每个方案一个 job；``paper_plans.status``
是控制通道——前端按钮只 UPDATE 该字段，本 worker 每 ~10s reconcile 表与内存 jobs：

- ``running`` → 确保 job 存在并 resume
- ``paused``  → 确保 job 存在并 pause（定时器照走，run_tick 也再兜底跳过）
- ``stopped`` → 摘除 job

启动：``python -m src.paper.worker``
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.paper import storage, tick
from src.paper.fetchers import get_fetcher

log = logging.getLogger(__name__)

JOB_PREFIX = "paper-"
RECONCILE_INTERVAL = 10   # 秒
HEARTBEAT_INTERVAL = 30   # 秒


# ---------------------------------------------------------------------------
# 频率解析
# ---------------------------------------------------------------------------

def parse_interval_seconds(spec: str) -> int:
    """``'5min'`` → 300, ``'2hour'`` → 7200, ``'30sec'`` → 30。"""
    s = spec.strip().lower()
    m = re.match(r"^(\d+)\s*(min|hour|sec)?$", s)
    if not m:
        raise ValueError(f"无法解析 interval 频率: {spec!r}（示例 '5min' / '2hour'）")
    n = int(m.group(1))
    unit = m.group(2) or "min"
    return n * {"min": 60, "hour": 3600, "sec": 1}[unit]


def build_trigger(plan: dict):
    """根据 freq_type/freq_spec 构造 APScheduler trigger。"""
    if plan["freq_type"] == "interval":
        return IntervalTrigger(seconds=parse_interval_seconds(plan["freq_spec"]))
    return CronTrigger.from_crontab(plan["freq_spec"])


# ---------------------------------------------------------------------------
# tick 入口（APScheduler 调用）
# ---------------------------------------------------------------------------

def run_plan_tick(plan_id: int) -> None:
    """APScheduler job 函数：取方案 → 取价器 → run_tick。"""
    plan = storage.get_plan(plan_id)
    if not plan:
        return
    fetcher = get_fetcher(plan.get("price_source") or "auto")
    try:
        result = tick.run_tick(plan_id, fetcher)
        log.info("[plan %s %s] %s", plan_id, plan.get("name"), result)
    except Exception:
        log.exception("[plan %s] tick 失败", plan_id)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class PaperWorker:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._stop = threading.Event()
        self._job_specs: dict[str, tuple[str, str]] = {}  # job_id -> (freq_type, freq_spec)

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        """启动 scheduler + reconcile/heartbeat 线程（非阻塞）。"""
        self.scheduler.start()
        log.info("PaperWorker scheduler 已启动")
        self._reconcile()
        threading.Thread(target=self._reconcile_loop, daemon=True, name="paper-reconcile").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="paper-heartbeat").start()

    def run(self) -> None:
        """启动并阻塞直到 Ctrl-C / SystemExit。"""
        self.start()
        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._stop.set()
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception:
            pass

    # ---------------- 后台循环 ----------------

    def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile()
            except Exception:
                log.exception("reconcile 失败")
            self._stop.wait(RECONCILE_INTERVAL)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                running = len(storage.list_plans(status="running"))
                storage.update_heartbeat(running_plans=running)
            except Exception:
                log.exception("heartbeat 失败")
            self._stop.wait(HEARTBEAT_INTERVAL)

    # ---------------- reconcile ----------------

    def _reconcile(self) -> None:
        plans = storage.list_plans()
        seen: set[str] = set()

        for plan in plans:
            job_id = f"{JOB_PREFIX}{plan['id']}"
            seen.add(job_id)
            status = plan["status"]
            if status == "stopped":
                self._remove_job(job_id)
                continue
            # running 或 paused → 确保 job
            self._ensure_job(plan, job_id, paused=(status == "paused"))

        # 删除表里已不存在的方案的 job
        for job in self.scheduler.get_jobs():
            if job.id.startswith(JOB_PREFIX) and job.id not in seen:
                self._remove_job(job.id)

        # 每个交易日一次的全局 T+1 解锁（与单方案 tick 解耦）
        self._maybe_daily_rollover()

    def _maybe_daily_rollover(self) -> None:
        """每个交易日执行一次 T+1 全局解锁，对所有方案（不限 status）。

        昨日买入应在新的交易日解锁——即使该方案当日尚未触发 tick（cron 未到点、
        paused/stopped）。用 heartbeat.last_rollover_date 去重，保证一日只解一次、
        不误伤当日新买入（当日买入的 t1 要等次日）。
        """
        today = dt.datetime.now().strftime("%Y%m%d")
        if not tick.is_trading_day(today):
            return
        hb = storage.get_heartbeat() or {}
        last = hb.get("last_rollover_date")
        if last and today <= last:
            return
        rolled = storage.rollover_all_plans()
        storage.update_heartbeat(
            running_plans=len(storage.list_plans(status="running")),
            last_rollover_date=today,
        )
        if rolled:
            log.info("每日 T+1 rollover：解锁方案 %s", rolled)

    def _ensure_job(self, plan: dict, job_id: str, paused: bool) -> None:
        spec = (plan["freq_type"], plan["freq_spec"])
        prev = self._job_specs.get(job_id)
        if prev != spec or self.scheduler.get_job(job_id) is None:
            # 新建或频率变更 → 重建
            self._remove_job(job_id)
            self.scheduler.add_job(
                run_plan_tick, build_trigger(plan), args=[plan["id"]],
                id=job_id, name=plan.get("name") or job_id,
                misfire_grace_time=60, max_instances=1, coalesce=True,
            )
            self._job_specs[job_id] = spec
        # pause / resume
        try:
            if paused:
                self.scheduler.pause_job(job_id)
            else:
                self.scheduler.resume_job(job_id)
        except Exception:
            pass

    def _remove_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass
        self._job_specs.pop(job_id, None)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ---- PID 互斥锁：防止多实例重复执行 -------
    import atexit
    _pid_file = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / "data" / "paper_worker.pid"
    if _pid_file.exists():
        old_pid = _pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)  # 信号 0 只检查进程是否存在
            print(f"Worker 已在运行 (PID {old_pid})，退出。如需重启请先 kill {old_pid}")
            return
        except (OSError, ValueError):
            _pid_file.unlink(missing_ok=True)  # 旧 PID 已死，清理
    _pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: _pid_file.unlink(missing_ok=True))

    PaperWorker().run()


if __name__ == "__main__":
    main()

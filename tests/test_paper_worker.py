"""Tests for src/paper/worker.py — 频率解析 + reconcile（add/pause/stop/remove）。"""

import time

import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage
from src.paper.worker import (
    PaperWorker, build_trigger, parse_interval_seconds,
)
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_worker.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    return db


def _make_plan(pid_seed="p", freq_type="interval", freq_spec="5min"):
    return paper_storage.create_plan(
        name=pid_seed, scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type=freq_type, freq_spec=freq_spec,
    )


# ---------------------------------------------------------------------------
# 频率解析 / trigger
# ---------------------------------------------------------------------------

def test_parse_interval_seconds():
    assert parse_interval_seconds("5min") == 300
    assert parse_interval_seconds("2hour") == 7200
    assert parse_interval_seconds("30sec") == 30
    assert parse_interval_seconds("15") == 900     # 默认 min
    with pytest.raises(ValueError):
        parse_interval_seconds("bogus")


def test_build_trigger_interval():
    t = build_trigger({"freq_type": "interval", "freq_spec": "5min"})
    assert isinstance(t, IntervalTrigger)


def test_build_trigger_cron():
    t = build_trigger({"freq_type": "cron", "freq_spec": "50 14 * * 1-5"})
    assert isinstance(t, CronTrigger)


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

def test_reconcile_running_paused_stopped(tmp_db):
    w = PaperWorker()
    w.start()
    try:
        rpid = _make_plan("running"); paper_storage.update_plan_status(rpid, "running")
        ppid = _make_plan("paused");  paper_storage.update_plan_status(ppid, "paused")
        _spid = _make_plan("stopped")                      # 默认 stopped
        w._reconcile()
        time.sleep(0.1)  # 等 scheduler 处理 add/pause

        jobs = {j.id: j for j in w.scheduler.get_jobs()}
        assert f"paper-{rpid}" in jobs                     # running → 有 job
        assert f"paper-{ppid}" in jobs                     # paused → 有 job（暂停态）
        assert f"paper-{_spid}" not in jobs                # stopped → 无 job

        # paused job 的 next_run_time 为 None（不会再触发）
        assert jobs[f"paper-{ppid}"].next_run_time is None
        assert jobs[f"paper-{rpid}"].next_run_time is not None

        # running → stopped：job 摘除
        paper_storage.update_plan_status(rpid, "stopped")
        w._reconcile()
        time.sleep(0.1)
        ids = {j.id for j in w.scheduler.get_jobs()}
        assert f"paper-{rpid}" not in ids
    finally:
        w.shutdown()


def test_reconcile_removes_job_when_plan_deleted(tmp_db):
    w = PaperWorker()
    w.start()
    try:
        pid = _make_plan("del"); paper_storage.update_plan_status(pid, "running")
        w._reconcile()
        time.sleep(0.1)
        assert any(j.id == f"paper-{pid}" for j in w.scheduler.get_jobs())

        paper_storage.delete_plan(pid)
        w._reconcile()
        time.sleep(0.1)
        assert not any(j.id == f"paper-{pid}" for j in w.scheduler.get_jobs())
    finally:
        w.shutdown()


def test_reconcile_reschedules_on_freq_change(tmp_db):
    w = PaperWorker()
    w.start()
    try:
        pid = _make_plan("f1", freq_spec="5min")
        paper_storage.update_plan_status(pid, "running")
        w._reconcile()
        time.sleep(0.1)
        j1 = w.scheduler.get_job(f"paper-{pid}")
        assert j1 is not None

        # 直接改 freq_spec（模拟用户编辑）
        conn = base_storage.get_connection()
        conn.execute("UPDATE paper_plans SET freq_spec=? WHERE id=?", ("1hour", pid))
        conn.commit(); conn.close()

        w._reconcile()
        time.sleep(0.1)
        j2 = w.scheduler.get_job(f"paper-{pid}")
        assert j2 is not None
        # IntervalTrigger 的 interval 从 300s 变为 3600s
        assert j2.trigger.interval.seconds == 3600
    finally:
        w.shutdown()

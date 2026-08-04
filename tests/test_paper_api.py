"""Tests for paper trading REST endpoints (src/live/server.py)."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

import src.data.storage as base_storage
import src.paper.storage as paper_storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "paper_api.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    from src.live.server import app
    return TestClient(app)


def _today() -> str:
    return dt.datetime.now().strftime("%Y%m%d")


def _future() -> str:
    return (dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 创建 + 控制
# ---------------------------------------------------------------------------

def test_create_plan_then_control(client):
    resp = client.post("/api/paper/plans", json={
        "name": "动量-10万", "scheme_name": "default", "total_capital": 100000,
        "start_date": _future(), "freq_type": "interval", "freq_spec": "5min",
    })
    assert resp.status_code == 200, resp.text
    pid = resp.json()["plan_id"]

    detail = client.get(f"/api/paper/plans/{pid}").json()
    assert detail["plan"]["cash"] == 100000
    assert detail["plan"]["status"] == "stopped"

    assert client.post(f"/api/paper/plans/{pid}/start").json()["status"] == "running"
    assert paper_storage.get_plan(pid)["status"] == "running"
    client.post(f"/api/paper/plans/{pid}/pause")
    assert paper_storage.get_plan(pid)["status"] == "paused"
    client.post(f"/api/paper/plans/{pid}/resume")
    assert paper_storage.get_plan(pid)["status"] == "running"
    client.post(f"/api/paper/plans/{pid}/stop")
    assert paper_storage.get_plan(pid)["status"] == "stopped"


def test_create_unknown_action_rejected(client):
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=10000,
        start_date=_future(), freq_type="interval", freq_spec="5min",
    )
    r = client.post(f"/api/paper/plans/{pid}/bogus")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 创建校验
# ---------------------------------------------------------------------------

def test_create_rejects_unknown_scheme(client):
    r = client.post("/api/paper/plans", json={
        "name": "x", "scheme_name": "nope", "total_capital": 10000,
        "start_date": _future(), "freq_type": "interval", "freq_spec": "5min",
    })
    assert r.status_code == 400


def test_create_rejects_past_start_date(client):
    r = client.post("/api/paper/plans", json={
        "name": "x", "scheme_name": "default", "total_capital": 10000,
        "start_date": "20200101", "freq_type": "interval", "freq_spec": "5min",
    })
    assert r.status_code == 400


def test_create_rejects_bad_interval(client):
    r = client.post("/api/paper/plans", json={
        "name": "x", "scheme_name": "default", "total_capital": 10000,
        "start_date": _future(), "freq_type": "interval", "freq_spec": "bogus",
    })
    assert r.status_code == 400


def test_create_rejects_bad_cron(client):
    r = client.post("/api/paper/plans", json={
        "name": "x", "scheme_name": "default", "total_capital": 10000,
        "start_date": _future(), "freq_type": "cron", "freq_spec": "not a cron",
    })
    assert r.status_code == 400


def test_create_cron_ok(client):
    r = client.post("/api/paper/plans", json={
        "name": "x", "scheme_name": "default", "total_capital": 10000,
        "start_date": _future(), "freq_type": "cron", "freq_spec": "50 14 * * 1-5",
    })
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 读端点
# ---------------------------------------------------------------------------

def test_read_endpoints_empty_initially(client):
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date=_future(), freq_type="interval", freq_spec="5min",
    )
    assert client.get(f"/api/paper/plans/{pid}/equity").json()["equity"] == []
    assert client.get(f"/api/paper/plans/{pid}/holdings").json()["holdings"] == []
    assert client.get(f"/api/paper/plans/{pid}/transactions").json()["transactions"] == []


def test_list_plans(client):
    paper_storage.create_plan(
        name="a", scheme_name="default", total_capital=10000,
        start_date=_future(), freq_type="interval", freq_spec="5min",
    )
    r = client.get("/api/paper/plans")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_get_plan_404(client):
    assert client.get("/api/paper/plans/99999").status_code == 404


# ---------------------------------------------------------------------------
# worker 心跳
# ---------------------------------------------------------------------------

def test_worker_status_not_alive_without_heartbeat(client):
    r = client.get("/api/paper/worker/status")
    assert r.status_code == 200
    assert r.json()["alive"] is False


def test_worker_status_alive_after_heartbeat(client):
    paper_storage.update_heartbeat(running_plans=2)
    r = client.get("/api/paper/worker/status")
    assert r.json()["alive"] is True
    assert r.json()["heartbeat"]["running_plans"] == 2


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

def test_delete_plan_cascades(client):
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=10000,
        start_date=_future(), freq_type="interval", freq_spec="5min",
    )
    assert client.delete(f"/api/paper/plans/{pid}").status_code == 200
    assert paper_storage.get_plan(pid) is None
    assert client.delete(f"/api/paper/plans/{pid}").status_code == 404


# ---------------------------------------------------------------------------
# dashboard 页面（9.7 烟雾测试）
# ---------------------------------------------------------------------------

def test_paper_page_redirect(client):
    r = client.get("/paper", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/paper-static/paper.html" in r.headers["location"]


def test_paper_static_html_served(client):
    r = client.get("/paper-static/paper.html")
    assert r.status_code == 200
    assert "实盘模拟" in r.text
    assert "/api/paper/plans" in r.text  # 页面确有对接 API


"""Smoke tests for src/paper/storage.py — 用临时 DB，不污染真实库。"""

import sqlite3

import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把 paper/storage 指向临时 DB。"""
    db = tmp_path / "paper_test.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    return db


def test_create_plan_initial_cash_and_status(tmp_db):
    pid = paper_storage.create_plan(
        name="动量-10万", scheme_name="momentum", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
        top_n=5, uses_intraday_factors=0,
    )
    plan = paper_storage.get_plan(pid)
    assert plan is not None
    assert plan["cash"] == 100000            # cash 初始化 = 总金额
    assert plan["status"] == "stopped"
    assert plan["uses_intraday_factors"] == 0
    assert plan["started_at"] is None


def test_update_plan_status_sets_started_at(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=10000,
        start_date="20260727", freq_type="cron", freq_spec="50 14 * * 1-5",
    )
    assert paper_storage.update_plan_status(pid, "running") is True
    assert paper_storage.get_plan(pid)["started_at"] is not None
    paper_storage.update_plan_status(pid, "paused")
    assert paper_storage.get_plan(pid)["status"] == "paused"
    # paused 不重置 started_at
    assert paper_storage.get_plan(pid)["started_at"] is not None


def test_signals_save_fill_reject(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=10000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.save_signals([
        {"plan_id": pid, "ts_code": "600036.SH", "action": "BUY",
         "quantity": 1000, "scheme_name": "momentum",
         "rebalance_date": "20260727", "tick_ts": "2026-07-27 10:05:00"},
        {"plan_id": pid, "ts_code": "000001.SZ", "action": "SELL",
         "quantity": 500, "rebalance_date": "20260727",
         "tick_ts": "2026-07-27 10:05:00"},
    ])
    sigs = paper_storage.list_signals(pid)
    assert len(sigs) == 2
    buy_id = next(s["id"] for s in sigs if s["action"] == "BUY")
    sell_id = next(s["id"] for s in sigs if s["action"] == "SELL")
    paper_storage.mark_filled(buy_id, 12.34, 1000, "tencent", "2026-07-27 10:05:01")
    paper_storage.mark_rejected(sell_id, "T+1 锁定")
    filled = paper_storage.list_signals(pid, status="filled")
    rejected = paper_storage.list_signals(pid, status="rejected")
    assert len(filled) == 1 and filled[0]["fill_price"] == 12.34
    assert len(rejected) == 1 and "T+1" in rejected[0]["error_msg"]


def test_holdings_upsert_and_rollover(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=10000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1000, t1_shares=1000, free_shares=0, avg_cost=12.34,
    )
    h = paper_storage.get_holding(pid, "600036.SH")
    assert h["shares"] == 1000 and h["t1_shares"] == 1000 and h["free_shares"] == 0
    n = paper_storage.rollover_locks(pid)
    assert n == 1
    h = paper_storage.get_holding(pid, "600036.SH")
    assert h["t1_shares"] == 0 and h["free_shares"] == 1000


def test_transactions_and_sum_fees(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=10000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.append_transaction({
        "plan_id": pid, "signal_id": None, "ts_code": "600036.SH",
        "action": "BUY", "quantity": 1000, "fill_price": 10.0,
        "gross_amount": 10000.0, "commission": 5.0, "stamp_tax": 0.0,
        "transfer_fee": 0.0, "total_cost": 5.0, "net_amount": 10005.0,
        "cash_after": 9995.0, "price_source": "tencent", "note": None,
    })
    paper_storage.append_transaction({
        "plan_id": pid, "signal_id": None, "ts_code": "600036.SH",
        "action": "SELL", "quantity": 1000, "fill_price": 10.0,
        "gross_amount": 10000.0, "commission": 5.0, "stamp_tax": 5.0,
        "transfer_fee": 0.0, "total_cost": 10.0, "net_amount": 9990.0,
        "cash_after": 19985.0, "price_source": "tencent", "note": None,
    })
    fees = paper_storage.sum_fees(pid)
    assert fees["commission"] == 10.0
    assert fees["stamp_tax"] == 5.0
    assert fees["total_cost"] == 15.0
    assert fees["trade_count"] == 2


def test_equity_snapshot_and_list(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.append_snapshot(
        pid, trade_date="20260727", cash=50000, holdings_value=50000,
        total_equity=100000, nav=1.0, n_positions=5,
    )
    eq = paper_storage.list_equity(pid)
    assert len(eq) == 1 and eq[0]["nav"] == 1.0


def test_delete_plan_cascades(tmp_db):
    pid = paper_storage.create_plan(
        name="p", scheme_name="momentum", total_capital=10000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.save_signals([
        {"plan_id": pid, "ts_code": "600036.SH", "action": "BUY",
         "quantity": 1000, "rebalance_date": "20260727", "tick_ts": "x"},
    ])
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1000, t1_shares=1000, free_shares=0, avg_cost=10.0,
    )
    paper_storage.append_transaction({
        "plan_id": pid, "ts_code": "600036.SH", "action": "BUY",
        "quantity": 1000, "fill_price": 10.0, "gross_amount": 10000.0,
        "commission": 5.0, "stamp_tax": 0.0, "transfer_fee": 0.0,
        "total_cost": 5.0, "net_amount": 10005.0, "cash_after": 9995.0,
    })
    assert paper_storage.delete_plan(pid) is True
    assert paper_storage.get_plan(pid) is None
    assert paper_storage.list_signals(pid) == []
    assert paper_storage.get_holdings(pid) == []
    assert paper_storage.list_transactions(pid) == []


def test_heartbeat(tmp_db):
    paper_storage.update_heartbeat(running_plans=3, note="ok")
    hb = paper_storage.get_heartbeat()
    assert hb is not None
    assert hb["running_plans"] == 3
    assert hb["last_beat_at"] is not None

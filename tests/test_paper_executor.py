"""Tests for src/paper/executor.py — 模拟成交 + T+1 + 费率。"""

import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage
from src.paper.executor import PaperExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeFetcher:
    """{ts_code: price} → FallbackChain 接口。"""

    def __init__(self, prices):
        self.prices = prices

    def fetch(self, ts_codes):
        return {
            c: {"price": self.prices[c], "source": "fake"}
            for c in ts_codes if c in self.prices
        }


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_exec.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    return db


def _make_plan(cash=None, **kw):
    defaults = dict(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    defaults.update(kw)
    pid = paper_storage.create_plan(**defaults)
    if cash is not None:
        paper_storage.update_plan_runtime(pid, cash=cash)
    return pid


def _signals(pid, specs, tick_ts="2026-07-27 10:05:00", rebalance="20260727"):
    return paper_storage.create_signals_with_ids(
        pid, specs, tick_ts=tick_ts, scheme_name="default", rebalance_date=rebalance,
    )


# ---------------------------------------------------------------------------
# BUY
# ---------------------------------------------------------------------------

def test_buy_fills_and_capitalizes_commission(tmp_db):
    pid = _make_plan()  # cash=100000
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 1000}])

    ex = PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0}))
    stats = ex.execute_signals(sigs, "2026-07-27 10:05:00", "20260727")

    assert stats == {"filled": 1, "rejected": 0}
    assert paper_storage.get_plan(pid)["cash"] == 89995.0   # 100000 - (10000+5)

    h = paper_storage.get_holding(pid, "600036.SH")
    assert h["t1_shares"] == 1000 and h["free_shares"] == 0
    assert h["avg_cost"] == 10.005                          # (10000+5)/1000

    txs = paper_storage.list_transactions(pid)
    assert len(txs) == 1
    assert txs[0]["commission"] == 5.0 and txs[0]["stamp_tax"] == 0.0
    assert txs[0]["net_amount"] == 10005.0
    assert paper_storage.list_signals(pid, status="filled")


def test_buy_small_order_uses_min_commission(tmp_db):
    pid = _make_plan()
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 100}])

    PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0})).execute_signals(
        sigs, "2026-07-27 10:05:00", "20260727"
    )
    tx = paper_storage.list_transactions(pid)[0]
    # gross 1000, 费率佣金 0.3 < 5 → 取最低 5
    assert tx["commission"] == 5.0


# ---------------------------------------------------------------------------
# SELL
# ---------------------------------------------------------------------------

def test_sell_fills_free_shares_and_charges_stamp_tax(tmp_db):
    pid = _make_plan(cash=0.0)
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1000, t1_shares=0, free_shares=1000, avg_cost=10.0,
    )
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "SELL", "quantity": 1000}])

    stats = PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0})).execute_signals(
        sigs, "2026-07-27 14:50:00", "20260727"
    )
    assert stats == {"filled": 1, "rejected": 0}
    # cash = 0 + (10000 - 5 佣金 - 5 印花税) = 9990
    assert paper_storage.get_plan(pid)["cash"] == 9990.0
    assert paper_storage.get_holding(pid, "600036.SH") is None  # 清仓删除
    tx = paper_storage.list_transactions(pid)[0]
    assert tx["stamp_tax"] == 5.0 and tx["commission"] == 5.0


def test_sell_t1_locked_rejected(tmp_db):
    """当日买入（t1_shares）不可卖 → reject，持仓与现金不变。"""
    pid = _make_plan(cash=0.0)
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1000, t1_shares=1000, free_shares=0, avg_cost=10.0,
    )
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "SELL", "quantity": 1000}])

    stats = PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0})).execute_signals(
        sigs, "2026-07-27 10:05:00", "20260727"
    )
    assert stats == {"filled": 0, "rejected": 1}
    # 持仓未变
    h = paper_storage.get_holding(pid, "600036.SH")
    assert h["t1_shares"] == 1000 and h["free_shares"] == 0
    # 现金未变
    assert paper_storage.get_plan(pid)["cash"] == 0.0
    rej = paper_storage.list_signals(pid, status="rejected")
    assert len(rej) == 1 and "T+1" in rej[0]["error_msg"]


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------

def test_buy_cash_insufficient_rejected(tmp_db):
    pid = _make_plan(cash=1000.0)
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 1000}])

    stats = PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0})).execute_signals(
        sigs, "2026-07-27 10:05:00", "20260727"
    )
    assert stats == {"filled": 0, "rejected": 1}
    assert paper_storage.get_plan(pid)["cash"] == 1000.0   # 未变
    assert paper_storage.get_holding(pid, "600036.SH") is None
    assert paper_storage.list_transactions(pid) == []
    rej = paper_storage.list_signals(pid, status="rejected")
    assert "现金不足" in rej[0]["error_msg"]


def test_price_missing_rejected(tmp_db):
    pid = _make_plan()
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 1000}])

    stats = PaperExecutor(plan, FakeFetcher({})).execute_signals(
        sigs, "2026-07-27 10:05:00", "20260727"
    )
    assert stats == {"filled": 0, "rejected": 1}
    rej = paper_storage.list_signals(pid, status="rejected")
    assert "取价失败" in rej[0]["error_msg"]


# ---------------------------------------------------------------------------
# 盯盘 + 净值
# ---------------------------------------------------------------------------

def test_mark_to_market_writes_equity_snapshot(tmp_db):
    pid = _make_plan()  # cash 100000
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 1000}])
    ex = PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0}))
    ex.execute_signals(sigs, "2026-07-27 10:05:00", "20260727")

    snap = ex.mark_to_market("2026-07-27 10:05:01", "20260727")
    # cash 89995 + 持仓市值 1000×10=10000 = 99995
    assert snap["total_equity"] == 99995.0
    assert snap["holdings_value"] == 10000.0
    assert snap["n_positions"] == 1
    assert abs(snap["nav"] - 0.99995) < 0.001


def test_mark_to_market_freezes_last_price_when_fetch_fails(tmp_db):
    """取不到价时沿用 last_price（停牌/源抽风）。"""
    pid = _make_plan()
    plan = paper_storage.get_plan(pid)
    sigs = _signals(pid, [{"ts_code": "600036.SH", "action": "BUY", "quantity": 1000}])
    # 成交时取到价 10.0（last_price 落库）
    PaperExecutor(plan, FakeFetcher({"600036.SH": 10.0})).execute_signals(
        sigs, "2026-07-27 10:05:00", "20260727"
    )
    # 盯盘时取不到价 → 冻结 last_price 10.0
    snap = PaperExecutor(plan, FakeFetcher({})).mark_to_market(
        "2026-07-27 10:06:00", "20260727"
    )
    assert snap["holdings_value"] == 10000.0   # 1000 × 10 (frozen)

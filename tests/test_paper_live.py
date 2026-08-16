"""Tests for src/paper/tick.py live 模式 — 信号写 trade_signals + 记录持仓。"""

import pandas as pd
import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage
import src.paper.tick as tick
from src.paper.tick import run_tick


class FakeFetcher:
    def fetch(self, ts_codes):
        return {c: {"price": 10.0, "source": "fake"} for c in ts_codes}


class FakeLiveGen:
    """模拟 live 模式 generator：compute_signals + _calc_quantity。"""

    def __init__(self, signals, picks, df):
        self._signals = signals
        self._picks = picks
        self._df = df

    def compute_signals(self, d):
        return list(self._signals), self._picks, d

    def _calc_quantity(self, picks, ts_code):
        return 500


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_live.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    tick.clear_selection_cache()
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: ["20260727"])
    return db


def _make_plan(mode):
    pid = paper_storage.create_plan(
        name="实盘测试", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min", mode=mode,
    )
    paper_storage.update_plan_status(pid, "running")
    return pid


def test_live_run_writes_trade_signals_and_holdings(tmp_db, monkeypatch):
    import src.live.server as srv
    monkeypatch.setattr(srv, "broadcast_signals_sync", lambda signals: 0)

    signals = [{"ts_code": "600036.SH", "action": "BUY", "quantity": 500,
                "price_type": "MKT", "scheme_name": "default",
                "rebalance_date": "20260727"}]
    picks = pd.DataFrame({"ts_code": ["600036.SH"], "trade_date": ["20260727"],
                          "total_score": [1.5]})
    df = pd.DataFrame({"trade_date": ["20260727"], "ts_code": ["600036.SH"],
                       "close": [10.0]})
    monkeypatch.setattr(tick, "_make_generator",
                        lambda plan: FakeLiveGen(signals, picks, df))

    pid = _make_plan("live")
    out = run_tick(pid, FakeFetcher(), trade_date="20260727",
                   now_ts="2026-07-27 09:30:00")
    assert out.get("saved") == 1
    assert out.get("signals") == 1

    rows = base_storage.load_trade_signals()
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["ts_code"] == "600036.SH"
    assert row["action"] == "BUY"
    assert row["plan_name"] == "实盘测试"
    assert row["score"] == 1.5
    assert row["scheme_name"] == "default"

    h = paper_storage.get_holdings(pid)
    assert len(h) == 1
    assert h[0]["ts_code"] == "600036.SH"
    assert h[0]["shares"] == 500
    assert h[0]["free_shares"] == 500
    assert h[0]["t1_shares"] == 0


def test_paper_mode_does_not_write_trade_signals(tmp_db, monkeypatch):
    # paper 模式走原路径（这里仅验证不会进入 live 分派写 trade_signals）
    signals = [{"ts_code": "600036.SH", "action": "BUY", "quantity": 500,
                "price_type": "MKT", "scheme_name": "default",
                "rebalance_date": "20260727"}]
    picks = pd.DataFrame({"ts_code": ["600036.SH"], "trade_date": ["20260727"],
                          "total_score": [1.5]})
    df = pd.DataFrame({"trade_date": ["20260727"], "ts_code": ["600036.SH"],
                       "close": [10.0]})
    monkeypatch.setattr(tick, "_make_generator",
                        lambda plan: FakeLiveGen(signals, picks, df))

    pid = _make_plan("paper")
    run_tick(pid, FakeFetcher(), trade_date="20260727",
             now_ts="2026-07-27 09:30:00")
    assert base_storage.load_trade_signals().empty

"""Tests for src/paper/tick.py — gate / rollover / 选股缓存。"""

import pandas as pd
import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage
import src.paper.tick as tick
from src.paper.tick import detect_uses_intraday_factors, is_trading_day, run_tick


class FakeFetcher:
    def __init__(self, prices):
        self.prices = prices

    def fetch(self, ts_codes):
        return {c: {"price": self.prices[c], "source": "fake"}
                for c in ts_codes if c in self.prices}


class FakeGen:
    """记录 compute_signals 调用次数的假 generator。"""

    def __init__(self, signals):
        self._signals = signals
        self._df = pd.DataFrame({
            "trade_date": ["20260727"] * len(signals),
            "ts_code": [s["ts_code"] for s in signals],
            "close": [10.0] * len(signals),
        })
        self.compute_calls = 0

    def compute_signals(self, d):
        self.compute_calls += 1
        picks = pd.DataFrame({
            "ts_code": [s["ts_code"] for s in self._signals],
            "trade_date": [d] * len(self._signals),
            "total_score": [1.0] * len(self._signals),
        })
        return list(self._signals), picks, d

    def diff_holdings(self, picks, d):
        return list(self._signals)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_tick.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    tick.clear_selection_cache()
    return db


# ---------------------------------------------------------------------------
# 纯工具
# ---------------------------------------------------------------------------

def test_detect_intraday_factors(monkeypatch):
    monkeypatch.setattr("src.scheme.load_schemes", lambda: {
        "daily": {"factors": ["momentum_20d", "pe_ttm_rank"]},
        "intraday": {"factors": ["momentum_20d", "vol_ratio_5m"]},
    })
    assert detect_uses_intraday_factors("daily") is False
    assert detect_uses_intraday_factors("intraday") is True
    assert detect_uses_intraday_factors("unknown") is False  # 无因子 → False


def test_is_trading_day(monkeypatch):
    monkeypatch.setattr("src.data.stats.trading_calendar",
                        lambda: ["20260727", "20260728"])
    assert is_trading_day("20260727") is True
    assert is_trading_day("20260725") is False   # 周末


def test_is_trading_day_empty_calendar_optimistic(monkeypatch):
    """取不到日历时乐观放行（不阻断模拟）。"""
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: [])
    assert is_trading_day("20260101") is True


# ---------------------------------------------------------------------------
# run_tick: gate / status
# ---------------------------------------------------------------------------

def test_run_tick_skips_non_trading_day(tmp_db, monkeypatch):
    monkeypatch.setattr("src.data.stats.trading_calendar",
                        lambda: ["20260728"])  # 今天 727 不在
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.update_plan_status(pid, "running")
    out = run_tick(pid, FakeFetcher({}), trade_date="20260727")
    assert out == {"skipped": "non-trading day"}


def test_run_tick_skips_paused_plan(tmp_db, monkeypatch):
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: ["20260727"])
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.update_plan_status(pid, "paused")
    out = run_tick(pid, FakeFetcher({}), trade_date="20260727")
    assert out["skipped"].startswith("status=paused")


# ---------------------------------------------------------------------------
# run_tick: T+1 rollover on new trading day
# ---------------------------------------------------------------------------

def test_run_tick_rolls_over_t1_on_new_trading_day(tmp_db, monkeypatch):
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: ["20260727"])
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260720", freq_type="interval", freq_spec="5min",
    )
    paper_storage.update_plan_status(pid, "running")
    # 昨天买入的票（t1 锁定），且 last_signal_date 是昨天
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1000, t1_shares=1000, free_shares=0, avg_cost=10.0,
    )
    paper_storage.update_plan_runtime(pid, last_signal_date="20260726")

    # 让选股产出空信号（避免成交干扰）
    monkeypatch.setattr(tick, "_make_generator", lambda plan: FakeGen([]))

    run_tick(pid, FakeFetcher({"600036.SH": 10.0}), trade_date="20260727")

    h = paper_storage.get_holding(pid, "600036.SH")
    assert h["t1_shares"] == 0 and h["free_shares"] == 1000  # 已解锁


# ---------------------------------------------------------------------------
# run_tick: 日线选股缓存
# ---------------------------------------------------------------------------

def test_daily_plan_caches_selection_same_day(tmp_db, monkeypatch):
    """日线方案同日第二个 tick 不再重跑 compute_signals（复用缓存）。"""
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: ["20260727"])
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
        uses_intraday_factors=0,
    )
    paper_storage.update_plan_status(pid, "running")

    gens = []
    def factory(plan):
        g = FakeGen([{"ts_code": "600036.SH", "action": "BUY", "quantity": 100}])
        gens.append(g)
        return g
    monkeypatch.setattr(tick, "_make_generator", factory)

    fetcher = FakeFetcher({"600036.SH": 10.0})
    # tick 1：全量跑
    run_tick(pid, fetcher, trade_date="20260727", now_ts="2026-07-27 09:30:00")
    assert gens[-1].compute_calls == 1
    # tick 2：复用缓存，不应再调 compute_signals
    run_tick(pid, fetcher, trade_date="20260727", now_ts="2026-07-27 09:35:00")
    # 仍只有第一次 tick 触发了 compute_signals
    total = sum(g.compute_calls for g in gens)
    assert total == 1


def test_intraday_plan_runs_full_every_tick(tmp_db, monkeypatch):
    """分钟因子方案每个 tick 都全量重跑。"""
    monkeypatch.setattr("src.data.stats.trading_calendar", lambda: ["20260727"])
    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
        uses_intraday_factors=1,
    )
    paper_storage.update_plan_status(pid, "running")

    gens = []
    monkeypatch.setattr(tick, "_make_generator", lambda plan: (
        gens.append(FakeGen([{"ts_code": "600036.SH", "action": "BUY", "quantity": 100}])) or gens[-1]
    ))
    fetcher = FakeFetcher({"600036.SH": 10.0})
    run_tick(pid, fetcher, trade_date="20260727", now_ts="2026-07-27 09:30:00")
    run_tick(pid, fetcher, trade_date="20260727", now_ts="2026-07-27 09:35:00")
    assert sum(g.compute_calls for g in gens) == 2

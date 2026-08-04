"""Tests for SignalGenerator holdings_provider 参数化 + paper 适配器。"""

import pandas as pd
import pytest

import src.data.storage as base_storage
import src.paper.storage as paper_storage


# ---------------------------------------------------------------------------
# 默认 provider = live 路径（不触 DB）
# ---------------------------------------------------------------------------

def test_default_holdings_provider_is_live_snapshot():
    """未传 holdings_provider 时，默认读 portfolio_snapshots（live 行为不变）。"""
    from src.live.signal_generator import SignalGenerator
    from src.live.portfolio_tracker import get_current_target_portfolio

    gen = SignalGenerator(scheme_name="default")
    assert gen._holdings_provider is get_current_target_portfolio


def test_diff_uses_injected_holdings_provider():
    """注入的 provider 决定 diff 的"当前持仓"。"""
    from src.live.signal_generator import SignalGenerator

    gen = SignalGenerator(scheme_name="default", top_n=2, total_capital=100000)
    # _calc_quantity 需要的价格表
    gen._df = pd.DataFrame({
        "trade_date": ["20260101", "20260101"],
        "ts_code": ["000001.SZ", "000002.SZ"],
        "close": [10.0, 20.0],
    })
    top_picks = pd.DataFrame({
        "ts_code": ["000001.SZ"],   # 新选入 → BUY
        "trade_date": ["20260101"],
        "total_score": [1.5],
    })
    # provider 说当前持有 600036.SH（不在新选 → SELL 全部）
    gen._holdings_provider = lambda: pd.DataFrame({
        "ts_code": ["600036.SH"], "target_shares": [1000],
    })

    signals = gen._diff_portfolio(top_picks, "20260101")
    by_key = {(s["ts_code"], s["action"]): s["quantity"] for s in signals}
    assert ("000001.SZ", "BUY") in by_key
    assert ("600036.SH", "SELL") in by_key
    assert by_key[("600036.SH", "SELL")] == 1000


# ---------------------------------------------------------------------------
# paper 适配器（用临时 DB）
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_test.db"
    monkeypatch.setattr(base_storage, "DB_PATH", db)
    paper_storage.ensure_tables()
    return db


def test_paper_holdings_provider_sums_free_and_t1(tmp_db):
    """provider 报告的总持仓 = free_shares + t1_shares。"""
    from src.paper.holdings_provider import make_paper_holdings_provider

    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    paper_storage.upsert_holding(
        pid, "600036.SH", shares=1500, t1_shares=500, free_shares=1000, avg_cost=12.0,
    )
    df = make_paper_holdings_provider(pid)()
    assert len(df) == 1
    assert df.iloc[0]["ts_code"] == "600036.SH"
    assert df.iloc[0]["target_shares"] == 1500


def test_paper_holdings_provider_empty_when_no_holdings(tmp_db):
    from src.paper.holdings_provider import make_paper_holdings_provider

    pid = paper_storage.create_plan(
        name="p", scheme_name="default", total_capital=100000,
        start_date="20260727", freq_type="interval", freq_spec="5min",
    )
    df = make_paper_holdings_provider(pid)()
    assert df.empty
    assert "ts_code" in df.columns

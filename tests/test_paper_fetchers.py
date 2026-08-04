"""Tests for src/paper/fetchers.py — parse 逻辑 + fallback 行为（不打真实网络）。"""

import pytest

from src.paper import fetchers as F
from src.paper.fetchers import (
    AkshareFetcher, EastmoneyFetcher, FallbackChain, RealtimePriceFetcher,
    SinaFetcher, TencentFetcher, get_fetcher,
)


# ---------------------------------------------------------------------------
# Fake fetcher（绕过网络）
# ---------------------------------------------------------------------------

class FakeFetcher(RealtimePriceFetcher):
    def __init__(self, name: str, raw: dict):
        self.name = name
        self._raw = raw

    def _fetch_raw(self, ts_codes):
        return {c: self._raw[c] for c in ts_codes if c in self._raw}


def _chain(*fakes):
    ch = FallbackChain("auto")
    ch.fetchers = list(fakes)
    return ch


# ---------------------------------------------------------------------------
# FallbackChain
# ---------------------------------------------------------------------------

def test_chain_first_source_wins():
    ch = _chain(
        FakeFetcher("tencent", {"600036.SH": 12.34}),
        FakeFetcher("sina", {"600036.SH": 99.99}),  # 不应采用
    )
    out = ch.fetch(["600036.SH"])
    assert out["600036.SH"]["price"] == 12.34
    assert out["600036.SH"]["source"] == "tencent"


def test_chain_falls_through_on_missing():
    ch = _chain(
        FakeFetcher("tencent", {"600036.SH": 12.34}),           # 只给 600036
        FakeFetcher("sina", {"000001.SZ": 13.10}),              # 兜底 000001
    )
    out = ch.fetch(["600036.SH", "000001.SZ"])
    assert out["600036.SH"]["source"] == "tencent"
    assert out["000001.SZ"]["source"] == "sina"


def test_chain_all_fail_returns_empty():
    ch = _chain(FakeFetcher("tencent", {}), FakeFetcher("sina", {}))
    assert ch.fetch(["600036.SH"]) == {}


def test_price_le_zero_treated_as_failure_and_falls_through():
    """报价 ≤ 0 视为失败，自动降级到下一源。"""
    ch = _chain(
        FakeFetcher("tencent", {"600036.SH": 0}),      # 停牌报价 0
        FakeFetcher("sina", {"600036.SH": 12.34}),
    )
    out = ch.fetch(["600036.SH"])
    assert out["600036.SH"]["price"] == 12.34
    assert out["600036.SH"]["source"] == "sina"


def test_chain_dedup_preserves_order():
    ch = _chain(FakeFetcher("tencent", {"600036.SH": 12.34}))
    out = ch.fetch(["600036.SH", "600036.SH", "600036.SH"])
    assert list(out.keys()) == ["600036.SH"]


# ---------------------------------------------------------------------------
# 各源 parse（纯函数）
# ---------------------------------------------------------------------------

def test_tencent_parse():
    text = (
        'v_sh600036="1~招商银行~600036~12.34~12.30~12.33~12345~";'
        'v_sz000001="51~平安银行~000001~13.10~13.00~13.05~67890~";'
    )
    out = TencentFetcher._parse(
        text, ["600036.SH", "000001.SZ"], ["sh600036", "sz000001"]
    )
    assert out == {"600036.SH": 12.34, "000001.SZ": 13.10}


def test_sina_parse_uses_yesterday_when_current_zero():
    # 字段：名称,今开,昨收,最新价,...
    text = (
        'hq_str_sh600036="招商银行,12.32,12.30,12.34,12.40,12.28,...";'
        'hq_str_sz000001="平安银行,0,13.00,0,0,0,...";'  # 停牌：最新价 0 → 昨收
    )
    out = SinaFetcher._parse(
        text, ["600036.SH", "000001.SZ"], ["sh600036", "sz000001"]
    )
    assert out["600036.SH"] == 12.34
    assert out["000001.SZ"] == 13.00  # 用昨收


def test_eastmoney_secid():
    assert EastmoneyFetcher._secid("600036.SH") == "1.600036"
    assert EastmoneyFetcher._secid("000001.SZ") == "0.000001"


def test_eastmoney_rekey_remaps_bare_to_tscode():
    """_rekey 只把裸代码 key 还原成 ts_code，值原样透传（/100 在 _fetch_raw 内做）。"""
    by_bare = {"600036": 12.34, "000001": 13.10}
    out = EastmoneyFetcher._rekey(by_bare, ["600036.SH", "000001.SZ"])
    assert out == {"600036.SH": 12.34, "000001.SZ": 13.10}


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def test_get_fetcher_auto_default_order():
    ch = get_fetcher("auto")
    names = [f.name for f in ch.fetchers]
    assert names == ["tencent", "sina", "eastmoney", "akshare"]


def test_get_fetcher_single_source():
    ch = get_fetcher("sina")
    assert [f.name for f in ch.fetchers] == ["sina"]


def test_get_fetcher_unknown_falls_back_to_auto():
    ch = get_fetcher("bogus")
    assert [f.name for f in ch.fetchers] == ["tencent", "sina", "eastmoney", "akshare"]

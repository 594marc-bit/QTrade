"""免费实时报价 fetcher + fallback 链。

腾讯 → 新浪 → 东财 → akshare 依次降级；Tushare 可选。任何单源失败/超时/
报价 ≤ 0 都不影响其它源兜底。全部失败时对应 ts_code 不在结果中出现
（由 executor 决定 reject）。

报价单位：均为元（东财接口 f43 返回分，已 /100 归一）。
"""

from __future__ import annotations

from typing import Any

import requests

_TIMEOUT = 3.0  # 单源超时秒数（短，靠 fallback 兜底）


# ---------------------------------------------------------------------------
# ts_code ↔ 市场符号转换
# ---------------------------------------------------------------------------

def _split_code(ts_code: str) -> tuple[str, str]:
    """``600036.SH`` → ``("600036", "sh")``。"""
    code, _, suffix = ts_code.partition(".")
    exchange = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix.upper(), suffix.lower())
    return code, exchange


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------

class RealtimePriceFetcher:
    """fetcher 接口。子类实现 :meth:`_fetch_raw` 与 :attr:`name`。"""

    name: str = "base"

    def fetch(self, ts_codes: list[str]) -> dict[str, dict[str, Any]]:
        """返回 ``{ts_code: {"price": float, "source": self.name}}``。

        取不到 / 报价 ≤ 0 的 ts_code 不出现在结果中。任何异常都吞掉、返回
        能取到的部分（不抛）。
        """
        if not ts_codes:
            return {}
        try:
            raw = self._fetch_raw(ts_codes)
        except Exception:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for code, price in raw.items():
            if price and price > 0:
                out[code] = {"price": float(price), "source": self.name}
        return out

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 腾讯 qt.gtimg.cn
# ---------------------------------------------------------------------------

class TencentFetcher(RealtimePriceFetcher):
    name = "tencent"
    _URL = "https://qt.gtimg.cn/q="

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        symbols = [f"{ex}{code}" for code, ex in (_split_code(c) for c in ts_codes)]
        url = self._URL + ",".join(symbols)
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.encoding = "gbk"
        return self._parse(resp.text, ts_codes, symbols)

    @staticmethod
    def _parse(text: str, ts_codes: list[str], symbols: list[str]) -> dict[str, float]:
        # 行形：v_sh600036="1~招商银行~600036~12.34~...";  字段 [3]=最新价
        out: dict[str, float] = {}
        for ts_code, sym in zip(ts_codes, symbols):
            marker = f'v_{sym}="'
            i = text.find(marker)
            if i < 0:
                continue
            start = i + len(marker)
            end = text.find('";', start)
            if end < 0:
                continue
            fields = text[start:end].split("~")
            if len(fields) > 3 and fields[3]:
                try:
                    out[ts_code] = float(fields[3])
                except ValueError:
                    continue
        return out


# ---------------------------------------------------------------------------
# 新浪 hq.sinajs.cn（需 Referer）
# ---------------------------------------------------------------------------

class SinaFetcher(RealtimePriceFetcher):
    name = "sina"
    _URL = "https://hq.sinajs.cn/list="
    _HEADERS = {"Referer": "https://finance.sina.com.cn"}

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        symbols = [f"{ex}{code}" for code, ex in (_split_code(c) for c in ts_codes)]
        url = self._URL + ",".join(symbols)
        resp = requests.get(url, headers=self._HEADERS, timeout=_TIMEOUT)
        resp.encoding = "gbk"
        return self._parse(resp.text, ts_codes, symbols)

    @staticmethod
    def _parse(text: str, ts_codes: list[str], symbols: list[str]) -> dict[str, float]:
        # hq_str_sh600036="名称,今开,昨收,最新价,最高,最低,...";
        out: dict[str, float] = {}
        for ts_code, sym in zip(ts_codes, symbols):
            marker = f'hq_str_{sym}="'
            i = text.find(marker)
            if i < 0:
                continue
            start = i + len(marker)
            end = text.find('";', start)
            if end < 0:
                continue
            fields = text[start:end].split(",")
            if len(fields) < 4:
                continue
            try:
                cur = float(fields[3])   # 最新价
                yest = float(fields[2])  # 昨收（盘中前/停牌时最新价可能为 0）
            except ValueError:
                continue
            # 新浪规则：最新价为 0（未开盘/停牌）时用昨收
            out[ts_code] = cur if cur > 0 else yest
        return out


# ---------------------------------------------------------------------------
# 东方财富 push2.eastmoney.com（fltt=2 时 f2=最新价，单位：元）
# ---------------------------------------------------------------------------

class EastmoneyFetcher(RealtimePriceFetcher):
    name = "eastmoney"
    _URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

    @staticmethod
    def _secid(ts_code: str) -> str:
        code, _, suffix = ts_code.partition(".")
        mkt = {"SH": "1", "SZ": "0", "BJ": "0"}.get(suffix.upper(), "0")
        return f"{mkt}.{code}"

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        secids = ",".join(self._secid(c) for c in ts_codes)
        params = {
            "fltt": "2",  # 返回浮点数值，f2 直接以元为单位
            "fields": "f2,f12",
            "secids": secids,
        }
        resp = requests.get(self._URL, params=params, timeout=_TIMEOUT)
        data = resp.json().get("data") or {}
        diff = data.get("diff") or []
        out: dict[str, float] = {}
        for item in diff:
            code = item.get("f12")
            f2 = item.get("f2")
            if not code or f2 is None:
                continue
            try:
                out[code] = float(f2)
            except (TypeError, ValueError):
                continue
        # 注意：返回的 key 是 6 位裸代码，需还原成 ts_code
        return self._rekey(out, ts_codes)

    @staticmethod
    def _rekey(by_bare_code: dict[str, float], ts_codes: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for ts_code in ts_codes:
            bare = ts_code.partition(".")[0]
            if bare in by_bare_code:
                out[ts_code] = by_bare_code[bare]
        return out


# ---------------------------------------------------------------------------
# akshare（兜底，全市场快照）
# ---------------------------------------------------------------------------

class AkshareFetcher(RealtimePriceFetcher):
    name = "akshare"

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        import akshare as ak  # 懒加载（较重）
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {}
        # 列名：代码 / 最新价
        code_col = "代码" if "代码" in df.columns else df.columns[1]
        price_col = "最新价" if "最新价" in df.columns else df.columns[3]
        sub = df[df[code_col].isin([c.partition(".")[0] for c in ts_codes])]
        lookup = dict(zip(sub[code_col].astype(str), sub[price_col]))
        out: dict[str, float] = {}
        for ts_code in ts_codes:
            bare = ts_code.partition(".")[0]
            if bare in lookup:
                try:
                    out[ts_code] = float(lookup[bare])
                except (TypeError, ValueError):
                    continue
        return out


# ---------------------------------------------------------------------------
# Tushare realtime_quote（可选，需积分）
# ---------------------------------------------------------------------------

class TushareRealtimeFetcher(RealtimePriceFetcher):
    name = "tushare"

    def _fetch_raw(self, ts_codes: list[str]) -> dict[str, float]:
        from src.config import _config  # 复用既有配置读取
        token = _config.get("tushare", "token", fallback="").strip()
        if not token:
            return {}
        import tushare as ts
        pro = ts.pro_api(token)
        codes = ",".join(c.partition(".")[0] for c in ts_codes)
        df = pro.realtime_quote(ts_code=codes)
        if df is None or df.empty:
            return {}
        out: dict[str, float] = {}
        price_col = "PRICE" if "PRICE" in df.columns else None
        code_col = "TS_CODE" if "TS_CODE" in df.columns else None
        if price_col is None or code_col is None:
            return {}
        for _, row in df.iterrows():
            try:
                out[str(row[code_col])] = float(row[price_col])
            except (TypeError, ValueError):
                continue
        return out


# ---------------------------------------------------------------------------
# FallbackChain
# ---------------------------------------------------------------------------

# "auto" 模式默认顺序
DEFAULT_ORDER = ["tencent", "sina", "eastmoney", "akshare"]

_FETCHER_CLASSES = {
    "tencent": TencentFetcher,
    "sina": SinaFetcher,
    "eastmoney": EastmoneyFetcher,
    "akshare": AkshareFetcher,
    "tushare": TushareRealtimeFetcher,
}


class FallbackChain:
    """按顺序逐源取价，首个有效价（>0）即采用并记录 source。

    ``price_source='auto'`` 用 :data:`DEFAULT_ORDER`；否则用指定单源（仍走
    chain，便于切换）。
    """

    def __init__(self, price_source: str = "auto"):
        if price_source == "auto":
            order = DEFAULT_ORDER
        else:
            order = [price_source] if price_source in _FETCHER_CLASSES else DEFAULT_ORDER
        self.fetchers: list[RealtimePriceFetcher] = [
            _FETCHER_CLASSES[n]() for n in order
        ]
        self.price_source = price_source

    def fetch(self, ts_codes: list[str]) -> dict[str, dict[str, Any]]:
        """返回 ``{ts_code: {"price", "source"}}``。取不到的不在结果中。"""
        result: dict[str, dict[str, Any]] = {}
        remaining = list(dict.fromkeys(ts_codes))  # 去重保序
        for fetcher in self.fetchers:
            if not remaining:
                break
            got = fetcher.fetch(remaining)
            for code, info in got.items():
                result[code] = info
                if code in remaining:
                    remaining.remove(code)
        return result


def get_fetcher(price_source: str = "auto") -> FallbackChain:
    """便捷工厂。"""
    return FallbackChain(price_source=price_source)

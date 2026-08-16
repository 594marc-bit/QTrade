"""单 tick 编排：交易日 gate → T+1 rollover → 选股（可缓存）→ diff → 成交 → 盯盘。

选股缓存（D5）：日线方案（``uses_intraday_factors=0``）同日只跑一次因子管线，
后续 tick 复用 top_picks 并重建价格表后重 diff（保证被拒买的重试仍能算出
数量）。分钟因子方案每 tick 全量重跑。

T+1 rollover（D4）：每个 tick 开头若发现 ``last_signal_date < trade_date``
（新交易日），先解锁该方案 ``paper_holdings`` 的 t1_shares → free_shares。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from src.live.signal_generator import SignalGenerator
from src.data.storage import save_trade_signals
from src.paper import storage
from src.paper.executor import PaperExecutor
from src.paper.fetchers import FallbackChain
from src.paper.holdings_provider import make_paper_holdings_provider

# plan_id -> (trade_date, top_picks, latest_date, price_map)
_selection_cache: dict[int, tuple[str, pd.DataFrame, str, dict[str, float]]] = {}


def clear_selection_cache(plan_id: int | None = None) -> None:
    """清缓存（测试用，或方案删除/停止时）。"""
    if plan_id is None:
        _selection_cache.clear()
    else:
        _selection_cache.pop(plan_id, None)


# ---------------------------------------------------------------------------
# 纯工具
# ---------------------------------------------------------------------------

def detect_uses_intraday_factors(scheme_name: str) -> bool:
    """扫 ``schemes.yaml`` 方案因子名，含 ``_5m`` 后缀即判为分钟因子方案。"""
    from src.scheme import load_schemes

    cfg = (load_schemes() or {}).get(scheme_name) or {}
    factors = cfg.get("factors", []) or []
    return any(str(f).endswith("_5m") for f in factors)


def is_trading_day(trade_date: str | None = None) -> bool:
    """查 ``stats.trading_calendar()``。取不到日历时乐观放行（不阻断模拟）。"""
    from src.data.stats import trading_calendar

    cal = trading_calendar() or []
    if not cal:
        return True
    today = trade_date or dt.datetime.now().strftime("%Y%m%d")
    return today in cal


# ---------------------------------------------------------------------------
# SignalGenerator 工厂（可被测试 monkeypatch）
# ---------------------------------------------------------------------------

def _make_generator(plan: dict[str, Any]) -> SignalGenerator:
    return SignalGenerator(
        scheme_name=plan["scheme_name"],
        top_n=plan["top_n"],
        total_capital=plan["total_capital"],
        holdings_provider=make_paper_holdings_provider(plan["id"]),
        exclude_etf=bool(plan.get("exclude_etf", 1)),
        exclude_star=bool(plan.get("exclude_star", 1)),
        index_codes=plan.get("index_codes"),
    )


def _extract_price_map(df: pd.DataFrame | None, latest_date: str) -> dict[str, float]:
    if df is None or df.empty:
        return {}
    sub = df[df["trade_date"] == latest_date]
    return {row["ts_code"]: row["close"] for _, row in sub.iterrows()}


def _price_df(latest_date: str, price_map: dict[str, float]) -> pd.DataFrame | None:
    if not price_map:
        return None
    return pd.DataFrame({
        "trade_date": [latest_date] * len(price_map),
        "ts_code": list(price_map.keys()),
        "close": list(price_map.values()),
    })


# ---------------------------------------------------------------------------
# 选股 + diff（带缓存）
# ---------------------------------------------------------------------------

def _resolve_signals(
    plan: dict[str, Any], trade_date: str
) -> list[dict[str, Any]]:
    """返回本 tick 的信号列表。

    - 日线方案 + 当日已选股：复用 top_picks，重建价格表后重 diff。
    - 否则：全量跑因子管线，缓存选股，更新 ``last_signal_date``。
    """
    intraday = bool(plan["uses_intraday_factors"])
    cached = _selection_cache.get(plan["id"])

    if (not intraday) and cached and cached[0] == trade_date:
        _, top_picks, latest_date, price_map = cached
        gen = _make_generator(plan)
        gen._df = _price_df(latest_date, price_map)
        return gen.diff_holdings(top_picks, latest_date)

    gen = _make_generator(plan)
    signals, top_picks, latest_date = gen.compute_signals(trade_date)
    price_map = _extract_price_map(gen._df, latest_date)
    _selection_cache[plan["id"]] = (trade_date, top_picks, latest_date, price_map)
    storage.update_plan_runtime(plan["id"], last_signal_date=trade_date)
    return signals


# ---------------------------------------------------------------------------
# live tick
# ---------------------------------------------------------------------------

def run_live_tick(
    plan: dict[str, Any],
    trade_date: str,
    *,
    now_ts: str | None = None,
) -> dict[str, Any]:
    """live 模式单 tick：跑因子管线 → diff vs 本系统记录持仓 → 写 trade_signals。

    与 paper 模式的区别：不做模拟成交/费用/T+1（QMT 负责执行），生成信号后把
    新目标写回 ``paper_holdings``（本系统记录持仓），防止下一 tick 重复发 BUY。
    """
    now = dt.datetime.now()
    now_ts = now_ts or now.strftime("%Y-%m-%d %H:%M:%S")

    gen = _make_generator(plan)
    try:
        signals, top_picks, latest_date = gen.compute_signals(trade_date)
    except Exception as e:
        storage.update_plan_runtime(plan["id"], error_msg=f"选股失败: {e}",
                                    last_run_at=now_ts)
        return {"error": str(e)}

    # 辅助分析信息：plan 名 + 每只得分
    score_map = {row["ts_code"]: row.get("total_score") for _, row in top_picks.iterrows()}
    for s in signals:
        s["plan_name"] = plan["name"]
        s["score"] = score_map.get(s["ts_code"])

    saved = 0
    if signals:
        saved = save_trade_signals(pd.DataFrame(signals))
        try:
            from src.live.server import broadcast_signals_sync
            broadcast_signals_sync(signals)
        except Exception as e:
            print(f"[live] WS broadcast skipped: {e}")

    # 写回目标持仓（本系统记录持仓）
    price_map = _extract_price_map(gen._df, latest_date)
    holdings = []
    for _, row in top_picks.iterrows():
        code = row["ts_code"]
        shares = gen._calc_quantity(top_picks, code)
        price = price_map.get(code)
        holdings.append({
            "ts_code": code,
            "shares": shares,
            "avg_cost": price or 0.0,
            "last_price": price,
        })
    storage.set_target_holdings(plan["id"], holdings)
    storage.update_plan_runtime(plan["id"], last_run_at=now_ts,
                                last_signal_date=trade_date)
    return {"saved": saved, "signals": len(signals)}


# ---------------------------------------------------------------------------
# tick 主入口
# ---------------------------------------------------------------------------

def run_tick(
    plan_id: int,
    fetcher: FallbackChain,
    *,
    now_ts: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """对一个 running 方案执行一次 tick。

    Returns:
        ``{"filled", "rejected"}`` 或 ``{"skipped": reason}`` / ``{"error": ...}``。
    """
    now = dt.datetime.now()
    now_ts = now_ts or now.strftime("%Y-%m-%d %H:%M:%S")
    trade_date = trade_date or now.strftime("%Y%m%d")

    plan = storage.get_plan(plan_id)
    if not plan:
        return {"skipped": "plan not found"}
    if plan["status"] != "running":
        return {"skipped": f"status={plan['status']}"}
    if not is_trading_day(trade_date):
        return {"skipped": "non-trading day"}

    # live 模式：走 live 信号生成路径（写 trade_signals，不模拟成交）
    if plan.get("mode") == "live":
        return run_live_tick(plan, trade_date, now_ts=now_ts)

    # T+1 rollover：新交易日解锁
    last_sig = plan.get("last_signal_date")
    if last_sig and last_sig < trade_date:
        storage.rollover_locks(plan_id)

    # 选股 + diff（可缓存）
    stats: dict[str, Any]
    try:
        signals = _resolve_signals(plan, trade_date)
    except Exception as e:
        storage.update_plan_runtime(plan_id, error_msg=f"选股失败: {e}",
                                    last_run_at=now_ts)
        # 即使选股失败也刷新现有持仓价格（不因选股问题丢失盯盘）
        executor = PaperExecutor(plan, fetcher)
        executor.mark_to_market(now_ts, trade_date)
        return {"error": str(e)}

    executor = PaperExecutor(plan, fetcher)
    if signals:
        persisted = storage.create_signals_with_ids(
            plan_id, signals, tick_ts=now_ts,
            scheme_name=plan["scheme_name"], rebalance_date=trade_date,
        )
        stats = executor.execute_signals(persisted, now_ts, trade_date)
    else:
        stats = {"filled": 0, "rejected": 0, "note": "no change"}

    # 盯盘（无论是否有信号都刷新净值）
    executor.mark_to_market(now_ts, trade_date)
    storage.update_plan_runtime(plan_id, last_run_at=now_ts, clear_error=True)
    return stats

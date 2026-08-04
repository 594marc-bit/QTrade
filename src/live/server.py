"""Live trading API server.

Provides REST endpoints + WebSocket for signal delivery between Mac (strategy)
and Windows (QMT execution). Run with:

    uvicorn src.live.server:app --host 0.0.0.0 --port 8000
"""

import matplotlib as _mpl
_mpl.use("Agg")  # Non-GUI backend for server environment

import json
import logging
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import LIVE_API_KEY, LIVE_API_PORT, QMT_API_BASE_URL
from src.data.storage import (
    delete_signal,
    get_connection,
    get_signal,
    load_latest_snapshot,
    load_trade_signals,
    save_trade_signals,
    update_signal_fields,
    update_signal_status,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="QTrade Live Trading API", version="0.1.0")
security = HTTPBearer()

# Static file serving (mount more-specific paths FIRST to avoid prefix conflicts)
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles as _StaticFiles

# Mount results BEFORE dashboard so /dashboard/results/... works
_RESULTS_DIR = _Path(__file__).parent.parent.parent / "data" / "results"
if _RESULTS_DIR.exists():
    app.mount("/dashboard/results", _StaticFiles(directory=str(_RESULTS_DIR)), name="results")

_DASHBOARD_DIR = _RESULTS_DIR / "dashboard_demo"
if _DASHBOARD_DIR.exists():
    app.mount("/dashboard", _StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard")

# Paper trading dashboard (versioned under src/live/static/paper.html)
_PAPER_STATIC_DIR = _Path(__file__).parent / "static"
if _PAPER_STATIC_DIR.exists():
    app.mount("/paper-static", _StaticFiles(directory=str(_PAPER_STATIC_DIR)), name="paper-static")

# Connected WebSocket clients
_ws_clients: list[WebSocket] = []

# In-memory actual holdings (synced from execution side)
_actual_holdings: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_factor_modules_imported = False


def _import_factors():
    """Lazily import factor modules to trigger @register_factor decorators."""
    global _factor_modules_imported
    if _factor_modules_imported:
        return
    import src.factors.candlestick
    import src.factors.downside_risk
    import src.factors.intraday_range
    import src.factors.liquidity
    import src.factors.ma_deviation
    import src.factors.market_relative
    import src.factors.momentum
    import src.factors.profitability
    import src.factors.return_20d
    import src.factors.return_distribution
    import src.factors.roe_change
    import src.factors.rsi
    import src.factors.short_reversal
    import src.factors.trend_60d
    import src.factors.turnover
    import src.factors.valuation
    import src.factors.valuation_extended
    import src.factors.volatility
    import src.factors.volume_price
    import src.factors.grid_suitability
    import src.factors.minute_factors
    _factor_modules_imported = True


# ---------------------------------------------------------------------------
# Factor override helpers
# ---------------------------------------------------------------------------

_OVERRIDES_PATH = _Path(__file__).parent.parent.parent / "data" / "factor_overrides.json"


def _load_factor_overrides() -> dict:
    """Load factor metadata overrides from JSON file."""
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_factor_overrides(data: dict) -> None:
    """Save factor metadata overrides to JSON file."""
    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _verify_api_key(credentials: HTTPAuthorizationCredentials) -> None:
    """Verify the Bearer token against the configured API key."""
    if credentials.credentials != LIVE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Health check — returns server status and pending signal count."""
    pending = load_trade_signals(status="pending")
    return {
        "status": "ok",
        "pending_signals": len(pending),
    }


def _df_to_json(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert DataFrame to JSON-safe list of dicts, replacing NaN/NaT with None."""
    records: list[dict[str, Any]] = df.to_dict(orient="records")
    for record in records:
        for key, value in record.items():
            if value is None:
                continue
            if isinstance(value, float) and pd.isna(value):
                record[key] = None
    return records


@app.get("/api/trade/pending")
def get_pending(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Return all pending (unexecuted) trade signals."""
    _verify_api_key(credentials)
    signals = load_trade_signals(status="pending")
    if signals.empty:
        return []
    return _df_to_json(signals)


@app.put("/api/trade/{signal_id}/status")
def put_signal_status(
    signal_id: int,
    body: dict[str, Any],
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update a trade signal's execution status.

    Body: {"status": "sent|filled|partial|rejected|cancelled", ...}
    """
    _verify_api_key(credentials)

    new_status = body.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing 'status' field")

    kwargs = {}
    for key in ("broker_order_id", "filled_qty", "avg_price", "error_msg"):
        if key in body:
            kwargs[key] = body[key]

    ok = update_signal_status(signal_id, new_status, **kwargs)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition or signal {signal_id} not found",
        )

    logger.info(f"Signal {signal_id} → {new_status}")
    return {"ok": True, "signal_id": signal_id, "status": new_status}


# ---------------------------------------------------------------------------
# CRUD: /api/trade/signals — full create/read/update/delete for trade_signals
# ---------------------------------------------------------------------------

SIGNAL_LISTABLE_FIELDS = {"ts_code", "action", "quantity", "price_type",
                          "scheme_name", "rebalance_date", "status"}
SIGNAL_CREATABLE_FIELDS = {"ts_code", "action", "quantity", "price_type",
                           "limit_price", "scheme_name", "rebalance_date",
                           "cancel_signal_id"}
SIGNAL_UPDATABLE_FIELDS = {"ts_code", "action", "quantity", "price_type",
                           "limit_price", "scheme_name", "rebalance_date", "status",
                           "broker_order_id", "filled_qty", "avg_price", "error_msg"}
SIGNAL_PROTECTED_FIELDS = {"id", "created_at", "sent_at", "filled_at"}


@app.get("/api/trade/signals")
def list_signals(
    status: str | None = Query(None),
    ts_code: str | None = Query(None),
    rebalance_date: str | None = Query(None),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """List trade signals with optional filters.

    Query params:
        status:          Filter by status (e.g. 'pending', 'sent', 'filled')
        ts_code:         Filter by stock code (e.g. '600036.SH')
        rebalance_date:  Filter by rebalance date (e.g. '20260701')
    """
    _verify_api_key(credentials)
    signals = load_trade_signals(status=status)

    if signals.empty:
        return []

    if ts_code:
        signals = signals[signals["ts_code"] == ts_code]
    if rebalance_date:
        signals = signals[signals["rebalance_date"] == rebalance_date]

    return _df_to_json(signals)


@app.get("/api/trade/signals/{signal_id}")
def get_signal_by_id(
    signal_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Get a single trade signal by ID."""
    _verify_api_key(credentials)
    signal = get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    return signal


@app.post("/api/trade/signals")
def create_signal(
    body: dict[str, Any],
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Create a new trade signal. Status defaults to 'pending'.

    Required fields: ts_code, action (BUY/SELL), quantity, rebalance_date
    Optional fields: price_type (default 'MKT'), limit_price, scheme_name
    """
    _verify_api_key(credentials)

    missing = [f for f in ("ts_code", "action", "quantity", "rebalance_date") if f not in body]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    if body["action"] not in ("BUY", "SELL", "CANCEL"):
        raise HTTPException(status_code=400, detail="action must be BUY, SELL, or CANCEL")

    if not isinstance(body.get("quantity"), (int, float)) or body["quantity"] <= 0:
        raise HTTPException(status_code=400, detail="quantity must be a positive integer")

    # Only keep known columns
    clean = {k: v for k, v in body.items() if k in SIGNAL_CREATABLE_FIELDS}
    clean.setdefault("price_type", "MKT")
    clean.setdefault("status", "pending")

    df = pd.DataFrame([clean])
    saved = save_trade_signals(df)
    logger.info(f"Signal created: {clean['ts_code']} {clean['action']} {clean['quantity']}股")

    return {"ok": True, "saved": saved}


@app.put("/api/trade/signals/{signal_id}")
def update_signal(
    signal_id: int,
    body: dict[str, Any],
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update a trade signal (admin override — no status-transition validation).

    Accepts any updatable field. For workflow-driven status changes with
    transition validation, use PUT /api/trade/{signal_id}/status instead.
    """
    _verify_api_key(credentials)

    existing = get_signal(signal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

    # Strip protected and unknown fields
    clean = {k: v for k, v in body.items()
             if k in SIGNAL_UPDATABLE_FIELDS and k not in SIGNAL_PROTECTED_FIELDS}

    if not clean:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    if "action" in clean and clean["action"] not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="action must be BUY or SELL")

    ok = update_signal_fields(signal_id, **clean)
    if not ok:
        raise HTTPException(status_code=500, detail="Update failed")

    logger.info(f"Signal {signal_id} updated: {list(clean.keys())}")
    return {"ok": True, "signal_id": signal_id}


@app.delete("/api/trade/signals/{signal_id}")
def delete_signal_by_id(
    signal_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Delete a trade signal by ID."""
    _verify_api_key(credentials)
    ok = delete_signal(signal_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    logger.info(f"Signal {signal_id} deleted")
    return {"ok": True, "signal_id": signal_id}


# ---------------------------------------------------------------------------
# Portfolio sync
# ---------------------------------------------------------------------------

@app.post("/api/portfolio/sync")
def sync_portfolio(
    body: list[dict[str, Any]],
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Receive actual portfolio holdings from the execution side.

    Body: [{"ts_code": "600036.SH", "shares": 1000, "avg_cost": 12.34}, ...]
    """
    _verify_api_key(credentials)
    global _actual_holdings
    _actual_holdings = body
    logger.info(f"Portfolio synced: {len(body)} positions")
    return {"ok": True, "positions": len(body)}


@app.get("/api/portfolio/actual")
def get_actual_portfolio(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Return the last synced actual portfolio holdings."""
    _verify_api_key(credentials)
    return _actual_holdings


# ---------------------------------------------------------------------------
# Dashboard API endpoints (read-only, no auth)
# ---------------------------------------------------------------------------

@app.post("/api/backtest/start")
async def backtest_start(request: Request):
    """Start a backtest job. Returns job_id for polling."""
    try:
        config = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    from src.live.backtest_runner import start_backtest
    job_id = start_backtest(config)
    return {"job_id": job_id}


@app.get("/api/backtest/{job_id}/status")
def backtest_status(job_id: str, add_charts: bool = Query(False)):
    """Return current status of a backtest job. ``?add_charts=true`` includes
    a list of chart URLs available in the result directory."""
    from src.live.backtest_runner import get_status
    status = get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if add_charts and status.get("result_dir"):
        import os as _os
        try:
            files = sorted(_os.listdir(status["result_dir"]))
            status["charts"] = [
                f"/dashboard/results/{_os.path.basename(status['result_dir'])}/{f}"
                for f in files if f.endswith(".png")
            ]
        except Exception:
            status["charts"] = []
    return status


@app.get("/api/backtest/history")
def backtest_history():
    """Return list of past backtest jobs (newest first)."""
    from src.data.storage import list_backtest_jobs
    import json as _json
    jobs = list_backtest_jobs()
    for j in jobs:
        if isinstance(j.get("metrics"), str):
            try:
                j["metrics"] = _json.loads(j["metrics"])
            except Exception:
                pass
    return {"jobs": jobs}


@app.delete("/api/backtest/{job_id}")
def backtest_delete(job_id: int):
    """Delete a backtest job record and its result directory."""
    from src.data.storage import get_backtest_job, delete_backtest_job
    job = get_backtest_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    import shutil as _shutil
    rd = job.get("result_dir")
    if rd:
        try:
            _shutil.rmtree(rd, ignore_errors=True)
        except Exception:
            pass
    delete_backtest_job(job_id)
    return {"ok": True, "deleted": job_id}


@app.get("/api/dashboard/factors")
def dashboard_factors():
    """Return registered factors with name, description, category, and active state."""
    _import_factors()
    from src.factors.base import get_registered_factors
    factors = get_registered_factors()
    overrides = _load_factor_overrides()
    result = []
    for name, cls in sorted(factors.items()):
        ov = overrides.get(name, {})
        result.append({
            "name": ov.get("name", name),
            "description": ov.get("description", cls.description or ""),
            "description_cn": ov.get("description_cn", getattr(cls, "description_cn", "") or cls.description or ""),
            "category": getattr(cls, "category", "其他"),
            "active": ov.get("active", True),
            "original_name": name,
        })
    return {"count": len(result), "factors": result}


@app.get("/api/dashboard/factors/{name}")
def dashboard_factor_detail(name: str):
    """Return full detail for a single factor."""
    _import_factors()
    from src.factors.base import get_registered_factors
    import inspect as _inspect

    factors = get_registered_factors()
    if name not in factors:
        raise HTTPException(status_code=404, detail=f"Factor '{name}' not found")
    cls = factors[name]
    overrides = _load_factor_overrides().get(name, {})

    # Get source file path
    try:
        src_file = _inspect.getfile(cls)
        # Make relative to project root
        from pathlib import Path as _P
        root = _P(__file__).parent.parent.parent
        src_file = str(_P(src_file).relative_to(root))
    except Exception:
        src_file = "unknown"

    return {
        "name": overrides.get("name", name),
        "original_name": name,
        "description": overrides.get("description", cls.description or ""),
        "description_cn": overrides.get("description_cn", getattr(cls, "description_cn", "") or cls.description or ""),
        "category": getattr(cls, "category", "其他"),
        "active": overrides.get("active", True),
        "source_file": src_file,
        "dependencies": getattr(cls, "dependencies", []),
    }


@app.put("/api/dashboard/factors/{name}")
async def dashboard_factor_update(name: str, request: Request):
    """Update factor metadata (name, description, description_cn)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    overrides = _load_factor_overrides()
    entry = overrides.get(name, {})
    for field in ("name", "description", "description_cn"):
        if field in body and body[field]:
            entry[field] = body[field]
    overrides[name] = entry
    _save_factor_overrides(overrides)
    return {"ok": True, "name": name}


@app.delete("/api/dashboard/factors/{name}")
def dashboard_factor_delete(name: str):
    """Mark a factor as inactive (does NOT delete the .py file)."""
    overrides = _load_factor_overrides()
    entry = overrides.get(name, {})
    entry["active"] = False
    overrides[name] = entry
    _save_factor_overrides(overrides)
    return {"ok": True, "name": name}


@app.get("/api/dashboard/schemes")
def dashboard_schemes(name: str | None = Query(None, description="方案名，为空返回全部")):
    """Return all schemes or a specific scheme's factors and weights."""
    from src.scheme import list_schemes, load_scheme

    if name:
        try:
            factors, weights = load_scheme(name)
            return {
                "name": name,
                "factors": sorted(factors),
                "weights": weights,
            }
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Scheme '{name}' not found")

    schemes = list_schemes()
    return {
        "count": len(schemes),
        "schemes": [
            {"name": n, "description": d} for n, d in sorted(schemes.items())
        ],
    }


@app.get("/api/dashboard/schemes/{name}")
def dashboard_scheme_detail(name: str):
    """Return full detail for a single scheme with weights mapped to factor names."""
    from src.scheme import load_scheme, load_schemes
    schemes = load_schemes()
    if name not in schemes:
        raise HTTPException(status_code=404, detail=f"Scheme '{name}' not found")
    cfg = schemes[name]
    factors = cfg.get("factors", [])
    raw_weights = cfg.get("weights", {})
    # Map score-key weights to factor-name weights using _factor_to_score_col
    from src.factors.scorer import _factor_to_score_col
    weights_by_factor = {}
    for f in factors:
        score_key = _factor_to_score_col(f)
        if score_key in raw_weights:
            weights_by_factor[f] = raw_weights[score_key]
    return {
        "name": name,
        "description": cfg.get("description", ""),
        "factors": factors,
        "weights": weights_by_factor,
    }


@app.put("/api/dashboard/schemes/{name}")
async def dashboard_scheme_save(name: str, request: Request):
    """Save (create or update) a scheme to schemes.yaml."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    from src.scheme import save_scheme
    from src.factors.scorer import _factor_to_score_col
    description = body.get("description", "")
    factors = body.get("factors", [])
    weights_input = body.get("weights", {})
    if not factors:
        raise HTTPException(status_code=400, detail="At least one factor is required")
    # Convert factor-name weights to score-key weights for YAML storage
    weights = {_factor_to_score_col(f): w for f, w in weights_input.items()}
    save_scheme(name, description, factors, weights)
    return {"ok": True, "name": name}


@app.delete("/api/dashboard/schemes/{name}")
def dashboard_scheme_delete(name: str):
    """Delete a scheme from schemes.yaml."""
    from src.scheme import delete_scheme
    ok = delete_scheme(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Scheme '{name}' not found")
    return {"ok": True, "name": name}


# Dashboard data response cache (TTL 5 min — stats queries are heavy)
_DASHBOARD_CACHE: dict[str, tuple[float, dict]] = {}
_DASHBOARD_CACHE_TTL = 300.0


def _invalidate_dashboard_cache():
    _DASHBOARD_CACHE.clear()


@app.get("/api/dashboard/data")
def dashboard_data(
    segment: str | None = Query(None, description="市场分段透镜：全部/沪主板/科创板/深主板/创业板/ETF"),
):
    """Return database statistics for the data panel."""
    import time as _time
    global _DASHBOARD_CACHE

    key = segment or "__all__"
    now = _time.time()
    if key in _DASHBOARD_CACHE:
        ts, cached = _DASHBOARD_CACHE[key]
        if now - ts < _DASHBOARD_CACHE_TTL:
            return cached

    from src.data import stats as _stats

    payload = _stats.build_dashboard_payload(segment)
    etf = next((s for s in payload["segments"] if s["segment"] == "ETF"), {"stocks": 0, "rows": 0})
    payload["etf"] = {"rows": etf["rows"], "stocks": etf["stocks"]}
    _DASHBOARD_CACHE[key] = (now, payload)
    return payload


@app.post("/api/data/sync")
async def data_sync(request: Request):
    """Start a background sync of one dataset.

    - daily: 增量同步日线 (QMT)
    - basic: 最近60天估值 (Tushare)
    - fina:  增量同步财务 (Tushare, 每批200只)
    - minute: 最近5天分钟线 → 本地 minute_5m 表 (QMT)

    Progress is reported to the dashboard log (source="sync"); poll
    ``GET /api/dashboard/logs`` or watch the WebSocket.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    dataset = (body or {}).get("dataset", "")
    if dataset not in {"daily", "basic", "fina", "minute"}:
        raise HTTPException(status_code=400, detail="dataset must be daily|basic|fina|minute")
    import threading
    from src.data import manage
    threading.Thread(
        daemon=True, target=manage.sync, args=(dataset,), name=f"sync-{dataset}"
    ).start()
    return {"ok": True, "dataset": dataset, "message": "同步已在后台启动，进度见日志"}


@app.post("/api/data/rebuild")
async def data_rebuild(request: Request):
    """Full rebuild of daily_price. Requires body ``{"confirm": "REBUILD"}``.

    The frontend modal gates the Windows-FIRST ordering; this endpoint only
    guards against accidental bare calls.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if (body or {}).get("confirm") != "REBUILD":
        raise HTTPException(status_code=400, detail="需提供 confirm='REBUILD' 确认词")
    import threading
    from src.data import manage
    threading.Thread(daemon=True, target=manage.rebuild, name="rebuild").start()
    return {"ok": True, "message": "全量重建已在后台启动，进度见日志"}


@app.get("/api/data/windows-health")
def data_windows_health():
    """Proxy to the Windows QMT API ``/api/health`` (browser can't reach 8001 directly — CORS).

    Used by the rebuild modal to show Windows-side freshness before nuking local data.
    Status indicator uses a short timeout (5s) to avoid blocking the page.
    """
    import requests as _requests
    url = (QMT_API_BASE_URL or "http://127.0.0.1:8001").rstrip("/") + "/api/health"
    try:
        resp = _requests.get(url, timeout=5)
        resp.raise_for_status()
        return {"ok": True, "health": resp.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/dashboard/signals")
def dashboard_signals(limit: int = Query(10, description="返回最近N条")):
    """Return recent trade signals for the live signals panel."""
    df = load_trade_signals()
    if df.empty:
        return {"count": 0, "signals": []}
    recent = df.head(limit)
    # Convert to list of dicts, replacing NaN/NaT with None for JSON
    # pandas .where(notna, None) doesn't reliably convert float NaN → None,
    # so we process each row manually.
    records = []
    for _, row in recent.iterrows():
        rec = {}
        for k, v in row.items():
            try:
                if v != v:  # NaN check
                    rec[k] = None
                else:
                    rec[k] = v
            except (TypeError, ValueError):
                rec[k] = v
        records.append(rec)
    return {
        "count": len(df),
        "pending": int((df["status"] == "pending").sum()) if "status" in df.columns else 0,
        "signals": records,
    }


@app.get("/api/dashboard/portfolio")
def dashboard_portfolio():
    """Return latest portfolio snapshot for the holdings panel."""
    df = load_latest_snapshot()
    if df.empty:
        return {"count": 0, "holdings": [], "total_value": 0}
    return {
        "count": len(df),
        "rebalance_date": df["rebalance_date"].iloc[0] if "rebalance_date" in df.columns else "",
        "holdings": df.to_dict("records"),
    }


@app.get("/api/dashboard/logs")
def dashboard_logs(n: int = Query(50, description="返回最近N条")):
    """Return recent log entries from the SQLite-backed log table."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                level TEXT NOT NULL DEFAULT 'info',
                source TEXT DEFAULT '',
                message TEXT NOT NULL
            )
        """)
        conn.commit()
        rows = conn.execute(
            "SELECT ts, level, source, message FROM dashboard_logs ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    items = [
        {"ts": r[0], "level": r[1], "source": r[2], "message": r[3]}
        for r in reversed(rows)
    ]
    return {"count": len(items), "logs": items}


# ---------------------------------------------------------------------------
# Paper trading endpoints (实盘模拟 — 与 trade_signals / QMT 物理隔离)
# ---------------------------------------------------------------------------

@app.get("/paper")
def paper_page():
    """实盘模拟 dashboard 入口（重定向到静态页）。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/paper-static/paper.html")


def _paper_sanitize(obj):
    """递归把 NaN/Inf float → None，便于 JSON 序列化。"""
    import math
    if isinstance(obj, dict):
        return {k: _paper_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_paper_sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


@app.get("/api/paper/plans")
def paper_list_plans(status: str | None = Query(None)):
    """列出全部模拟方案，可选 status 过滤。"""
    from src.paper import storage as ps
    return {"count": len(ps.list_plans(status=status)),
            "plans": _paper_sanitize(ps.list_plans(status=status))}


@app.get("/api/paper/plans/{plan_id}")
def paper_get_plan(plan_id: int):
    """方案详情 + 累计费率 + 最新净值。"""
    from src.paper import storage as ps
    plan = ps.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    return {
        "plan": _paper_sanitize(plan),
        "fees": _paper_sanitize(ps.sum_fees(plan_id)),
        "latest_equity": _paper_sanitize(ps.get_latest_equity(plan_id)),
    }


@app.post("/api/paper/plans")
async def paper_create_plan(request: Request):
    """创建模拟方案。校验 scheme 存在 / total_capital>0 / forward-only / freq 合法。

    uses_intraday_factors 不传则按 scheme 因子名自动判定（含 _5m → 1）。
    """
    from src.paper import storage as ps
    from src.scheme import load_schemes
    from src.paper.tick import detect_uses_intraday_factors

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    required = ("name", "scheme_name", "total_capital", "start_date", "freq_type", "freq_spec")
    missing = [f for f in required if body.get(f) in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必填字段: {missing}")

    scheme_name = body["scheme_name"]
    if scheme_name not in load_schemes():
        raise HTTPException(status_code=400, detail=f"方案 '{scheme_name}' 不存在")

    freq_type = body["freq_type"]
    if freq_type not in ("interval", "cron"):
        raise HTTPException(status_code=400, detail="freq_type 必须是 interval 或 cron")
    freq_spec = body["freq_spec"]
    if freq_type == "interval":
        try:
            from src.paper.worker import parse_interval_seconds
            parse_interval_seconds(freq_spec)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(freq_spec)
        except Exception:
            raise HTTPException(status_code=400, detail=f"cron 表达式无效: {freq_spec}")

    try:
        total_capital = float(body["total_capital"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="total_capital 必须是数字")
    if total_capital <= 0:
        raise HTTPException(status_code=400, detail="total_capital 必须 > 0")

    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y%m%d")
    if str(body["start_date"]) < today:
        raise HTTPException(status_code=400, detail=f"start_date 必须 >= 今天 ({today})；v1 纯前向")

    uses_intraday = body.get("uses_intraday_factors")
    if uses_intraday is None:
        uses_intraday = 1 if detect_uses_intraday_factors(scheme_name) else 0

    pid = ps.create_plan(
        name=body["name"], scheme_name=scheme_name, total_capital=total_capital,
        start_date=str(body["start_date"]), freq_type=freq_type, freq_spec=freq_spec,
        top_n=int(body.get("top_n", 10)),
        position_sizing=body.get("position_sizing", "equal_weight"),
        uses_intraday_factors=int(uses_intraday),
        index_codes=body.get("index_codes"),
        price_source=body.get("price_source", "auto"),
        slippage=float(body.get("slippage", 0.0)),
        exclude_etf=int(body.get("exclude_etf", 1)),
    )
    logger.info(f"Paper plan created: id={pid} scheme={scheme_name}")
    return {"ok": True, "plan_id": pid}


@app.post("/api/paper/plans/{plan_id}/{action}")
def paper_control(plan_id: int, action: str):
    """启动/暂停/继续/停止 — 只 UPDATE status（worker reconcile 生效）。"""
    from src.paper import storage as ps
    action_map = {"start": "running", "resume": "running",
                  "pause": "paused", "stop": "stopped"}
    if action not in action_map:
        raise HTTPException(status_code=400,
                            detail=f"未知操作: {action}（可选 start/pause/resume/stop）")
    if not ps.get_plan(plan_id):
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    new_status = action_map[action]
    ps.update_plan_status(plan_id, new_status)
    logger.info(f"Paper plan {plan_id} → {new_status}")
    return {"ok": True, "plan_id": plan_id, "status": new_status}


@app.delete("/api/paper/plans/{plan_id}")
def paper_delete_plan(plan_id: int):
    """删除方案（CASCADE 清理信号/持仓/流水/净值）。"""
    from src.paper import storage as ps
    if not ps.delete_plan(plan_id):
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    return {"ok": True, "plan_id": plan_id}


@app.get("/api/paper/plans/{plan_id}/equity")
def paper_equity(plan_id: int, limit: int | None = Query(None)):
    from src.paper import storage as ps
    if not ps.get_plan(plan_id):
        raise HTTPException(status_code=404, detail="plan not found")
    return {"equity": _paper_sanitize(ps.list_equity(plan_id, limit=limit))}


@app.get("/api/paper/plans/{plan_id}/holdings")
def paper_holdings(plan_id: int):
    from src.paper import storage as ps
    if not ps.get_plan(plan_id):
        raise HTTPException(status_code=404, detail="plan not found")
    return {"holdings": _paper_sanitize(ps.get_holdings(plan_id))}


def _is_a_share_trading_time(now: Any = None) -> bool:
    """Check if we're currently within A-share continuous auction hours (Mon-Fri)."""
    import datetime as _dt
    t = now or _dt.datetime.now()
    if t.weekday() >= 5:
        return False
    tm = t.time()
    morning_start = _dt.time(9, 30)
    morning_end = _dt.time(11, 30)
    afternoon_start = _dt.time(13, 0)
    afternoon_end = _dt.time(15, 0)
    return (morning_start <= tm <= morning_end) or (afternoon_start <= tm <= afternoon_end)


def _is_trading_day(date_str: str | None = None) -> bool:
    """Check if date_str is a trading day (uses trading calendar if available)."""
    from src.paper.tick import is_trading_day
    return is_trading_day(date_str)


@app.get("/api/paper/plans/{plan_id}/holdings-live")
def paper_holdings_live(plan_id: int):
    """Return holdings with live real-time prices during trading hours.

    During A-share trading hours (Mo-Fr 9:30-11:30, 13:00-15:00):
        Fetches real-time prices via the fallback chain and returns live prices.
    Outside trading hours:
        Returns the stored last_price (should be the last mark_to_market close).

    Response includes ``price_mode``: "live" | "close", and ``priced_at`` timestamp.
    """
    import datetime as _dt
    from src.paper import storage as ps
    from src.paper.fetchers import FallbackChain

    plan = ps.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")

    holdings = _paper_sanitize(ps.get_holdings(plan_id))
    now = _dt.datetime.now()
    trade_date = now.strftime("%Y%m%d")

    # Determine whether to fetch live prices
    use_live = _is_a_share_trading_time(now) and _is_trading_day(trade_date)
    price_mode = "live" if use_live else "close"

    if use_live and holdings:
        ts_codes = [h["ts_code"] for h in holdings if h.get("shares", 0) > 0]
        if ts_codes:
            chain = FallbackChain(plan.get("price_source", "auto"))
            prices = chain.fetch(ts_codes)
            for h in holdings:
                live = prices.get(h["ts_code"])
                if live and live.get("price") and live["price"] > 0:
                    h["last_price"] = round(live["price"], 4)
                    h["price_source"] = live.get("source", "?")
                    h["price_mode"] = "live"
                    continue
            # Any holding that didn't get a live price falls back to stored last_price
            for h in holdings:
                h.setdefault("price_mode", "close")

    # Ensure all holdings have price_mode set
    for h in holdings:
        if "price_mode" not in h:
            h["price_mode"] = "close"
        if "price_source" not in h:
            h["price_source"] = h.get("last_price_src", "")

    return {
        "holdings": holdings,
        "price_mode": price_mode,
        "priced_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/paper/plans/{plan_id}/transactions")
def paper_transactions(plan_id: int, limit: int = Query(100)):
    from src.paper import storage as ps
    if not ps.get_plan(plan_id):
        raise HTTPException(status_code=404, detail="plan not found")
    return {"transactions": _paper_sanitize(ps.list_transactions(plan_id, limit=limit))}


@app.get("/api/paper/worker/status")
def paper_worker_status():
    """worker 心跳红绿灯：last_beat_at 90s 内 = alive。"""
    from src.paper import storage as ps
    import datetime as _dt
    hb = ps.get_heartbeat()
    alive = False
    if hb and hb.get("last_beat_at"):
        try:
            last = _dt.datetime.strptime(hb["last_beat_at"], "%Y-%m-%d %H:%M:%S")
            alive = (_dt.datetime.now() - last).total_seconds() < 90
        except Exception:
            alive = False
    return {"alive": alive, "heartbeat": _paper_sanitize(hb)}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """WebSocket for real-time signal push.

    Client connects, authenticates with api_key query param, and receives
    signal JSON messages as they are generated. Server sends heartbeat pings
    every 30 seconds.
    """
    api_key = ws.query_params.get("api_key", "")
    if api_key != LIVE_API_KEY:
        await ws.accept()
        await ws.send_text(json.dumps({"error": "Invalid API key"}))
        await ws.close()
        return

    await ws.accept()
    _ws_clients.append(ws)
    logger.info(f"WebSocket client connected (total: {len(_ws_clients)})")

    try:
        while True:
            # Wait for client messages (or disconnection).
            # We use receive_text() as a blocking keep-alive; the server
            # pushes signals via broadcast_signal() called from signal_generator.
            data = await ws.receive_text()
            # Client can send "ping" — respond with "pong"
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        _ws_clients.remove(ws)
        logger.info(f"WebSocket client disconnected (remaining: {len(_ws_clients)})")


# ---------------------------------------------------------------------------
# Broadcast helper (called by signal_generator after writing signals)
# ---------------------------------------------------------------------------

async def broadcast_signals(signals: list[dict[str, Any]]) -> int:
    """Push new signals to all connected WebSocket clients.

    Args:
        signals: List of signal dicts to broadcast.

    Returns:
        Number of clients that received the broadcast.
    """
    if not _ws_clients or not signals:
        return 0

    message = json.dumps({
        "type": "signals",
        "data": signals,
    })

    disconnected = []
    sent = 0
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
            sent += 1
        except Exception:
            disconnected.append(ws)

    # Clean up disconnected clients
    for ws in disconnected:
        try:
            _ws_clients.remove(ws)
        except ValueError:
            pass

    return sent


def broadcast_signals_sync(signals: list[dict[str, Any]]) -> int:
    """Synchronous wrapper for broadcast_signals.

    Use this from non-async code (e.g., signal_generator).
    """
    import asyncio

    if not _ws_clients or not signals:
        return 0

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in an event loop — schedule as task
            import threading
            result = [0]

            def _broadcast():
                async def _run():
                    result[0] = await broadcast_signals(signals)
                asyncio.run_coroutine_threadsafe(_run(), loop)

            threading.Thread(target=_broadcast).start()
            return result[0]
        else:
            return loop.run_until_complete(broadcast_signals(signals))
    except RuntimeError:
        # No event loop at all
        return asyncio.run(broadcast_signals(signals))


@app.on_event("startup")
def _prewarm_data_caches():
    """Warm segment/minute caches in the background.

    minute_coverage hits the Windows API and stalls ~20s cold; base_stats is ~3s.
    Pre-warming them at startup means the first /dashboard/data request (and the
    rebuild modal's windows-health) are already hot by the time a user opens #data.
    """
    import threading
    from src.data import stats

    def _warm():
        try:
            stats._base_stats()
            stats.trading_calendar()  # pre-warm to avoid 200s Windows API hit
            stats.minute_coverage()
        except Exception:
            pass

    threading.Thread(daemon=True, target=_warm, name="prewarm-caches").start()


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------

def start_server(host: str = "0.0.0.0", port: int | None = None):
    """Start the API server (blocking).

    Args:
        host: Bind address.
        port: Port override (defaults to config.ini [live] api_port).
    """
    import uvicorn

    if port is None:
        port = LIVE_API_PORT

    uvicorn.run(app, host=host, port=port, log_level="info")

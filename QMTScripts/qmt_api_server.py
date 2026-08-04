"""QMT 数据 API 服务（Windows 端）

读取 qmt_strategy_数据导出.py / qmt_strategy_分钟数据导出.py 写入的 SQLite，
向 Mac 端 QTrade（src/data/qmt_fetcher.py）提供日线和分钟线 HTTP API。

运行（Windows）：
    pip install fastapi uvicorn
    uvicorn qmt_api_server:app --host 0.0.0.0 --port 8001

端点：
    日线:
    GET /api/health                                    服务与数据状态
    GET /api/trade_dates?start_date=&end_date=         交易日历（distinct trade_date 升序）
    GET /api/daily?trade_date=YYYYMMDD                 单日全市场日线
    GET /api/daily/stock?ts_code=&start_date=&end_date= 单股区间日线
    分钟线:
    GET /api/minute_kline?ts_code=&start=&end=         单股区间5mK线
    GET /api/minute_kline/available?ts_code=           该股分钟数据覆盖的日期列表
"""

import os
import sqlite3

from fastapi import FastAPI, HTTPException, Query

DB_PATH = r"C:\quant_data\stock_data.db"  # 与 qmt_strategy_数据导出.py 保持一致

app = FastAPI(title="QMT Daily Data API")

_COLUMNS = "trade_date, ts_code, open, high, low, close, vol, amount, is_trading"


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """查询（仅 SELECT）。

    注意：不用 mode=ro 只读连接——WAL 库需要恢复时只读连接会报
    "unable to open database file"；普通连接配合策略端的 WAL 模式可安全并发读。
    """
    if not os.path.exists(DB_PATH):
        raise HTTPException(
            status_code=503,
            detail=f"数据库文件不存在: {DB_PATH}，请先运行 qmt_strategy_数据导出.py 完成导出",
        )
    try:
        conn = sqlite3.connect(DB_PATH, timeout=2)
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=503, detail=f"数据库不可用: {e}")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=2000")  # 2s max wait on WAL lock
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise HTTPException(
                status_code=503,
                detail="数据库繁忙（QMT 正在写入），请稍后重试",
            )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/health")
def health():
    daily = _query(
        "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT ts_code) AS stock_count, "
        "MIN(trade_date) AS earliest_date, MAX(trade_date) AS latest_date FROM daily_price"
    )
    result = {"status": "ok", **daily[0]}
    # Minute data: light check — only verify table exists, don't scan data
    # (scanning MIN/MAX on a 2.5M-row table blocks when QMT is writing)
    try:
        table_check = _query(
            "SELECT COUNT(*) AS cnt FROM sqlite_master "
            "WHERE type='table' AND name='kline_5m'"
        )
        if table_check and table_check[0].get("cnt", 0) > 0:
            result["minute_status"] = "available"
        else:
            result["minute_status"] = "table not created"
    except Exception:
        result["minute_status"] = "unavailable (locked)"
    return result


@app.get("/api/trade_dates")
def trade_dates(
    start_date: str = Query("", description="起始日期 YYYYMMDD，可选"),
    end_date: str = Query("", description="结束日期 YYYYMMDD，可选"),
):
    sql = "SELECT DISTINCT trade_date FROM daily_price"
    conds, params = [], []
    if start_date:
        conds.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        conds.append("trade_date <= ?")
        params.append(end_date)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY trade_date"
    dates = [r["trade_date"] for r in _query(sql, tuple(params))]
    return {"count": len(dates), "trade_dates": dates}


@app.get("/api/daily")
def daily(trade_date: str = Query(..., description="交易日 YYYYMMDD")):
    items = _query(
        f"SELECT {_COLUMNS} FROM daily_price WHERE trade_date = ? ORDER BY ts_code",
        (trade_date,),
    )
    return {"trade_date": trade_date, "count": len(items), "items": items}


@app.get("/api/daily/stock")
def daily_stock(
    ts_code: str = Query(..., description="股票代码，如 000001.SZ"),
    start_date: str = Query("", description="起始日期 YYYYMMDD，可选"),
    end_date: str = Query("", description="结束日期 YYYYMMDD，可选"),
):
    sql = f"SELECT {_COLUMNS} FROM daily_price WHERE ts_code = ?"
    params = [ts_code]
    if start_date:
        sql += " AND trade_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= ?"
        params.append(end_date)
    sql += " ORDER BY trade_date"
    items = _query(sql, tuple(params))
    return {"ts_code": ts_code, "count": len(items), "items": items}


# ============================================================
# 分钟K线数据（从 kline_5m 表读取，网格回测等场景使用）
# ============================================================

_MINUTE_COLUMNS = "bar_time, ts_code, open, high, low, close, vol, amount, is_trading"


@app.get("/api/minute_kline/available")
def minute_kline_available(
    ts_code: str = Query(..., description="股票代码"),
):
    """返回该股票分钟数据覆盖的日期列表（distinct bar_time 前8位）。"""
    rows = _query(
        "SELECT DISTINCT SUBSTR(bar_time, 1, 8) AS trade_date "
        "FROM kline_5m WHERE ts_code = ? ORDER BY trade_date",
        (ts_code,),
    )
    return {
        "ts_code": ts_code,
        "trade_dates": [r["trade_date"] for r in rows],
    }


# Cached minute aggregate stats — scanning kline_5m is expensive (~2.5M rows)
# and blocks when QMT is writing, so cache for 300s.
_MINUTE_STATS_CACHE: dict | None = None
_MINUTE_STATS_CACHE_TS: float = 0.0
_MINUTE_STATS_CACHE_TTL = 300.0


@app.get("/api/minute_kline/stats")
def minute_kline_stats():
    """Aggregate minute-data stats (total rows, stock count, date range).

    Cached for 300s to avoid repeated full-table scans on the 2.5M-row
    kline_5m table while QMT may be writing.
    """
    import time as _time
    global _MINUTE_STATS_CACHE, _MINUTE_STATS_CACHE_TS
    now = _time.time()
    if _MINUTE_STATS_CACHE is not None and (now - _MINUTE_STATS_CACHE_TS) < _MINUTE_STATS_CACHE_TTL:
        return _MINUTE_STATS_CACHE

    try:
        table_check = _query(
            "SELECT COUNT(*) AS cnt FROM sqlite_master "
            "WHERE type='table' AND name='kline_5m'"
        )
        if not table_check or table_check[0].get("cnt", 0) == 0:
            _MINUTE_STATS_CACHE = {"status": "unavailable", "total_rows": 0,
                                   "stock_count": 0, "earliest_date": None,
                                   "latest_date": None}
            _MINUTE_STATS_CACHE_TS = now
            return _MINUTE_STATS_CACHE
    except Exception:
        return {"status": "unavailable", "total_rows": 0, "stock_count": 0,
                "earliest_date": None, "latest_date": None}

    try:
        stats = _query(
            "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT ts_code) AS stock_count, "
            "MIN(SUBSTR(bar_time,1,8)) AS earliest_date, "
            "MAX(SUBSTR(bar_time,1,8)) AS latest_date FROM kline_5m"
        )
        result = {"status": "ok", **stats[0]}
    except Exception:
        result = {"status": "unavailable (locked)", "total_rows": 0,
                  "stock_count": 0, "earliest_date": None, "latest_date": None}
    _MINUTE_STATS_CACHE = result
    _MINUTE_STATS_CACHE_TS = now
    return result


@app.get("/api/minute_kline")
def minute_kline(
    ts_code: str = Query(..., description="股票代码"),
    start: str = Query("", description="起始 YYYYMMDDHHMMSS 或 YYYYMMDD"),
    end: str = Query("", description="结束 YYYYMMDDHHMMSS 或 YYYYMMDD"),
):
    """返回单股分钟K线数据，支持按时间区间过滤。

    若 start/end 仅提供8位日期，自动前缀匹配。
    """
    sql = f"SELECT {_MINUTE_COLUMNS} FROM kline_5m WHERE ts_code = ?"
    params: list = [ts_code]
    if start:
        # If only YYYYMMDD (8 chars), expand to HHMMSS for bar_time matching
        s_val = start if len(start) > 8 else start + "000000"
        sql += " AND bar_time >= ?"
        params.append(s_val)
    if end:
        e_val = end if len(end) > 8 else end + "235959"
        sql += " AND bar_time <= ?"
        params.append(e_val)
    sql += " ORDER BY bar_time"
    items = _query(sql, tuple(params))
    return {
        "ts_code": ts_code,
        "count": len(items),
        "items": items,
    }


@app.get("/api/minute_kline/daily")
def minute_kline_daily(
    trade_date: str = Query(..., description="交易日 YYYYMMDD"),
):
    """全市场单日分钟K线，ORDER BY ts_code, bar_time。

    一次 HTTP 返回当日全部股票的 5 分钟 K 线，替代逐股票调用。
    """
    sql = (
        f"SELECT {_MINUTE_COLUMNS} FROM kline_5m "
        "WHERE bar_time >= ? AND bar_time <= ? ORDER BY ts_code, bar_time"
    )
    items = _query(sql, (trade_date + "000000", trade_date + "235959"))
    return {"trade_date": trade_date, "count": len(items), "items": items}

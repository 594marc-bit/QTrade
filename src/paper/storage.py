"""实盘模拟数据层 — 5 张专用表 + worker 心跳表的 DDL 与 CRUD。

所有表与 ``trade_signals`` 物理隔离，外键 ``ON DELETE CASCADE``（删除方案时
自动清理其信号/持仓/流水/净值）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.data.storage import get_connection as _get_base_connection


def get_connection() -> sqlite3.Connection:
    """paper 模块专用连接：复用既有 WAL 连接 + 开启外键 + Row 工厂。"""
    conn = _get_base_connection()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def _ensure_tables(conn: sqlite3.Connection) -> None:
    """首次访问时建 6 张表（幂等）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paper_plans (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT NOT NULL,
            scheme_name           TEXT NOT NULL,
            index_codes           TEXT,
            total_capital         REAL NOT NULL,
            start_date            TEXT NOT NULL,
            top_n                 INTEGER DEFAULT 10,
            position_sizing       TEXT DEFAULT 'equal_weight',
            uses_intraday_factors INTEGER DEFAULT 0,
            freq_type             TEXT NOT NULL CHECK(freq_type IN ('interval','cron')),
            freq_spec             TEXT NOT NULL,
            price_source          TEXT DEFAULT 'auto',
            slippage              REAL DEFAULT 0.0,
            exclude_etf           INTEGER DEFAULT 1,
            exclude_star          INTEGER DEFAULT 1,
            status                TEXT DEFAULT 'stopped'
                                  CHECK(status IN ('stopped','running','paused')),
            mode                  TEXT NOT NULL DEFAULT 'paper'
                                  CHECK(mode IN ('paper','live')),
            cash                  REAL DEFAULT 0,
            last_run_at           TEXT,
            last_signal_date      TEXT,
            next_run_at           TEXT,
            error_msg             TEXT,
            created_at            TEXT DEFAULT (datetime('now','localtime')),
            started_at            TEXT,
            updated_at            TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS paper_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id         INTEGER NOT NULL REFERENCES paper_plans(id) ON DELETE CASCADE,
            ts_code         TEXT NOT NULL,
            action          TEXT NOT NULL CHECK(action IN ('BUY','SELL')),
            quantity        INTEGER NOT NULL,
            scheme_name     TEXT,
            rebalance_date  TEXT NOT NULL,
            tick_ts         TEXT NOT NULL,
            status          TEXT DEFAULT 'pending'
                            CHECK(status IN ('pending','filled','rejected','skipped')),
            fill_price      REAL,
            fill_qty        INTEGER,
            fill_ts         TEXT,
            price_source    TEXT,
            error_msg       TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS paper_holdings (
            plan_id      INTEGER NOT NULL REFERENCES paper_plans(id) ON DELETE CASCADE,
            ts_code      TEXT NOT NULL,
            shares       INTEGER NOT NULL DEFAULT 0,
            t1_shares    INTEGER NOT NULL DEFAULT 0,
            free_shares  INTEGER NOT NULL DEFAULT 0,
            avg_cost     REAL NOT NULL DEFAULT 0,
            last_price   REAL,
            last_price_ts TEXT,
            last_price_src TEXT,
            updated_at   TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (plan_id, ts_code)
        );

        CREATE TABLE IF NOT EXISTS paper_transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id         INTEGER NOT NULL REFERENCES paper_plans(id) ON DELETE CASCADE,
            signal_id       INTEGER REFERENCES paper_signals(id) ON DELETE SET NULL,
            ts_code         TEXT NOT NULL,
            action          TEXT NOT NULL CHECK(action IN ('BUY','SELL')),
            quantity        INTEGER NOT NULL,
            fill_price      REAL NOT NULL,
            gross_amount    REAL NOT NULL,
            commission      REAL NOT NULL DEFAULT 0,
            stamp_tax       REAL NOT NULL DEFAULT 0,
            transfer_fee    REAL NOT NULL DEFAULT 0,
            total_cost      REAL NOT NULL,
            net_amount      REAL NOT NULL,
            cash_after      REAL NOT NULL,
            price_source    TEXT,
            executed_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            note            TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_equity_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id         INTEGER NOT NULL REFERENCES paper_plans(id) ON DELETE CASCADE,
            snapshot_ts     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            trade_date      TEXT,
            cash            REAL NOT NULL,
            holdings_value  REAL NOT NULL,
            total_equity    REAL NOT NULL,
            nav             REAL NOT NULL,
            daily_return    REAL,
            n_positions     INTEGER,
            UNIQUE(plan_id, snapshot_ts)
        );

        CREATE TABLE IF NOT EXISTS paper_worker_heartbeat (
            id              INTEGER PRIMARY KEY CHECK(id = 1),
            last_beat_at    TEXT,
            running_plans   INTEGER DEFAULT 0,
            note            TEXT,
            last_rollover_date TEXT
        );
        """
    )
    # 迁移：为既有库幂等补 last_rollover_date 列
    try:
        conn.execute("ALTER TABLE paper_worker_heartbeat ADD COLUMN last_rollover_date TEXT")
    except sqlite3.OperationalError:
        pass
    # 迁移：为既有库幂等补 exclude_etf 列
    try:
        conn.execute("ALTER TABLE paper_plans ADD COLUMN exclude_etf INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    # 迁移：为既有库幂等补 exclude_star 列
    try:
        conn.execute("ALTER TABLE paper_plans ADD COLUMN exclude_star INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    # 迁移：为既有库幂等补 mode 列（paper/live 分派）
    try:
        conn.execute("ALTER TABLE paper_plans ADD COLUMN mode TEXT NOT NULL DEFAULT 'paper'")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def ensure_tables() -> None:
    """模块级便捷入口：获取连接并建表。"""
    conn = get_connection()
    try:
        _ensure_tables(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_plans
# ---------------------------------------------------------------------------

PLAN_FIELDS = (
    "name", "scheme_name", "index_codes", "total_capital", "start_date",
    "top_n", "position_sizing", "uses_intraday_factors", "freq_type",
    "freq_spec", "price_source", "slippage", "exclude_etf", "exclude_star",
    "mode",
)


def create_plan(
    name: str,
    scheme_name: str,
    total_capital: float,
    start_date: str,
    freq_type: str,
    freq_spec: str,
    top_n: int = 10,
    position_sizing: str = "equal_weight",
    uses_intraday_factors: int = 0,
    index_codes: str | None = None,
    price_source: str = "auto",
    slippage: float = 0.0,
    exclude_etf: int = 1,
    exclude_star: int = 1,
    mode: str = "paper",
) -> int:
    """插入一个新方案，返回 plan id。cash 初始化 = total_capital。"""
    conn = get_connection()
    try:
        _ensure_tables(conn)
        cur = conn.execute(
            f"""INSERT INTO paper_plans ({", ".join(PLAN_FIELDS)}, cash)
                VALUES ({", ".join(["?"] * len(PLAN_FIELDS))}, ?)""",
            (name, scheme_name, index_codes, total_capital, start_date,
             top_n, position_sizing, uses_intraday_factors, freq_type,
             freq_spec, price_source, slippage, exclude_etf, exclude_star,
             mode, total_capital),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _row_to_plan(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_plan(plan_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT * FROM paper_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return _row_to_plan(row) if row else None
    finally:
        conn.close()


def list_plans(status: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        if status:
            rows = conn.execute(
                "SELECT * FROM paper_plans WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_plans ORDER BY id"
            ).fetchall()
        return [_row_to_plan(r) for r in rows]
    finally:
        conn.close()


def update_plan_status(plan_id: int, status: str) -> bool:
    """只改 status；started_at 在首次 running 时写入。"""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE paper_plans
               SET status = ?,
                   started_at = COALESCE(started_at,
                                         CASE WHEN ? = 'running' THEN datetime('now','localtime') END),
                   updated_at = datetime('now','localtime')
               WHERE id = ?""",
            (status, status, plan_id),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def update_plan_runtime(
    plan_id: int,
    *,
    cash: float | None = None,
    last_run_at: str | None = None,
    last_signal_date: str | None = None,
    next_run_at: str | None = None,
    error_msg: str | None = None,
    clear_error: bool = False,
) -> bool:
    """批量更新运行态字段（None 的字段不动）。"""
    sets: list[str] = []
    vals: list[Any] = []
    if cash is not None:
        sets.append("cash = ?"); vals.append(cash)
    if last_run_at is not None:
        sets.append("last_run_at = ?"); vals.append(last_run_at)
    if last_signal_date is not None:
        sets.append("last_signal_date = ?"); vals.append(last_signal_date)
    if next_run_at is not None:
        sets.append("next_run_at = ?"); vals.append(next_run_at)
    if clear_error:
        sets.append("error_msg = NULL")
    elif error_msg is not None:
        sets.append("error_msg = ?"); vals.append(error_msg)
    if not sets:
        return False
    sets.append("updated_at = datetime('now','localtime')")
    vals.append(plan_id)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE paper_plans SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def delete_plan(plan_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM paper_plans WHERE id = ?", (plan_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_holdings
# ---------------------------------------------------------------------------

def get_holdings(plan_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM paper_holdings WHERE plan_id = ? ORDER BY ts_code",
            (plan_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_holding(plan_id: int, ts_code: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM paper_holdings WHERE plan_id = ? AND ts_code = ?",
            (plan_id, ts_code),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_holding(
    plan_id: int,
    ts_code: str,
    *,
    shares: int,
    t1_shares: int,
    free_shares: int,
    avg_cost: float,
    last_price: float | None = None,
    last_price_src: str | None = None,
) -> None:
    """整行覆盖写入（executor 计算好新值后调用）。"""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO paper_holdings
               (plan_id, ts_code, shares, t1_shares, free_shares, avg_cost,
                last_price, last_price_src, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
               ON CONFLICT(plan_id, ts_code) DO UPDATE SET
                 shares = excluded.shares,
                 t1_shares = excluded.t1_shares,
                 free_shares = excluded.free_shares,
                 avg_cost = excluded.avg_cost,
                 last_price = CASE WHEN excluded.last_price IS NOT NULL
                              THEN excluded.last_price ELSE paper_holdings.last_price END,
                 last_price_src = CASE WHEN excluded.last_price_src IS NOT NULL
                                 THEN excluded.last_price_src ELSE paper_holdings.last_price_src END,
                 updated_at = datetime('now','localtime')""",
            (plan_id, ts_code, shares, t1_shares, free_shares, avg_cost,
             last_price, last_price_src),
        )
        conn.commit()
    finally:
        conn.close()


def update_holding_price(
    plan_id: int, ts_code: str, last_price: float, src: str, ts: str
) -> None:
    """盯盘：只刷 last_price。"""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE paper_holdings
               SET last_price = ?, last_price_src = ?, last_price_ts = ?,
                   updated_at = datetime('now','localtime')
               WHERE plan_id = ? AND ts_code = ?""",
            (last_price, src, ts, plan_id, ts_code),
        )
        conn.commit()
    finally:
        conn.close()


def delete_holding(plan_id: int, ts_code: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM paper_holdings WHERE plan_id = ? AND ts_code = ?",
            (plan_id, ts_code),
        )
        conn.commit()
    finally:
        conn.close()


def set_target_holdings(plan_id: int, holdings: list[dict[str, Any]]) -> int:
    """用目标持仓整体替换某方案的 paper_holdings（live 模式记录持仓用）。

    ``holdings`` 每项含 ``ts_code`` / ``shares`` / ``avg_cost`` / ``last_price``；
    写入时 free_shares=shares、t1_shares=0（live 不模拟 T+1 锁定）。
    """
    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute("DELETE FROM paper_holdings WHERE plan_id = ?", (plan_id,))
        for h in holdings:
            shares = int(h["shares"])
            conn.execute(
                """INSERT INTO paper_holdings
                   (plan_id, ts_code, shares, t1_shares, free_shares, avg_cost,
                    last_price, updated_at)
                   VALUES (?, ?, ?, 0, ?, ?, ?, datetime('now','localtime'))""",
                (plan_id, h["ts_code"], shares, shares,
                 float(h.get("avg_cost", 0.0) or 0.0), h.get("last_price")),
            )
        conn.commit()
        return len(holdings)
    finally:
        conn.close()


def rollover_locks(plan_id: int) -> int:
    """T+1 解锁：free_shares += t1_shares; t1_shares = 0。返回受影响行数。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            """UPDATE paper_holdings
               SET free_shares = free_shares + t1_shares,
                   t1_shares = 0,
                   updated_at = datetime('now','localtime')
               WHERE plan_id = ? AND t1_shares > 0""",
            (plan_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def rollover_all_plans() -> list[int]:
    """对所有持有 t1 持仓的方案执行 T+1 解锁（不限 status）。

    用于 worker 的每日维护：与单方案 tick 解耦，即使某方案当日尚未触发 tick
    （如 cron 未到点、paused/stopped），昨日的买入也能在新的交易日解锁。
    幂等：t1 已为 0 时为 no-op。
    """
    conn = get_connection()
    try:
        _ensure_tables(conn)
        plan_ids = [r["plan_id"] for r in conn.execute(
            "SELECT DISTINCT plan_id FROM paper_holdings WHERE t1_shares > 0"
        ).fetchall()]
        if plan_ids:
            conn.executemany(
                """UPDATE paper_holdings
                   SET free_shares = free_shares + t1_shares,
                       t1_shares = 0,
                       updated_at = datetime('now','localtime')
                   WHERE plan_id = ? AND t1_shares > 0""",
                [(pid,) for pid in plan_ids],
            )
            conn.commit()
        return plan_ids
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_signals
# ---------------------------------------------------------------------------

SIGNAL_FIELDS = (
    "plan_id", "ts_code", "action", "quantity", "scheme_name",
    "rebalance_date", "tick_ts",
)


def save_signals(rows: list[dict[str, Any]]) -> int:
    """批量插入信号行（status 默认 pending）。返回插入数。"""
    if not rows:
        return 0
    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.executemany(
            f"""INSERT INTO paper_signals ({", ".join(SIGNAL_FIELDS)})
                VALUES ({", ".join(["?"] * len(SIGNAL_FIELDS))})""",
            [(r["plan_id"], r["ts_code"], r["action"], r["quantity"],
              r.get("scheme_name"), r["rebalance_date"], r["tick_ts"])
             for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def create_signals_with_ids(
    plan_id: int,
    raw_signals: list[dict[str, Any]],
    *,
    tick_ts: str,
    scheme_name: str | None,
    rebalance_date: str,
) -> list[dict[str, Any]]:
    """插入信号并回填 id，供 executor 标记成交/拒绝。

    Args:
        plan_id: 方案 id。
        raw_signals: ``[{ts_code, action, quantity}, ...]``。
        tick_ts: 生成时刻。
        scheme_name / rebalance_date: 冗余字段。

    Returns:
        插入后的信号列表，每项含 ``id``。
    """
    if not raw_signals:
        return []
    conn = get_connection()
    try:
        _ensure_tables(conn)
        out: list[dict[str, Any]] = []
        for s in raw_signals:
            row = {
                "plan_id": plan_id, "ts_code": s["ts_code"],
                "action": s["action"], "quantity": s["quantity"],
                "scheme_name": scheme_name, "rebalance_date": rebalance_date,
                "tick_ts": tick_ts,
            }
            cur = conn.execute(
                f"""INSERT INTO paper_signals ({", ".join(SIGNAL_FIELDS)})
                    VALUES ({", ".join(["?"] * len(SIGNAL_FIELDS))})""",
                (row["plan_id"], row["ts_code"], row["action"], row["quantity"],
                 row["scheme_name"], row["rebalance_date"], row["tick_ts"]),
            )
            row["id"] = int(cur.lastrowid)
            out.append(row)
        conn.commit()
        return out
    finally:
        conn.close()


def _set_signal_status(
    signal_id: int, status: str, extra: dict[str, Any] | None = None
) -> bool:
    sets = ["status = ?"]
    vals: list[Any] = [status]
    if extra:
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
    vals.append(signal_id)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE paper_signals SET {', '.join(sets)} WHERE id = ?", vals
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def mark_filled(
    signal_id: int, fill_price: float, fill_qty: int,
    price_source: str, ts: str,
) -> bool:
    return _set_signal_status(
        signal_id, "filled",
        {"fill_price": fill_price, "fill_qty": fill_qty,
         "price_source": price_source, "fill_ts": ts},
    )


def mark_rejected(signal_id: int, error_msg: str) -> bool:
    return _set_signal_status(signal_id, "rejected", {"error_msg": error_msg})


def mark_skipped(signal_id: int, reason: str | None = None) -> bool:
    return _set_signal_status(
        signal_id, "skipped",
        {"error_msg": reason} if reason else None,
    )


def list_signals(
    plan_id: int, status: str | None = None, limit: int | None = None,
) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM paper_signals WHERE plan_id = ?"
        params: list[Any] = [plan_id]
        if status:
            sql += " AND status = ?"; params.append(status)
        sql += " ORDER BY id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_transactions
# ---------------------------------------------------------------------------

TX_FIELDS = (
    "plan_id", "signal_id", "ts_code", "action", "quantity", "fill_price",
    "gross_amount", "commission", "stamp_tax", "transfer_fee", "total_cost",
    "net_amount", "cash_after", "price_source", "note",
)


def append_transaction(row: dict[str, Any]) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            f"""INSERT INTO paper_transactions ({", ".join(TX_FIELDS)})
                VALUES ({", ".join(["?"] * len(TX_FIELDS))})""",
            [row.get(f) for f in TX_FIELDS],
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_transactions(plan_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM paper_transactions WHERE plan_id = ? ORDER BY id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, (plan_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sum_fees(plan_id: int) -> dict[str, float]:
    """汇总佣金/印花税/过户费/总成本/成交笔数。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(commission), 0)   AS commission,
                 COALESCE(SUM(stamp_tax), 0)    AS stamp_tax,
                 COALESCE(SUM(transfer_fee), 0) AS transfer_fee,
                 COALESCE(SUM(total_cost), 0)   AS total_cost,
                 COUNT(*)                       AS trade_count
               FROM paper_transactions WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()
        return dict(row) if row else {
            "commission": 0, "stamp_tax": 0, "transfer_fee": 0,
            "total_cost": 0, "trade_count": 0,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_equity_history
# ---------------------------------------------------------------------------

def append_snapshot(
    plan_id: int,
    *,
    trade_date: str | None,
    cash: float,
    holdings_value: float,
    total_equity: float,
    nav: float,
    daily_return: float | None = None,
    n_positions: int = 0,
    snapshot_ts: str | None = None,
) -> int:
    conn = get_connection()
    try:
        if snapshot_ts is None:
            snapshot_ts_sql = "datetime('now','localtime')"
            cur = conn.execute(
                f"""INSERT OR REPLACE INTO paper_equity_history
                    (plan_id, trade_date, cash, holdings_value, total_equity,
                     nav, daily_return, n_positions, snapshot_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, {snapshot_ts_sql})""",
                (plan_id, trade_date, cash, holdings_value, total_equity,
                 nav, daily_return, n_positions),
            )
        else:
            cur = conn.execute(
                """INSERT OR REPLACE INTO paper_equity_history
                   (plan_id, trade_date, cash, holdings_value, total_equity,
                    nav, daily_return, n_positions, snapshot_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, trade_date, cash, holdings_value, total_equity,
                 nav, daily_return, n_positions, snapshot_ts),
            )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_equity(plan_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        sql = (
            "SELECT * FROM paper_equity_history WHERE plan_id = ? "
            "ORDER BY snapshot_ts ASC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, (plan_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_equity(plan_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM paper_equity_history WHERE plan_id = ? "
            "ORDER BY snapshot_ts DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paper_worker_heartbeat
# ---------------------------------------------------------------------------

def update_heartbeat(
    running_plans: int, note: str | None = None, last_rollover_date: str | None = None,
) -> None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        rollover_set = ", last_rollover_date = ?" if last_rollover_date is not None else ""
        rollover_val = (last_rollover_date,) if last_rollover_date is not None else ()
        conn.execute(
            f"""INSERT INTO paper_worker_heartbeat (id, last_beat_at, running_plans, note)
               VALUES (1, datetime('now','localtime'), ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 last_beat_at = datetime('now','localtime'),
                 running_plans = excluded.running_plans,
                 note = excluded.note{rollover_set}""",
            (running_plans, note) + rollover_val,
        )
        conn.commit()
    finally:
        conn.close()


def get_heartbeat() -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM paper_worker_heartbeat WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

"""Data storage module for persisting stock data to SQLite and CSV."""

import sqlite3

import pandas as pd

from src.config import DATA_DIR, DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get SQLite database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table_and_index(conn: sqlite3.Connection):
    """Ensure daily_price table exists with UNIQUE constraint on (trade_date, ts_code)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_price (
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            vol REAL,
            amount REAL,
            is_trading REAL
        )
    """)

    # Create unique index (idempotent)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_unique "
            "ON daily_price (trade_date, ts_code)"
        )
    except sqlite3.OperationalError:
        pass  # Index already exists

    conn.commit()


def save_daily_price(df: pd.DataFrame, if_exists: str = "append") -> int:
    """Save daily price data to SQLite using INSERT OR REPLACE (incremental UPSERT).

    Does NOT read existing data into memory. Uses SQLite-native UPSERT
    with UNIQUE constraint on (trade_date, ts_code).

    Args:
        df: DataFrame with columns [trade_date, ts_code, open, high, low, close, vol, amount, ...].
        if_exists: Ignored (kept for API compatibility). Always uses UPSERT.

    Returns:
        Number of rows saved.
    """
    if df.empty:
        return 0

    conn = get_connection()
    _ensure_table_and_index(conn)

    # Determine columns present in DataFrame
    df_cols = [c for c in df.columns if c != "index"]
    placeholders = ", ".join(["?"] * len(df_cols))
    col_names = ", ".join(df_cols)

    sql = f"INSERT OR REPLACE INTO daily_price ({col_names}) VALUES ({placeholders})"

    rows = df[df_cols].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()

    return len(rows)


def load_daily_price(
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load daily price data from SQLite.

    Args:
        ts_codes: List of stock codes to filter, or None for all.
        start_date: Start date YYYYMMDD, or None.
        end_date: End date YYYYMMDD, or None.

    Returns:
        Filtered DataFrame.
    """
    conn = get_connection()

    # Push filters into SQL to avoid loading unnecessary data
    wheres: list[str] = []
    params: list[str] = []
    if start_date:
        wheres.append("trade_date >= ?")
        params.append(start_date)
    if end_date:
        wheres.append("trade_date <= ?")
        params.append(end_date)
    if ts_codes:
        placeholders = ",".join(["?"] * len(ts_codes))
        wheres.append(f"ts_code IN ({placeholders})")
        params.extend(ts_codes)

    where_clause = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    sql = f"SELECT * FROM daily_price{where_clause} ORDER BY trade_date, ts_code"

    try:
        df = pd.read_sql(sql, conn, params=params if params else None)
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    return df


def get_latest_date(ts_code: str | None = None) -> str | None:
    """Get the latest trade_date in the database.

    Args:
        ts_code: Optional stock code to filter by.

    Returns:
        Latest date string YYYYMMDD, or None if no data.
    """
    conn = get_connection()
    try:
        if ts_code:
            result = conn.execute(
                "SELECT MAX(trade_date) FROM daily_price WHERE ts_code = ?",
                (ts_code,),
            ).fetchone()
        else:
            result = conn.execute(
                "SELECT MAX(trade_date) FROM daily_price"
            ).fetchone()
        conn.close()
        return result[0] if result and result[0] else None
    except Exception:
        conn.close()
        return None


def get_latest_date_per_stock(ts_codes: list[str] | None = None) -> dict[str, str]:
    """Get the latest trade_date for each stock in the database.

    Args:
        ts_codes: Optional list of stock codes to filter. If None, returns all stocks.

    Returns:
        Dict mapping ts_code to latest trade_date string.
    """
    conn = get_connection()
    try:
        if ts_codes:
            placeholders = ",".join(["?"] * len(ts_codes))
            query = (
                f"SELECT ts_code, MAX(trade_date) as latest_date "
                f"FROM daily_price WHERE ts_code IN ({placeholders}) GROUP BY ts_code"
            )
            rows = conn.execute(query, tuple(ts_codes)).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts_code, MAX(trade_date) as latest_date "
                "FROM daily_price GROUP BY ts_code"
            ).fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        conn.close()
        return {}


# ============================================================
#  Minute 5m K-line table — local cache of Windows kline_5m
# ============================================================

_MINUTE_COLS = ["bar_time", "ts_code", "open", "high", "low", "close", "vol", "amount", "is_trading"]


def _ensure_minute_5m_table(conn: sqlite3.Connection):
    """Ensure minute_5m table exists with UNIQUE constraint on (bar_time, ts_code)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS minute_5m (
            bar_time TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            vol REAL,
            amount REAL,
            is_trading REAL
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_minute_5m_unique "
            "ON minute_5m (bar_time, ts_code)"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def save_minute_kline(df: pd.DataFrame) -> int:
    """Save minute 5m K-line data using INSERT OR REPLACE (UPSERT).

    Args:
        df: DataFrame with columns matching minute_5m schema.

    Returns:
        Number of rows saved.
    """
    if df.empty:
        return 0
    conn = get_connection()
    _ensure_minute_5m_table(conn)
    cols = [c for c in _MINUTE_COLS if c in df.columns]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO minute_5m ({', '.join(cols)}) VALUES ({placeholders})"
    rows = df[cols].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()
    return len(rows)


def load_minute_kline(ts_code: str, trade_date: str) -> pd.DataFrame:
    """Load 5-minute K-line bars for one stock on one trading day.

    Args:
        ts_code: e.g. '000001.SZ'
        trade_date: YYYYMMDD

    Returns:
        DataFrame with columns matching minute_5m, sorted by bar_time.
    """
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM minute_5m WHERE ts_code = ? AND bar_time >= ? AND bar_time <= ? "
            "ORDER BY bar_time",
            conn,
            params=(ts_code, trade_date + "000000", trade_date + "235959"),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


def load_minute_range(
    ts_code: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Load 5-minute bars for a stock over a date range.

    Args:
        ts_code: e.g. '000001.SZ'
        start_date / end_date: YYYYMMDD

    Returns:
        DataFrame sorted by bar_time.
    """
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM minute_5m WHERE ts_code = ? "
            "AND bar_time >= ? AND bar_time <= ? ORDER BY bar_time",
            conn,
            params=(ts_code, start_date + "000000", end_date + "235959"),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


def load_minute_daily(trade_date: str) -> pd.DataFrame:
    """Load ALL stocks' 5-minute bars for one trading day.

    Args:
        trade_date: YYYYMMDD

    Returns:
        DataFrame sorted by ts_code, bar_time.
    """
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT * FROM minute_5m WHERE bar_time >= ? AND bar_time <= ? "
            "ORDER BY ts_code, bar_time",
            conn,
            params=(trade_date + "000000", trade_date + "235959"),
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    return df


def get_latest_minute_date() -> str | None:
    """Get the latest trade date in minute_5m."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT SUBSTR(MAX(bar_time), 1, 8) FROM minute_5m"
        ).fetchone()
    except Exception:
        row = [None]
    finally:
        conn.close()
    return row[0] if row else None


def _ensure_daily_basic_table(conn: sqlite3.Connection):
    """Ensure daily_basic table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_basic (
            trade_date TEXT NOT NULL,
            ts_code TEXT NOT NULL,
            pe_ttm REAL,
            pb REAL,
            ps_ttm REAL
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_basic_unique "
            "ON daily_basic (trade_date, ts_code)"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def save_daily_basic(df: pd.DataFrame) -> int:
    """Save daily basic data to SQLite using UPSERT."""
    if df.empty:
        return 0

    conn = get_connection()
    _ensure_daily_basic_table(conn)

    df_cols = [c for c in df.columns if c not in ("index",)]
    placeholders = ", ".join(["?"] * len(df_cols))
    col_names = ", ".join(df_cols)

    sql = f"INSERT OR REPLACE INTO daily_basic ({col_names}) VALUES ({placeholders})"

    rows = df[df_cols].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()

    return len(rows)


def _ensure_adj_factor_table(conn: sqlite3.Connection):
    """Ensure adj_factor table exists for qfq adjustment factor storage."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS adj_factor (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            adj_factor REAL NOT NULL
        )
    """)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_adj_factor_unique "
            "ON adj_factor (ts_code, trade_date)"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def save_adj_factor(df: pd.DataFrame) -> int:
    """Save adj_factor data to SQLite using UPSERT.

    Args:
        df: DataFrame with columns [ts_code, trade_date, adj_factor].

    Returns:
        Number of rows saved.
    """
    if df.empty:
        return 0

    conn = get_connection()
    _ensure_adj_factor_table(conn)

    cols = ["ts_code", "trade_date", "adj_factor"]
    available_cols = [c for c in cols if c in df.columns]
    if len(available_cols) < 3:
        conn.close()
        return 0

    placeholders = ", ".join(["?"] * len(available_cols))
    col_names = ", ".join(available_cols)

    sql = f"INSERT OR REPLACE INTO adj_factor ({col_names}) VALUES ({placeholders})"

    rows = df[available_cols].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()

    return len(rows)


def load_adj_factor(
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load adj_factor data from SQLite.

    Args:
        ts_codes: List of stock codes to filter, or None for all.
        start_date: Start date YYYYMMDD, or None.
        end_date: End date YYYYMMDD, or None.

    Returns:
        DataFrame with columns: ts_code, trade_date, adj_factor.
    """
    conn = get_connection()
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='adj_factor'"
        ).fetchall()
    except Exception:
        conn.close()
        return pd.DataFrame()

    if not tables:
        conn.close()
        return pd.DataFrame()

    try:
        df = pd.read_sql("SELECT ts_code, trade_date, adj_factor FROM adj_factor", conn)
    except Exception:
        conn.close()
        return pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df

    if ts_codes:
        df = df[df["ts_code"].isin(ts_codes)]
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def get_latest_adj_factor_dates(ts_codes: list[str] | None = None) -> dict[str, str]:
    """Get the latest adj_factor date for each stock.

    Args:
        ts_codes: List of stock codes to filter. If None, returns all.

    Returns:
        Dict mapping ts_code to latest trade_date string.
    """
    conn = get_connection()
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='adj_factor'"
        ).fetchall()
    except Exception:
        conn.close()
        return {}

    if not tables:
        conn.close()
        return {}

    try:
        if ts_codes:
            placeholders = ",".join(["?"] * len(ts_codes))
            rows = conn.execute(
                f"SELECT ts_code, MAX(trade_date) FROM adj_factor "
                f"WHERE ts_code IN ({placeholders}) GROUP BY ts_code",
                tuple(ts_codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts_code, MAX(trade_date) FROM adj_factor GROUP BY ts_code"
            ).fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        conn.close()
        return {}


def load_daily_basic(
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load daily basic data from SQLite.

    Args:
        start_date: Start date YYYYMMDD, or None.
        end_date: End date YYYYMMDD, or None.

    Returns:
        DataFrame with columns: trade_date, ts_code, pe_ttm, pb, ps_ttm.
    """
    conn = get_connection()

    try:
        df = pd.read_sql("SELECT * FROM daily_basic", conn)
    except Exception:
        conn.close()
        return pd.DataFrame()

    conn.close()

    if df.empty:
        return df

    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    df = df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    return df


def merge_fundamentals(price_df: pd.DataFrame, basic_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join daily_basic data onto price DataFrame.

    Args:
        price_df: Main DataFrame with trade_date, ts_code.
        basic_df: DataFrame with trade_date, ts_code, pe_ttm, pb, ps_ttm.

    Returns:
        price_df with pe_ttm, pb, ps_ttm columns added.
    """
    if basic_df.empty:
        for col in ["pe_ttm", "pb", "ps_ttm"]:
            price_df[col] = pd.NA
        return price_df

    merge_cols = ["trade_date", "ts_code"]
    value_cols = [c for c in ["pe_ttm", "pb", "ps_ttm"] if c in basic_df.columns]
    basic_subset = basic_df[merge_cols + value_cols].drop_duplicates(subset=merge_cols)

    return price_df.merge(basic_subset, on=merge_cols, how="left")


def load_fina_indicator(
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load fina_indicator data from SQLite.

    Args:
        ts_codes: List of stock codes to filter, or None for all.
        start_date: Start date YYYYMMDD, or None.
        end_date: End date YYYYMMDD, or None.

    Returns:
        DataFrame with columns: trade_date, ts_code, roe, roe_yoy.
    """
    conn = get_connection()

    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fina_indicator'"
        ).fetchall()
    except Exception:
        conn.close()
        return pd.DataFrame()

    if not tables:
        conn.close()
        return pd.DataFrame()

    try:
        df = pd.read_sql("SELECT ts_code, trade_date, roe, roe_yoy FROM fina_indicator", conn)
    except Exception:
        conn.close()
        return pd.DataFrame()

    conn.close()

    if df.empty:
        return df

    if ts_codes:
        df = df[df["ts_code"].isin(ts_codes)]
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    df = df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    return df


def save_fina_indicator(df: pd.DataFrame, if_exists: str = "append") -> int:
    """Save fina_indicator data to SQLite.

    Args:
        df: DataFrame with at least ts_code, end_date, roe, roe_yoy columns.
        if_exists: 'append' or 'replace'.

    Returns:
        Number of rows saved.
    """
    conn = get_connection()
    cols = ["ts_code", "end_date", "roe", "roe_yoy"]
    existing_cols = [c for c in cols if c in df.columns]
    df_to_save = df[existing_cols].copy()
    # Rename end_date to trade_date for consistency with other tables
    if "end_date" in df_to_save.columns:
        df_to_save = df_to_save.rename(columns={"end_date": "trade_date"})
    df_to_save = df_to_save.dropna(subset=["ts_code", "trade_date"])

    if df_to_save.empty:
        conn.close()
        return 0

    try:
        df_to_save.to_sql("fina_indicator", conn, if_exists=if_exists, index=False)
        saved = len(df_to_save)
    except Exception as e:
        saved = 0
    finally:
        conn.close()

    return saved


def merge_fina_indicator(price_df: pd.DataFrame, fina_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join fina_indicator data onto price DataFrame.

    Since fina_indicator reports are quarterly (end_date), this function
    forward-fills the latest report for each stock up to the next report date.

    Args:
        price_df: Main DataFrame with trade_date, ts_code.
        fina_df: DataFrame with trade_date (end_date), ts_code, roe, roe_yoy.

    Returns:
        price_df with roe, roe_yoy columns added.
    """
    if fina_df.empty:
        price_df["roe"] = pd.NA
        price_df["roe_yoy"] = pd.NA
        return price_df

    merge_cols = ["trade_date", "ts_code"]
    value_cols = [c for c in ["roe", "roe_yoy"] if c in fina_df.columns]
    fina_subset = fina_df[merge_cols + value_cols].drop_duplicates(subset=merge_cols)

    merged = price_df.merge(fina_subset, on=merge_cols, how="left")

    # Forward-fill within each stock: use latest available report data
    merged = merged.sort_values(["ts_code", "trade_date"])
    for col in value_cols:
        merged[col] = merged.groupby("ts_code")[col].ffill()

    return merged


# ============================================================
#  Trade Signals table — live trading signal persistence
# ============================================================

VALID_STATUS_TRANSITIONS = {
    "pending": ["sent", "cancelled", "rejected"],
    "sent": ["filled", "partial", "rejected"],
    "partial": ["filled", "rejected"],
    "filled": [],
    "rejected": [],
    "cancelled": [],
}


def _ensure_trade_signals_table(conn: sqlite3.Connection):
    """Ensure trade_signals table exists with all fields and status CHECK constraint."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code         TEXT NOT NULL,
            action          TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
            quantity        INTEGER NOT NULL,
            price_type      TEXT DEFAULT 'MKT' CHECK(price_type IN ('MKT', 'LIMIT')),
            limit_price     REAL,
            scheme_name     TEXT,
            rebalance_date  TEXT NOT NULL,
            status          TEXT DEFAULT 'pending'
                            CHECK(status IN ('pending','sent','filled','partial','rejected','cancelled')),
            broker_order_id TEXT,
            filled_qty      INTEGER DEFAULT 0,
            avg_price       REAL,
            error_msg       TEXT,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            sent_at         TEXT,
            filled_at       TEXT
        )
    """)
    conn.commit()


def save_trade_signals(df: pd.DataFrame) -> int:
    """Save trade signals to SQLite.

    Args:
        df: DataFrame with columns matching trade_signals table.

    Returns:
        Number of rows saved.
    """
    if df.empty:
        return 0

    conn = get_connection()
    _ensure_trade_signals_table(conn)

    db_cols = ["ts_code", "action", "quantity", "price_type", "limit_price",
               "scheme_name", "rebalance_date", "cancel_signal_id"]
    available = [c for c in db_cols if c in df.columns]
    placeholders = ", ".join(["?"] * len(available))
    col_names = ", ".join(available)

    sql = f"INSERT INTO trade_signals ({col_names}) VALUES ({placeholders})"

    rows = df[available].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()

    return len(rows)


def load_trade_signals(status: str | None = None) -> pd.DataFrame:
    """Load trade signals, optionally filtered by status.

    Args:
        status: Filter by signal status (e.g. 'pending', 'sent'), or None for all.

    Returns:
        DataFrame of trade signals.
    """
    conn = get_connection()
    _ensure_trade_signals_table(conn)

    try:
        if status:
            df = pd.read_sql(
                "SELECT * FROM trade_signals WHERE status = ? ORDER BY created_at ASC",
                conn,
                params=(status,),
            )
        else:
            df = pd.read_sql(
                "SELECT * FROM trade_signals ORDER BY created_at DESC", conn
            )
    except Exception:
        conn.close()
        return pd.DataFrame()

    conn.close()
    return df


def update_signal_status(signal_id: int, status: str, **kwargs) -> bool:
    """Update the status of a trade signal with transition validation.

    Args:
        signal_id: The signal ID to update.
        status: New status value.
        **kwargs: Additional fields to update (broker_order_id, filled_qty,
                  avg_price, error_msg).

    Returns:
        True if update succeeded, False if transition is invalid or signal not found.
    """
    conn = get_connection()
    _ensure_trade_signals_table(conn)

    # Read current status
    cur = conn.execute(
        "SELECT status FROM trade_signals WHERE id = ?", (signal_id,)
    ).fetchone()

    if cur is None:
        conn.close()
        return False

    current_status = cur[0]

    # Validate transition
    allowed = VALID_STATUS_TRANSITIONS.get(current_status, [])
    if status not in allowed:
        conn.close()
        return False

    # Build UPDATE statement from kwargs
    set_parts = ["status = ?"]
    params = [status]

    if status == "sent":
        set_parts.append("sent_at = datetime('now','localtime')")
    elif status in ("filled", "partial"):
        set_parts.append("filled_at = datetime('now','localtime')")

    for key in ("broker_order_id", "filled_qty", "avg_price", "error_msg"):
        if key in kwargs:
            set_parts.append(f"{key} = ?")
            params.append(kwargs[key])

    params.append(signal_id)

    sql = f"UPDATE trade_signals SET {', '.join(set_parts)} WHERE id = ?"
    conn.execute(sql, params)
    conn.commit()
    conn.close()

    return True


def get_signal(signal_id: int) -> dict | None:
    """Get a single trade signal by ID.

    Args:
        signal_id: The signal ID to fetch.

    Returns:
        Signal dict if found, None otherwise.
    """
    conn = get_connection()
    _ensure_trade_signals_table(conn)

    cursor = conn.execute(
        "SELECT * FROM trade_signals WHERE id = ?", (signal_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))


def update_signal_fields(signal_id: int, **kwargs) -> bool:
    """Update arbitrary fields on a trade signal (no status-transition validation).

    For admin CRUD use. Does NOT enforce the pending→sent→filled state machine.
    Use update_signal_status() for workflow-driven status changes.

    Args:
        signal_id: The signal ID to update.
        **kwargs: Field=value pairs to set.

    Returns:
        True if updated, False if signal not found or no fields provided.
    """
    if not kwargs:
        return False

    conn = get_connection()
    _ensure_trade_signals_table(conn)

    cur = conn.execute(
        "SELECT id FROM trade_signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if cur is None:
        conn.close()
        return False

    set_parts = []
    params = []
    for key, value in kwargs.items():
        set_parts.append(f"{key} = ?")
        params.append(value)

    params.append(signal_id)
    sql = f"UPDATE trade_signals SET {', '.join(set_parts)} WHERE id = ?"
    conn.execute(sql, params)
    conn.commit()
    conn.close()
    return True


def delete_signal(signal_id: int) -> bool:
    """Delete a trade signal by ID.

    Args:
        signal_id: The signal ID to delete.

    Returns:
        True if deleted, False if not found.
    """
    conn = get_connection()
    _ensure_trade_signals_table(conn)

    cur = conn.execute(
        "DELETE FROM trade_signals WHERE id = ?", (signal_id,)
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()

    return deleted


# ============================================================
#  Portfolio Snapshots table — target holdings after rebalance
# ============================================================


def _ensure_portfolio_snapshots_table(conn: sqlite3.Connection):
    """Ensure portfolio_snapshots table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rebalance_date  TEXT NOT NULL,
            ts_code         TEXT NOT NULL,
            target_weight   REAL,
            target_shares   INTEGER,
            score           REAL,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()


def save_portfolio_snapshot(df: pd.DataFrame) -> int:
    """Save a portfolio snapshot after rebalance.

    Args:
        df: DataFrame with columns [rebalance_date, ts_code, target_weight,
            target_shares, score].

    Returns:
        Number of rows saved.
    """
    if df.empty:
        return 0

    conn = get_connection()
    _ensure_portfolio_snapshots_table(conn)

    cols = ["rebalance_date", "ts_code", "target_weight", "target_shares", "score"]
    available = [c for c in cols if c in df.columns]
    placeholders = ", ".join(["?"] * len(available))
    col_names = ", ".join(available)

    sql = f"INSERT INTO portfolio_snapshots ({col_names}) VALUES ({placeholders})"

    rows = df[available].values.tolist()
    conn.executemany(sql, rows)
    conn.commit()
    conn.close()

    return len(rows)


def load_latest_snapshot() -> pd.DataFrame:
    """Load the most recent portfolio snapshot.

    Returns:
        DataFrame with the latest rebalance_date's holdings, or empty if none.
    """
    conn = get_connection()
    _ensure_portfolio_snapshots_table(conn)

    try:
        latest_date = conn.execute(
            "SELECT MAX(rebalance_date) FROM portfolio_snapshots"
        ).fetchone()[0]

        if latest_date is None:
            conn.close()
            return pd.DataFrame()

        df = pd.read_sql(
            "SELECT * FROM portfolio_snapshots WHERE rebalance_date = ?",
            conn,
            params=(latest_date,),
        )
    except Exception:
        conn.close()
        return pd.DataFrame()

    conn.close()
    return df


def export_csv(
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    filename: str = "export.csv",
) -> str:
    """Export data to CSV file in the data directory.

    Args:
        ts_codes: Stock codes to export, or None for all.
        start_date: Start date filter.
        end_date: End date filter.
        filename: Output filename.

    Returns:
        Path to the exported CSV file.
    """
    df = load_daily_price(ts_codes, start_date, end_date)

    if df.empty:
        return ""

    filepath = DATA_DIR / filename
    df.to_csv(filepath, index=False)
    return str(filepath)


# ============================================================
# Grid state table — persists grid trading live state
# ============================================================

def _ensure_grid_state_table(conn: sqlite3.Connection):
    """Ensure grid_state table exists for live grid trading state."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS grid_state (
            ts_code TEXT NOT NULL,
            grid_level INTEGER NOT NULL,
            grid_price REAL NOT NULL,
            direction TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle',
            signal_id INTEGER,
            filled_shares INTEGER DEFAULT 0,
            filled_price REAL,
            last_trigger_price REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(ts_code, grid_level, direction)
        )
    """)
    conn.commit()


def save_grid_state(rows: list[dict]) -> int:
    """Save or update grid state rows.

    Args:
        rows: List of dicts with keys [ts_code, grid_level, grid_price,
              direction, status, signal_id, filled_shares, filled_price,
              last_trigger_price].

    Returns:
        Number of rows saved.
    """
    if not rows:
        return 0
    conn = get_connection()
    _ensure_grid_state_table(conn)
    cols = ["ts_code", "grid_level", "grid_price", "direction", "status",
            "signal_id", "filled_shares", "filled_price", "last_trigger_price"]
    sql = f"""INSERT OR REPLACE INTO grid_state
        ({', '.join(cols)})
        VALUES ({', '.join(['?'] * len(cols))})"""
    conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


def load_grid_state(ts_code: str | None = None) -> pd.DataFrame:
    """Load grid state from database.

    Args:
        ts_code: Optional filter by stock code.

    Returns:
        DataFrame with grid state rows.
    """
    conn = get_connection()
    _ensure_grid_state_table(conn)
    try:
        if ts_code:
            df = pd.read_sql(
                "SELECT * FROM grid_state WHERE ts_code = ? ORDER BY ts_code, grid_level, direction",
                conn, params=(ts_code,),
            )
        else:
            df = pd.read_sql(
                "SELECT * FROM grid_state ORDER BY ts_code, grid_level, direction",
                conn,
            )
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        df = pd.DataFrame()
    conn.close()
    return df


def clear_grid_state(ts_code: str | None = None) -> int:
    """Clear grid state rows. If ts_code is None, clears all.

    Returns:
        Number of rows deleted.
    """
    conn = get_connection()
    _ensure_grid_state_table(conn)
    cursor = conn.cursor()
    if ts_code:
        cursor.execute("DELETE FROM grid_state WHERE ts_code = ?", (ts_code,))
    else:
        cursor.execute("DELETE FROM grid_state")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()


# ============================================================
#  Backtest Jobs table — persist backtest results for history
# ============================================================

def _ensure_backtest_jobs_table(conn: sqlite3.Connection):
    """Ensure backtest_jobs table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            scheme_name TEXT,
            config_json TEXT,
            metrics_json TEXT,
            result_dir TEXT
        )
    """)
    conn.commit()


def save_backtest_job(scheme_name: str, config_json: str, metrics_json: str, result_dir: str) -> int:
    """Save a completed backtest job record. Returns the new row id."""
    conn = get_connection()
    _ensure_backtest_jobs_table(conn)
    conn.execute(
        "INSERT INTO backtest_jobs (scheme_name, config_json, metrics_json, result_dir) "
        "VALUES (?, ?, ?, ?)",
        (scheme_name, config_json, metrics_json, result_dir),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


def list_backtest_jobs() -> list[dict]:
    """Return all backtest jobs, newest first."""
    conn = get_connection()
    _ensure_backtest_jobs_table(conn)
    try:
        rows = conn.execute(
            "SELECT id, created_at, scheme_name, metrics_json, result_dir "
            "FROM backtest_jobs ORDER BY id DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "scheme_name": r[2],
                "metrics": r[3],
                "result_dir": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_backtest_job(job_id: int) -> dict | None:
    """Return a single backtest job by id, or None."""
    conn = get_connection()
    _ensure_backtest_jobs_table(conn)
    try:
        row = conn.execute(
            "SELECT id, created_at, scheme_name, config_json, metrics_json, result_dir "
            "FROM backtest_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "scheme_name": row[2],
            "config": row[3],
            "metrics": row[4],
            "result_dir": row[5],
        }
    finally:
        conn.close()


def delete_backtest_job(job_id: int) -> bool:
    """Delete a backtest job record. Returns True if deleted, False if not found."""
    conn = get_connection()
    _ensure_backtest_jobs_table(conn)
    cur = conn.execute("DELETE FROM backtest_jobs WHERE id = ?", (job_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
    return deleted

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

    try:
        df = pd.read_sql("SELECT * FROM daily_price", conn)
    except Exception:
        conn.close()
        return pd.DataFrame()

    conn.close()

    if df.empty:
        return df

    # Apply filters
    if ts_codes:
        df = df[df["ts_code"].isin(ts_codes)]
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    # Sort
    df = df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)

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

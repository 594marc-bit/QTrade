"""Industry classification data fetching and caching."""

import logging

import pandas as pd

from src.config import DB_PATH

logger = logging.getLogger(__name__)


def _ensure_industry_table(conn):
    """Ensure industry_classify table exists in SQLite."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_classify (
            ts_code TEXT NOT NULL,
            industry_name TEXT,
            industry_code TEXT,
            PRIMARY KEY (ts_code)
        )
    """)
    conn.commit()


def load_industry_data() -> dict[str, str]:
    """Load industry classification from SQLite cache.

    Returns:
        Dict mapping ts_code to industry_name. Empty dict if no data.
    """
    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_industry_table(conn)
        df = pd.read_sql("SELECT ts_code, industry_name FROM industry_classify", conn)
        conn.close()
        if df.empty:
            return {}
        return dict(zip(df["ts_code"], df["industry_name"]))
    except Exception:
        conn.close()
        return {}


def fetch_industry_tushare(ts_codes: list[str]) -> dict[str, str]:
    """Fetch industry classification from Tushare.

    Args:
        ts_codes: List of stock codes.

    Returns:
        Dict mapping ts_code to industry_name.
    """
    try:
        import tushare as ts
        from src.config import TUSHARE_TOKEN

        if not TUSHARE_TOKEN:
            logger.warning("Tushare token not configured, cannot fetch industry data.")
            return {}

        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()

        # Fetch index constituent with industry info
        # Use stock_basic which has industry field
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,industry",
        )

        if df is None or df.empty:
            return {}

        result = dict(zip(df["ts_code"], df["industry"]))

        # Filter to only requested codes
        result = {k: v for k, v in result.items() if k in ts_codes}

        return result

    except Exception as e:
        logger.warning("Failed to fetch industry data from Tushare: %s", e)
        return {}


def fetch_industry_akshare(ts_codes: list[str]) -> dict[str, str]:
    """Fetch industry classification from AKShare (East Money).

    Args:
        ts_codes: List of stock codes.

    Returns:
        Dict mapping ts_code to industry_name.
    """
    try:
        import akshare as ak

        df = ak.stock_individual_info_em(symbol="000001")

        # AKShare doesn't have a direct bulk industry API,
        # use stock_board_industry_name_em for industry classification
        industry_df = ak.stock_board_industry_name_em()

        if industry_df is None or industry_df.empty:
            return {}

        # This returns board/industry names but not per-stock mapping.
        # Fallback: use stock_individual_info for each is too slow,
        # so we try the sector membership API.
        result = {}
        for _, row in industry_df.iterrows():
            industry_name = row.get("板块名称", "")
            if not industry_name:
                continue
            try:
                members = ak.stock_board_industry_cons_em(symbol=industry_name)
                if members is not None and not members.empty:
                    code_col = members.columns[1] if len(members.columns) > 1 else members.columns[0]
                    for code in members[code_col]:
                        # Convert AKShare code format (6 digits) to Tushare format
                        ts_code = _convert_code(str(code))
                        if ts_code in ts_codes:
                            result[ts_code] = industry_name
            except Exception:
                continue

        return result

    except Exception as e:
        logger.warning("Failed to fetch industry data from AKShare: %s", e)
        return {}


def _convert_code(code: str) -> str:
    """Convert stock code to Tushare format.

    Examples: 600519 → 600519.SH, 000001 → 000001.SZ
    """
    code = code.zfill(6)
    if code.startswith(("6",)):
        return f"{code}.SH"
    return f"{code}.SZ"


def save_industry_data(industry_map: dict[str, str]) -> int:
    """Save industry classification to SQLite.

    Args:
        industry_map: Dict mapping ts_code to industry_name.

    Returns:
        Number of rows saved.
    """
    if not industry_map:
        return 0

    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    _ensure_industry_table(conn)

    rows = [(k, v, "") for k, v in industry_map.items()]
    conn.executemany(
        "INSERT OR REPLACE INTO industry_classify (ts_code, industry_name, industry_code) "
        "VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def get_industry_map(ts_codes: list[str]) -> dict[str, str]:
    """Get industry classification for stocks, using cache or fetching.

    Tries SQLite cache first, then fetches from configured data source.

    Args:
        ts_codes: List of stock codes.

    Returns:
        Dict mapping ts_code to industry_name.
    """
    # Try cache first
    cached = load_industry_data()
    if cached:
        missing = [c for c in ts_codes if c not in cached]
        if not missing:
            return {k: v for k, v in cached.items() if k in ts_codes}
        logger.info("Industry data cached for %d/%d stocks, fetching %d missing.",
                     len(ts_codes) - len(missing), len(ts_codes), len(missing))

    # Fetch from data source
    from src.config import DATA_SOURCE

    if DATA_SOURCE == "tushare":
        industry_map = fetch_industry_tushare(ts_codes)
    else:
        industry_map = fetch_industry_akshare(ts_codes)

    if industry_map:
        save_industry_data(industry_map)
        # Merge with existing cache
        if cached:
            cached.update(industry_map)
            return {k: v for k, v in cached.items() if k in ts_codes}
        return industry_map

    logger.warning("Could not fetch industry data. Proceeding without industry constraint.")
    return {}

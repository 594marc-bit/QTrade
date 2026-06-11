"""Data fetching module for A-share market data via Tushare Pro API."""

import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
from tqdm import tqdm

from src.config import (
    CACHE_EXPIRE_DAYS,
    DATA_DIR,
    END_DATE,
    INDEX_CODE,
    START_DATE,
    TUSHARE_FETCH_INTERVAL,
    TUSHARE_TOKEN,
)


def _init_pro():
    """Initialize and return Tushare pro_api, raising if token is missing."""
    if not TUSHARE_TOKEN:
        raise ValueError(
            "Tushare token not configured. "
            "Please set [tushare] token in config.ini. "
            "See config.ini.example for details."
        )
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()


# Module-level pro_api (lazy init)
_pro = None


def _get_pro():
    global _pro
    if _pro is None:
        _pro = _init_pro()
    return _pro


def get_index_constituents(index_code: str = INDEX_CODE) -> pd.DataFrame:
    """Get index constituent stock list.

    Uses AKShare for constituent list (Tushare requires higher-tier access),
    then converts codes to Tushare format (600519.SH).

    Args:
        index_code: Index code, e.g. "000300" (HS300).

    Returns:
        DataFrame with columns: ts_code, name (Tushare format: 600519.SH)
    """
    import pickle

    cache_path = DATA_DIR / f"{index_code}_constituents_tushare.pkl"

    # Check cache
    if cache_path.exists():
        cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRE_DAYS):
            with open(cache_path, "rb") as f:
                return pickle.load(f)

    # Use AKShare for constituent list (free, no rate-limit for this)
    from src.data.akshare_fetcher import get_index_constituents as _ak_get

    df = _ak_get(index_code)

    # Save cache
    with open(cache_path, "wb") as f:
        pickle.dump(df, f)

    return df


def get_all_stocks() -> pd.DataFrame:
    """Get all listed A-share stocks via Tushare stock_basic.

    Returns:
        DataFrame with columns: ts_code, name.
    """
    import pickle

    cache_path = DATA_DIR / "all_stocks_tushare.pkl"

    # Check cache
    if cache_path.exists():
        cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRE_DAYS):
            with open(cache_path, "rb") as f:
                return pickle.load(f)

    pro = _get_pro()
    df = pro.stock_basic(
        exchange="", list_status="L",
        fields="ts_code,name",
    )
    if df is None or df.empty:
        raise RuntimeError("stock_basic 返回空，请检查 Tushare 权限")

    df = df[["ts_code", "name"]].copy()

    # Save cache
    with open(cache_path, "wb") as f:
        pickle.dump(df, f)

    return df


def get_stock_daily(
    ts_code: str,
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Fetch daily OHLCV data for a single stock via Tushare.

    Gets raw daily data + adj_factor, then calculates forward-adjusted prices.

    Args:
        ts_code: Stock code in format "600519.SH" or "000858.SZ".
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.
        adjust: "qfq" (forward adjust) or "" (raw).

    Returns:
        DataFrame with columns: trade_date, ts_code, open, high, low, close, vol, amount.
    """
    pro = _get_pro()

    retries = 3
    for attempt in range(retries):
        try:
            # Get daily data
            df = pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="trade_date,open,high,low,close,vol,amount",
            )
            break
        except Exception as e:
            if attempt == retries - 1:
                return pd.DataFrame()
            backoff = TUSHARE_FETCH_INTERVAL * (3 ** attempt)
            time.sleep(backoff)

    if df is None or df.empty:
        return pd.DataFrame()

    # Sort by date ascending
    df = df.sort_values("trade_date").reset_index(drop=True)

    # Apply forward adjustment if requested
    if adjust == "qfq":
        try:
            adj = pro.adj_factor(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="trade_date,adj_factor",
            )
            if adj is not None and not adj.empty:
                adj = adj.sort_values("trade_date").reset_index(drop=True)
                df = df.merge(adj, on="trade_date", how="left")
                # Forward fill any missing adj_factor
                df["adj_factor"] = df["adj_factor"].ffill().bfill()
                # Calculate qfq price = raw_price * adj_factor / latest_adj_factor
                latest_adj = df["adj_factor"].iloc[-1]
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col] * df["adj_factor"] / latest_adj
                df = df.drop(columns=["adj_factor"])
        except Exception:
            pass  # Return raw data if adj_factor fails

    df["ts_code"] = ts_code

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Select standard columns
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
    available_cols = [c for c in cols if c in df.columns]
    df = df[available_cols]

    return df


def get_missing_trade_dates(
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> list[str]:
    """Determine which trading dates are missing from the database.

    Compares the Tushare trading calendar against existing dates in daily_price.

    Args:
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.

    Returns:
        Sorted list of missing trade date strings.
    """
    from src.data.storage import get_connection

    pro = _get_pro()

    # Get trading calendar from Tushare
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="cal_date,is_open",
        )
        if cal is None or cal.empty:
            return []
        trade_dates = sorted(
            cal.loc[cal["is_open"] == 1, "cal_date"].tolist()
        )
    except Exception:
        return []

    if not trade_dates:
        return []

    # Get existing dates from DB
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price"
        ).fetchall()
    except sqlite3.OperationalError:
        existing = []
    finally:
        conn.close()

    existing_set = {r[0] for r in existing}

    missing = [d for d in trade_dates if d not in existing_set]
    return missing


def fetch_all_stocks_for_date(trade_date: str) -> pd.DataFrame:
    """Fetch daily OHLCV data for all stocks on a single trade date.

    Args:
        trade_date: Trade date YYYYMMDD.

    Returns:
        DataFrame with columns [trade_date, ts_code, open, high, low, close, vol, amount].
    """
    pro = _get_pro()

    for attempt in range(3):
        try:
            df = pro.daily(
                trade_date=trade_date,
                fields="trade_date,ts_code,open,high,low,close,vol,amount",
            )
            break
        except Exception:
            if attempt == 2:
                return pd.DataFrame()
            time.sleep(TUSHARE_FETCH_INTERVAL * (3 ** attempt))

    if df is None or df.empty:
        return pd.DataFrame()

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Standard column order
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
    return df[[c for c in cols if c in df.columns]]


def sync_adj_factor_for_stocks(
    ts_codes: list[str],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> int:
    """Ensure adj_factor data is complete for given stocks up to end_date.

    Only fetches adj_factor for stocks whose latest stored adj_factor is
    older than end_date. Downloads incrementally from the last stored date.

    Args:
        ts_codes: List of stock codes to check.
        start_date: Earliest date for full fetch (when no data exists).
        end_date: Target end date.

    Returns:
        Number of adj_factor rows saved.
    """
    from src.data.storage import get_latest_adj_factor_dates, save_adj_factor

    if not ts_codes:
        return 0

    pro = _get_pro()
    latest_adj = get_latest_adj_factor_dates(ts_codes)
    total_saved = 0
    failed = []

    for ts_code in tqdm(ts_codes, desc="Syncing adj_factor"):
        stored_latest = latest_adj.get(ts_code)

        if stored_latest and stored_latest >= end_date:
            # Already up to date
            continue

        # Determine fetch range
        if stored_latest:
            fetch_start = (pd.to_datetime(stored_latest) + timedelta(days=1)).strftime("%Y%m%d")
        else:
            fetch_start = start_date

        try:
            adj = pro.adj_factor(
                ts_code=ts_code,
                start_date=fetch_start,
                end_date=end_date,
                fields="trade_date,adj_factor",
            )
            if adj is not None and not adj.empty:
                adj["ts_code"] = ts_code
                adj["adj_factor"] = pd.to_numeric(adj["adj_factor"], errors="coerce")
                saved = save_adj_factor(adj[["ts_code", "trade_date", "adj_factor"]])
                total_saved += saved
            time.sleep(TUSHARE_FETCH_INTERVAL)
        except Exception:
            failed.append(ts_code)

    if failed:
        print(f"  adj_factor failed: {len(failed)} stocks: {failed[:10]}...")

    return total_saved


def _apply_qfq(
    price_df: pd.DataFrame,
    adj_df: pd.DataFrame,
) -> pd.DataFrame:
    """Apply forward adjusted (qfq) prices to a price DataFrame.

    qfq_price = raw_price * adj_factor_on_date / latest_adj_factor

    Args:
        price_df: Raw price data with [trade_date, ts_code, open, high, low, close].
        adj_df: Adj_factor data with [ts_code, trade_date, adj_factor].

    Returns:
        DataFrame with qfq-adjusted open/high/low/close.
    """
    if adj_df.empty:
        return price_df

    # Compute latest adj_factor per stock
    latest_adj = adj_df.groupby("ts_code")["adj_factor"].last().reset_index()
    latest_adj.columns = ["ts_code", "latest_adj"]

    # Merge adj_factor for each date
    merged = price_df.merge(adj_df, on=["ts_code", "trade_date"], how="left")
    # Fill missing adj_factor by forward-filling within each stock
    merged = merged.sort_values(["ts_code", "trade_date"])
    merged["adj_factor"] = merged.groupby("ts_code")["adj_factor"].ffill()

    # Merge latest adj_factor
    merged = merged.merge(latest_adj, on="ts_code", how="left")

    # Apply qfq: only if both adj_factor and latest_adj are available
    mask = merged["adj_factor"].notna() & merged["latest_adj"].notna() & (merged["latest_adj"] > 0)
    for col in ["open", "high", "low", "close"]:
        if col in merged.columns:
            merged.loc[mask, col] = (
                merged.loc[mask, col].astype(float)
                * merged.loc[mask, "adj_factor"]
                / merged.loc[mask, "latest_adj"]
            )

    # Return with original columns
    out_cols = [c for c in price_df.columns if c in merged.columns]
    return merged[out_cols]


def sync_stocks_data(
    end_date: str = END_DATE,
    start_date: str = START_DATE,
) -> pd.DataFrame:
    """Sync daily price data for ALL listed A-share stocks, date by date.

    Iterates over missing trading dates, fetches all stocks' raw prices per
    date in a single API call, computes qfq-adjusted prices using cached
    adj_factor data, and saves to DB.

    Stock universe (index, custom codes) does NOT affect sync scope.
    Sync always covers all A-shares; filtering happens downstream.

    Args:
        end_date: Target end date YYYYMMDD.
        start_date: Override start date (defaults to config START_DATE).

    Returns:
        DataFrame of newly fetched data (empty if already up to date).
    """
    from src.data.storage import save_daily_price, load_adj_factor

    missing_dates = get_missing_trade_dates(start_date=start_date, end_date=end_date)

    if not missing_dates:
        print("  All trade dates up to date. Skipping price fetch.")
        return pd.DataFrame()

    print(f"  Missing {len(missing_dates)} trade dates: {missing_dates[0]} ~ {missing_dates[-1]}")

    # Step 1: Fetch raw prices by date
    all_dfs = []
    for date in tqdm(missing_dates, desc="Fetching prices by date"):
        df = fetch_all_stocks_for_date(date)
        if df.empty:
            print(f"\n  Warning: no data for {date}, skipping")
            continue
        all_dfs.append(df)
        time.sleep(TUSHARE_FETCH_INTERVAL)

    if not all_dfs:
        return pd.DataFrame()

    raw_prices = pd.concat(all_dfs, ignore_index=True)

    # Step 2: Load adj_factor from DB and apply qfq to raw prices
    # (adj_factor is synced per user's stock universe before sync_stocks_data is called)
    all_codes = sorted(raw_prices["ts_code"].unique().tolist())
    adj_all = load_adj_factor(
        ts_codes=all_codes,
        start_date=start_date,
        end_date=end_date,
    )
    qfq_df = _apply_qfq(raw_prices, adj_all)

    # Step 4: Save qfq-adjusted prices
    saved = save_daily_price(qfq_df)
    print(f"  Saved {saved} qfq-adjusted price rows ({len(all_dfs)} dates)")

    return qfq_df


def fetch_daily_basic(
    ts_codes: list[str],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Fetch daily basic indicators (PE_TTM, PB, PS_TTM) via Tushare daily_basic.

    Fetches by trade_date (all stocks in one call per date).
    CSI 300 stocks fit within the 6000-row limit.

    Args:
        ts_codes: List of stock codes to filter.
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.

    Returns:
        DataFrame with columns: trade_date, ts_code, pe_ttm, pb, ps_ttm.
    """
    from src.data.storage import get_latest_date

    pro = _get_pro()
    code_set = set(ts_codes)

    # Get trading calendar
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="cal_date",
        )
        if cal is None or cal.empty:
            return pd.DataFrame()
        trade_dates = sorted(cal["cal_date"].tolist())
    except Exception:
        return pd.DataFrame()

    all_dfs = []
    for date in trade_dates:
        try:
            df = pro.daily_basic(
                trade_date=date,
                fields="ts_code,trade_date,pe_ttm,pb,ps_ttm",
            )
            if df is not None and not df.empty:
                df = df[df["ts_code"].isin(code_set)]
                all_dfs.append(df)
        except Exception:
            continue
        time.sleep(TUSHARE_FETCH_INTERVAL)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    for col in ["pe_ttm", "pb", "ps_ttm"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    return result


def fetch_fina_indicator(
    ts_codes: list[str],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Fetch fina_indicator (ROE, ROE YoY) from Tushare for given stocks.

    Args:
        ts_codes: List of stock codes.
        start_date: Start date YYYYMMDD (end_date filter on reports).
        end_date: End date YYYYMMDD (end_date filter on reports).

    Returns:
        DataFrame with columns [ts_code, end_date, roe, roe_yoy].
    """
    pro = _get_pro()
    all_dfs = []
    fields = "ts_code,end_date,roe,roe_yoy"

    for i, code in enumerate(ts_codes):
        try:
            df = pro.fina_indicator(
                ts_code=code,
                start_date=start_date,
                end_date=end_date,
                fields=fields,
            )
            if df is not None and not df.empty:
                all_dfs.append(df)
        except Exception:
            pass

        if (i + 1) % 50 == 0 and i < len(ts_codes) - 1:
            time.sleep(TUSHARE_FETCH_INTERVAL)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    for col in ["roe", "roe_yoy"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    return result


def get_index_daily(
    ts_code: str = "000300.SH",
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Fetch index daily data for benchmark comparison.

    Args:
        ts_code: Index code (default 000300.SH for HS300).
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.

    Returns:
        DataFrame with columns [trade_date, close].
    """
    pro = _get_pro()
    try:
        df = pro.index_daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="trade_date,close",
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()

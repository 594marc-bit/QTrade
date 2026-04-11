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


def get_stocks_batch(
    ts_codes: list[str],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    adjust: str = "qfq",
    save_every: int = 50,
) -> pd.DataFrame:
    """Fetch daily data for multiple stocks with rate limiting and progress bar.

    Saves progress to database every `save_every` stocks.
    Stops after 10 consecutive failures (likely IP banned).

    Args:
        ts_codes: List of stock codes.
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.
        adjust: "qfq" (forward adjust) or "hfq" (backward adjust) or "" (raw).
        save_every: Save to DB every N stocks.

    Returns:
        Combined DataFrame for all stocks.
    """
    from src.data.storage import save_daily_price

    all_dfs = []
    failed = []
    consecutive_failures = 0

    for i, ts_code in enumerate(tqdm(ts_codes, desc="Fetching stock data (Tushare)")):
        df = get_stock_daily(ts_code, start_date, end_date, adjust)
        if not df.empty:
            all_dfs.append(df)
            consecutive_failures = 0
        else:
            failed.append(ts_code)
            consecutive_failures += 1

            if consecutive_failures >= 10:
                print(f"\n  ⚠️ 10 consecutive failures, likely rate-limited. Stopping early.")
                break

        time.sleep(TUSHARE_FETCH_INTERVAL)

        # Periodic save
        if (i + 1) % save_every == 0 and all_dfs:
            batch_df = pd.concat(all_dfs, ignore_index=True)
            save_daily_price(batch_df)
            print(f"\n  [Checkpoint] Saved {len(batch_df)} rows ({i+1}/{len(ts_codes)} stocks)")

    if failed:
        print(f"Failed to fetch {len(failed)} stocks: {failed[:10]}...")

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


def sync_stocks_data(
    ts_codes: list[str],
    end_date: str = END_DATE,
    adjust: str = "qfq",
    save_every: int = 50,
) -> pd.DataFrame:
    """Smart incremental sync: only fetch missing data per stock.

    Checks DB for each stock's latest date and decides:
    - No data in DB -> full fetch (START_DATE ~ end_date)
    - Data exists but not latest -> incremental fetch (last_date+1 ~ end_date)
    - Already up to date -> skip

    Saves incrementally to DB with periodic checkpoints.

    Args:
        ts_codes: List of stock codes.
        end_date: End date YYYYMMDD.
        adjust: "qfq" or "".
        save_every: Save checkpoint every N stocks.

    Returns:
        Combined DataFrame for all fetched data.
    """
    from src.data.storage import get_latest_date_per_stock, save_daily_price

    latest_dates = get_latest_date_per_stock(ts_codes)

    full_count = sum(1 for code in ts_codes if code not in latest_dates)
    incremental_count = sum(1 for code in latest_dates if latest_dates[code] < end_date)
    skip_count = len(ts_codes) - full_count - incremental_count

    if skip_count == len(ts_codes):
        print(f"  All {len(ts_codes)} stocks up to date. Skipping fetch.")
        return pd.DataFrame()

    print(f"  Sync plan: {full_count} full / {incremental_count} incremental / {skip_count} skip")

    all_dfs = []
    failed = []
    consecutive_failures = 0

    for i, ts_code in enumerate(tqdm(ts_codes, desc="Syncing stock data")):
        is_full_fetch = ts_code not in latest_dates

        if is_full_fetch:
            # No data in DB -> full fetch
            df = get_stock_daily(ts_code, start_date=START_DATE, end_date=end_date, adjust=adjust)
        elif latest_dates[ts_code] >= end_date:
            # Already up to date -> skip
            continue
        else:
            # Incremental fetch
            last_date = latest_dates[ts_code]
            next_day = (pd.to_datetime(last_date) + timedelta(days=1)).strftime("%Y%m%d")
            df = get_stock_daily(ts_code, start_date=next_day, end_date=end_date, adjust=adjust)

        if not df.empty:
            all_dfs.append(df)
            consecutive_failures = 0
        elif is_full_fetch:
            # Full fetch returning empty = real failure
            failed.append(ts_code)
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print(f"\n  Warning: 10 consecutive failures, stopping early.")
                break
        # else: incremental fetch empty = no new data, not a failure

        time.sleep(TUSHARE_FETCH_INTERVAL)

        # Periodic checkpoint save
        if (i + 1) % save_every == 0 and all_dfs:
            batch_df = pd.concat(all_dfs, ignore_index=True)
            saved = save_daily_price(batch_df)
            print(f"\n  [Checkpoint] Saved {saved} rows ({i+1}/{len(ts_codes)} stocks)")

    if failed:
        print(f"  Failed: {len(failed)} stocks: {failed[:10]}...")

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


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

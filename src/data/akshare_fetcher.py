"""Data fetching module for A-share market data via AKShare."""

import os
import time
import pickle
from datetime import datetime, timedelta

# Disable proxy to avoid connection issues with system-level proxy
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(key, None)

import akshare as ak
import pandas as pd
from tqdm import tqdm

from src.config import (
    CACHE_EXPIRE_DAYS,
    DATA_DIR,
    END_DATE,
    FETCH_INTERVAL,
    FETCH_RETRIES,
    INDEX_CODE,
    START_DATE,
)


def get_index_constituents(index_code: str = INDEX_CODE) -> pd.DataFrame:
    """Get HS300 constituent stock list with 7-day cache.

    Args:
        index_code: Index code, default "000300" (HS300).

    Returns:
        DataFrame with columns: ts_code, name
    """
    cache_path = DATA_DIR / "hs300_constituents.pkl"

    # Check cache
    if cache_path.exists():
        cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRE_DAYS):
            with open(cache_path, "rb") as f:
                return pickle.load(f)

    # Fetch from AKShare
    df = ak.index_stock_cons_csindex(symbol=index_code)
    df = df.rename(columns={
        "成分券代码": "ts_code",
        "成分券名称": "name",
    })
    df = df[["ts_code", "name"]]

    # Convert code format: 600519 -> 600519.SH / 000858 -> 000858.SZ
    def _format_code(code):
        code = str(code).zfill(6)
        if code.startswith(("6",)):
            return f"{code}.SH"
        else:
            return f"{code}.SZ"

    df["ts_code"] = df["ts_code"].apply(_format_code)

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
    """Fetch daily OHLCV data for a single stock (forward-adjusted).

    Args:
        ts_code: Stock code in format 600519.SH or 000858.SZ.
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.
        adjust: "qfq" (forward adjust) or "hfq" (backward adjust) or "" (raw).

    Returns:
        DataFrame with columns: trade_date, ts_code, open, high, low, close, vol, amount.
    """
    # Convert ts_code to AKShare format: 600519.SH -> 600519
    symbol = ts_code.split(".")[0]

    adj_map = {"qfq": "", "hfq": "", "": ""}

    for attempt in range(FETCH_RETRIES):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            break
        except Exception as e:
            if attempt == FETCH_RETRIES - 1:
                return pd.DataFrame()
            # Exponential backoff: 5s, 15s, 45s...
            backoff = FETCH_INTERVAL * (3 ** attempt)
            time.sleep(backoff)

    if df.empty:
        return df

    # Rename columns to standard format
    df = df.rename(columns={
        "日期": "trade_date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "vol",
        "成交额": "amount",
        "涨跌幅": "pct_change",
    })

    # Convert date format: 2023-01-03 -> 20230103
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")

    df["ts_code"] = ts_code

    # Select standard columns
    cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
    available_cols = [c for c in cols if c in df.columns]
    df = df[available_cols]

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def get_stocks_batch(
    ts_codes: list[str],
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    adjust: str = "qfq",
    save_every: int = 50,
) -> pd.DataFrame:
    """Fetch daily data for multiple stocks with rate limiting and progress bar.

    Saves progress to database every `save_every` stocks to avoid data loss.
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

    for i, ts_code in enumerate(tqdm(ts_codes, desc="Fetching stock data")):
        df = get_stock_daily(ts_code, start_date, end_date, adjust)
        if not df.empty:
            all_dfs.append(df)
            consecutive_failures = 0
        else:
            failed.append(ts_code)
            consecutive_failures += 1

            # If 10 consecutive failures, likely IP banned — stop and save what we have
            if consecutive_failures >= 10:
                print(f"\n  ⚠️ 10 consecutive failures, likely rate-limited. Stopping early.")
                break

        time.sleep(FETCH_INTERVAL)

        # Periodic save
        if (i + 1) % save_every == 0 and all_dfs:
            batch_df = pd.concat(all_dfs, ignore_index=True)
            saved = save_daily_price(batch_df)
            print(f"\n  [Checkpoint] Saved {len(batch_df)} rows ({i+1}/{len(ts_codes)} stocks)")

    if failed:
        print(f"Failed to fetch {len(failed)} stocks: {failed[:10]}...")

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


def get_stock_daily_incremental(
    ts_code: str,
    existing_df: pd.DataFrame | None = None,
    end_date: str = END_DATE,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Incrementally fetch new data since the last available date.

    Args:
        ts_code: Stock code.
        existing_df: Existing DataFrame with trade_date column, or None.
        end_date: End date YYYYMMDD.
        adjust: Adjustment type.

    Returns:
        Updated DataFrame with new data appended.
    """
    if existing_df is not None and not existing_df.empty:
        last_date = existing_df["trade_date"].max()
        # Start from the day after the last available date
        next_day = (
            pd.to_datetime(last_date) + timedelta(days=1)
        ).strftime("%Y%m%d")

        if next_day > end_date:
            return existing_df  # Already up to date

        new_data = get_stock_daily(ts_code, start_date=next_day, end_date=end_date, adjust=adjust)

        if new_data.empty:
            return existing_df

        return pd.concat([existing_df, new_data], ignore_index=True)
    else:
        return get_stock_daily(ts_code, start_date=START_DATE, end_date=end_date, adjust=adjust)

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
            df = get_stock_daily(ts_code, start_date=START_DATE, end_date=end_date, adjust=adjust)
        elif latest_dates[ts_code] >= end_date:
            continue
        else:
            last_date = latest_dates[ts_code]
            next_day = (pd.to_datetime(last_date) + timedelta(days=1)).strftime("%Y%m%d")
            df = get_stock_daily(ts_code, start_date=next_day, end_date=end_date, adjust=adjust)

        if not df.empty:
            all_dfs.append(df)
            consecutive_failures = 0
        elif is_full_fetch:
            failed.append(ts_code)
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print(f"\n  Warning: 10 consecutive failures, stopping early.")
                break
        # else: incremental fetch empty = no new data, not a failure

        time.sleep(FETCH_INTERVAL)

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
    """Fetch daily basic indicators (PE_TTM, PB, PS_TTM) via AKShare stock_a_indicator_lg.

    Fetches per stock (full historical series), then filters by date range.

    Args:
        ts_codes: List of stock codes (e.g. "600519.SH").
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.

    Returns:
        DataFrame with columns: trade_date, ts_code, pe_ttm, pb, ps_ttm.
    """
    all_dfs = []
    failed = []
    consecutive_failures = 0

    for i, ts_code in enumerate(tqdm(ts_codes, desc="Fetching fundamental data")):
        symbol = ts_code.split(".")[0]

        for attempt in range(FETCH_RETRIES):
            try:
                df = ak.stock_a_indicator_lg(symbol=symbol)
                break
            except Exception:
                if attempt == FETCH_RETRIES - 1:
                    df = pd.DataFrame()
                time.sleep(FETCH_INTERVAL * (3 ** attempt))

        if df is None or df.empty:
            failed.append(ts_code)
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print(f"\n  Warning: 10 consecutive failures, stopping early.")
                break
            time.sleep(FETCH_INTERVAL)
            continue

        consecutive_failures = 0

        # Rename columns to standard format
        col_map = {}
        if "pe_ttm" in df.columns:
            col_map["pe_ttm"] = "pe_ttm"
        elif "pe" in df.columns:
            col_map["pe"] = "pe_ttm"
        if "pb" in df.columns:
            col_map["pb"] = "pb"
        if "ps_ttm" in df.columns:
            col_map["ps_ttm"] = "ps_ttm"
        elif "ps" in df.columns:
            col_map["ps"] = "ps_ttm"

        df = df.rename(columns=col_map)

        # Convert date format
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
        else:
            failed.append(ts_code)
            time.sleep(FETCH_INTERVAL)
            continue

        df["ts_code"] = ts_code

        # Filter by date range
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]

        value_cols = [c for c in ["pe_ttm", "pb", "ps_ttm"] if c in df.columns]
        if value_cols:
            subset = df[["trade_date", "ts_code"] + value_cols].copy()
            for col in value_cols:
                subset[col] = pd.to_numeric(subset[col], errors="coerce")
            all_dfs.append(subset)

        time.sleep(FETCH_INTERVAL)

    if failed:
        print(f"  Failed fundamental data: {len(failed)} stocks: {failed[:10]}...")

    if not all_dfs:
        return pd.DataFrame()

    return pd.concat(all_dfs, ignore_index=True)


def get_index_daily(
    symbol: str = "000300",
    start_date: str = START_DATE,
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Fetch index daily data for benchmark comparison via AKShare.

    Args:
        symbol: Index code (default 000300 for HS300).
        start_date: Start date YYYYMMDD.
        end_date: End date YYYYMMDD.

    Returns:
        DataFrame with columns [trade_date, close].
    """
    try:
        df = ak.stock_zh_index_daily_em(symbol=symbol, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        result = df[["date", "close"]].copy()
        result = result.rename(columns={"date": "trade_date"})
        result = result.sort_values("trade_date").reset_index(drop=True)
        result["close"] = pd.to_numeric(result["close"], errors="coerce")
        return result
    except Exception:
        return pd.DataFrame()

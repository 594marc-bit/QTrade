"""Data cleaning module for A-share daily price data.

All operations are fully vectorized (no per-stock loops) to avoid
O(N_stocks × N_rows) performance degradation on large universes.
"""

import pandas as pd
import numpy as np

from src.config import (
    MAX_CONSECUTIVE_MISSING, PRICE_CHANGE_LIMIT, STOCK_MIN_TRADING_DAYS,
    BSE_PREFIXES, ST_PATTERN, DELIST_PATTERN, IPO_MIN_MONTHS,
    EXCLUDE_ST, EXCLUDE_DELIST, EXCLUDE_NEW_IPO,
)


def filter_low_quality_stocks(
    df: pd.DataFrame,
    min_trading_days: int = STOCK_MIN_TRADING_DAYS,
    exclude_bse: bool = True,
    exclude_st: bool = EXCLUDE_ST,
    exclude_delist: bool = EXCLUDE_DELIST,
    exclude_new_ipo: bool = EXCLUDE_NEW_IPO,
    ipo_min_months: int = IPO_MIN_MONTHS,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Filter out low-quality stocks from the universe.

    Removes:
    1. 北交所 (BSE) stocks — prefix 920/830/870 (if exclude_bse=True)
    2. ST stocks — name contains "ST" (if exclude_st=True)
    3. Delisting stocks — name contains "退" (if exclude_delist=True)
    4. New IPO stocks — listed < ipo_min_months ago (if exclude_new_ipo=True)
    5. Stocks with fewer than min_trading_days of data

    Args:
        df: DataFrame with columns [trade_date, ts_code, ...].
        min_trading_days: Minimum number of trading days required.
        exclude_bse: Whether to exclude BSE stocks.
        exclude_st: Whether to exclude ST stocks.
        exclude_delist: Whether to exclude delisting stocks.
        exclude_new_ipo: Whether to exclude newly listed stocks.
        ipo_min_months: Minimum months since listing.
        end_date: Reference date for IPO cutoff (YYYYMMDD). Uses max(trade_date) if None.

    Returns:
        Tuple of (filtered DataFrame, report dict).
    """
    original_stocks = df["ts_code"].nunique()
    original_rows = len(df)
    removed = {}

    # 1. Remove BSE stocks (北交所)
    if exclude_bse:
        bse_mask = df["ts_code"].str.extract(r"^(\d+)")[0].str.startswith(BSE_PREFIXES)
        bse_codes = set(df.loc[bse_mask, "ts_code"])
        if bse_codes:
            df = df[~bse_mask].copy()
            removed["bse_stocks"] = len(bse_codes)
            removed["bse_codes"] = sorted(bse_codes)[:20]  # Show first 20

    # 2. Remove ST stocks
    if exclude_st and "name" in df.columns:
        st_mask = df["name"].str.contains(ST_PATTERN, na=False)
        st_codes = set(df.loc[st_mask, "ts_code"])
        if st_codes:
            df = df[~st_mask].copy()
            removed["st_stocks"] = len(st_codes)
    elif exclude_st and "name" not in df.columns:
        # Name column not available — try to load from stock_basic cache
        st_codes = _filter_codes_by_name_from_cache(df, ST_PATTERN)
        if st_codes:
            df = df[~df["ts_code"].isin(st_codes)].copy()
            removed["st_stocks"] = len(st_codes)

    # 3. Remove delisting stocks (名称含"退")
    if exclude_delist:
        delist_codes = set()
        if "name" in df.columns:
            delist_mask = df["name"].str.contains(DELIST_PATTERN, na=False)
            delist_codes = set(df.loc[delist_mask, "ts_code"])
        else:
            delist_codes = _filter_codes_by_name_from_cache(df, DELIST_PATTERN)
        if delist_codes:
            df = df[~df["ts_code"].isin(delist_codes)].copy()
            removed["delist_stocks"] = len(delist_codes)

    # 4. Remove new IPO stocks (上市不足N个月)
    if exclude_new_ipo:
        ref_date = end_date if end_date else df["trade_date"].max()
        if isinstance(ref_date, str):
            from datetime import datetime, timedelta
            ref_dt = datetime.strptime(ref_date[:8], "%Y%m%d")
        else:
            from datetime import datetime, timedelta
            ref_dt = datetime.strptime(str(ref_date)[:8], "%Y%m%d")
        cutoff = (ref_dt - timedelta(days=ipo_min_months * 30)).strftime("%Y%m%d")

        first_trade = df.groupby("ts_code")["trade_date"].min()
        new_ipo_codes = set(first_trade[first_trade > cutoff].index)
        if new_ipo_codes:
            df = df[~df["ts_code"].isin(new_ipo_codes)].copy()
            removed["new_ipo_stocks"] = len(new_ipo_codes)

    # 5. Remove stocks with insufficient data
    if "is_trading" in df.columns:
        day_count = df[df["is_trading"] == True].groupby("ts_code").size()
    else:
        day_count = df.groupby("ts_code").size()

    low_data_codes = set(day_count[day_count < min_trading_days].index)
    if low_data_codes:
        df = df[~df["ts_code"].isin(low_data_codes)].copy()
        removed["insufficient_data"] = len(low_data_codes)
        removed["insufficient_data_codes"] = sorted(low_data_codes)[:20]

    remaining_stocks = df["ts_code"].nunique()
    removed["total_removed"] = original_stocks - remaining_stocks
    removed["remaining_stocks"] = remaining_stocks

    return df, removed


def _filter_codes_by_name_from_cache(df: pd.DataFrame, pattern: str) -> set[str]:
    """Try to find stock codes matching a name pattern using cached stock_basic data.

    Returns set of ts_codes that match the pattern.
    """
    try:
        import pickle
        from pathlib import Path
        cache_path = Path(__file__).parent.parent.parent / "data" / "all_stocks_tushare.pkl"
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                basic = pickle.load(f)
            if "name" in basic.columns and "ts_code" in basic.columns:
                bad_codes = set(basic.loc[basic["name"].str.contains(pattern, na=False), "ts_code"])
                existing = bad_codes & set(df["ts_code"].unique())
                return existing
    except Exception:
        pass
    return set()


def fill_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """Handle missing values via vectorized groupby ffill.

    Args:
        df: DataFrame with columns [trade_date, ts_code, open, high, low, close, vol, amount].

    Returns:
        Cleaned DataFrame.
    """
    df = df.copy()
    df = df.sort_values(["ts_code", "trade_date"])

    price_cols = ["open", "high", "low", "close", "vol", "amount"]

    # Forward fill gaps within each stock (vectorized)
    for col in price_cols:
        if col in df.columns:
            df[col] = df.groupby("ts_code")[col].ffill()

    # Drop rows with no close price after ffill (e.g. before listing)
    if "close" in df.columns:
        df = df.dropna(subset=["close"])

    return df


def mark_suspended(df: pd.DataFrame) -> pd.DataFrame:
    """Mark suspended (non-trading) days based on volume = 0.

    Args:
        df: DataFrame with vol column.

    Returns:
        DataFrame with added is_trading column.
    """
    df = df.copy()
    df["is_trading"] = df["vol"] > 0
    return df


def filter_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Filter anomalous data points (vectorized).

    Removes rows where:
    - Daily price change exceeds ±PRICE_CHANGE_LIMIT
    - Volume is 0 but price changed (data error)

    Args:
        df: DataFrame with close, vol columns.

    Returns:
        Filtered DataFrame.
    """
    df = df.copy()
    df = df.sort_values(["ts_code", "trade_date"])

    # Vectorized daily returns
    prev = df.groupby("ts_code")["close"].shift(1)
    daily_ret = (df["close"] - prev) / prev

    # Filter masks (purely vectorized)
    ok_return = daily_ret.between(-PRICE_CHANGE_LIMIT, PRICE_CHANGE_LIMIT) | prev.isna()
    ok_vol = ~((df["vol"] == 0) & (df["close"] != prev) & prev.notna())

    df = df[ok_return & ok_vol].copy()
    return df


def align_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Align all stocks to a unified trading calendar (vectorized).

    Uses MultiIndex.from_product to avoid per-stock loops.

    Args:
        df: DataFrame with trade_date, ts_code columns.

    Returns:
        DataFrame with all stocks on the same date index.
    """
    all_dates = sorted(df["trade_date"].unique())
    all_stocks = df["ts_code"].unique()

    full_idx = pd.MultiIndex.from_product(
        [all_dates, all_stocks],
        names=["trade_date", "ts_code"],
    )

    result = (
        df.set_index(["trade_date", "ts_code"])
        .reindex(full_idx)
        .reset_index()
    )
    return result


def validate_data(df: pd.DataFrame) -> dict:
    """Validate cleaned data quality. Returns validation report.

    Checks:
    - All close prices > 0
    - Daily returns within reasonable range
    - Each trading day has >= 250 stocks

    Args:
        df: Cleaned DataFrame.

    Returns:
        Dict with validation results and any issues found.
    """
    issues = []

    # Check prices > 0
    invalid_prices = df[df["close"] <= 0]
    if not invalid_prices.empty:
        issues.append(f"Found {len(invalid_prices)} rows with close <= 0")

    # Check daily returns
    df_sorted = df.sort_values(["ts_code", "trade_date"])
    prev = df_sorted.groupby("ts_code")["close"].shift(1)
    daily_ret = (df_sorted["close"] - prev) / prev
    extreme_returns = df_sorted[daily_ret.abs() > PRICE_CHANGE_LIMIT]
    if not extreme_returns.empty:
        issues.append(
            f"Found {len(extreme_returns)} rows with daily return > ±{PRICE_CHANGE_LIMIT*100:.0f}%"
        )

    # Check stock count per day
    daily_count = df.groupby("trade_date")["ts_code"].nunique()
    low_count_days = daily_count[daily_count < 250]
    if not low_count_days.empty:
        issues.append(
            f"Found {len(low_count_days)} days with < 250 stocks "
            f"(min: {daily_count.min()}, mean: {daily_count.mean():.0f})"
        )

    return {
        "is_valid": len(issues) == 0,
        "total_rows": len(df),
        "total_stocks": df["ts_code"].nunique(),
        "date_range": f"{df['trade_date'].min()} ~ {df['trade_date'].max()}",
        "issues": issues,
    }


def clean_pipeline(
    df: pd.DataFrame,
    filter_stocks: bool = True,
    min_trading_days: int = STOCK_MIN_TRADING_DAYS,
    exclude_st: bool = EXCLUDE_ST,
    exclude_delist: bool = EXCLUDE_DELIST,
    exclude_new_ipo: bool = EXCLUDE_NEW_IPO,
    ipo_min_months: int = IPO_MIN_MONTHS,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Run the full cleaning pipeline.

    Order: filter low-quality stocks -> fill missing -> filter outliers ->
    mark suspended -> align dates -> validate.

    Args:
        df: Raw DataFrame from fetcher.
        filter_stocks: Whether to apply stock quality filter.
        min_trading_days: Minimum trading days for a stock to be included.
        exclude_st: Whether to exclude ST stocks.
        exclude_delist: Whether to exclude delisting stocks.
        exclude_new_ipo: Whether to exclude newly listed stocks.
        ipo_min_months: Minimum months since listing.
        end_date: Reference date for IPO cutoff.

    Returns:
        Tuple of (cleaned DataFrame, validation report dict).
    """
    filter_report = {}
    if filter_stocks:
        df, filter_report = filter_low_quality_stocks(
            df, min_trading_days=min_trading_days,
            exclude_st=exclude_st, exclude_delist=exclude_delist,
            exclude_new_ipo=exclude_new_ipo, ipo_min_months=ipo_min_months,
            end_date=end_date,
        )

    df = fill_missing_values(df)
    df = filter_outliers(df)
    df = mark_suspended(df)
    df = align_dates(df)

    report = validate_data(df)
    report["filter"] = filter_report

    return df, report

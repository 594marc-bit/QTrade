"""Data cleaning module for A-share daily price data."""

import pandas as pd
import numpy as np

from src.config import MAX_CONSECUTIVE_MISSING, PRICE_CHANGE_LIMIT


def fill_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """Handle missing values: ffill short gaps, drop long consecutive gaps.

    Args:
        df: DataFrame with columns [trade_date, ts_code, open, high, low, close, vol, amount].

    Returns:
        Cleaned DataFrame.
    """
    df = df.copy()

    # For each stock, identify consecutive NaN runs
    stocks = df["ts_code"].unique()
    rows_to_drop = []

    for stock in stocks:
        mask = df["ts_code"] == stock
        stock_idx = df.index[mask]

        for col in ["open", "high", "low", "close", "vol", "amount"]:
            series = df.loc[stock_idx, col]
            is_nan = series.isna()

            if not is_nan.any():
                continue

            # Find consecutive NaN groups
            groups = (is_nan != is_nan.shift()).cumsum()
            for group_id in groups[is_nan].unique():
                group_mask = groups == group_id
                group_len = group_mask.sum()

                if group_len > MAX_CONSECUTIVE_MISSING:
                    # Drop rows with long consecutive missing
                    rows_to_drop.extend(series.index[group_mask].tolist())
                # Short gaps will be ffilled below

    # Drop long missing rows
    if rows_to_drop:
        df = df.drop(index=set(rows_to_drop))

    # Forward fill short gaps per stock
    df = df.sort_values(["ts_code", "trade_date"])
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        if col in df.columns:
            df[col] = df.groupby("ts_code")[col].transform(lambda x: x.ffill())

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
    """Filter anomalous data points.

    Removes rows where:
    - Daily price change exceeds ±20%
    - Volume is 0 but price changed (data error)

    Args:
        df: DataFrame with close, vol columns.

    Returns:
        Filtered DataFrame.
    """
    df = df.copy()

    # Calculate daily returns per stock
    df = df.sort_values(["ts_code", "trade_date"])
    df["prev_close"] = df.groupby("ts_code")["close"].shift(1)
    df["daily_return"] = (df["close"] - df["prev_close"]) / df["prev_close"]

    # Filter: daily return within ±20%
    mask_return = df["daily_return"].between(-PRICE_CHANGE_LIMIT, PRICE_CHANGE_LIMIT) | df["prev_close"].isna()

    # Filter: not (volume=0 and price changed)
    mask_vol_price = ~((df["vol"] == 0) & (df["close"] != df["prev_close"]) & df["prev_close"].notna())

    # Apply filters
    df = df[mask_return & mask_vol_price].copy()

    # Drop helper columns
    df = df.drop(columns=["prev_close", "daily_return"])

    return df


def align_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Align all stocks to a unified trading calendar.

    Finds the union of all trading dates and reindexes each stock,
    filling missing dates with NaN.

    Args:
        df: DataFrame with trade_date, ts_code columns.

    Returns:
        DataFrame with all stocks on the same date index.
    """
    # Get the full trading calendar (union of all dates)
    all_dates = sorted(df["trade_date"].unique())

    stocks = df["ts_code"].unique()
    aligned_dfs = []

    for stock in stocks:
        stock_df = df[df["ts_code"] == stock].copy()
        stock_df = stock_df.set_index("trade_date")
        # Reindex to full calendar
        stock_df = stock_df.reindex(all_dates)
        stock_df["ts_code"] = stock
        stock_df.index.name = "trade_date"
        aligned_dfs.append(stock_df.reset_index())

    result = pd.concat(aligned_dfs, ignore_index=True)
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
    df_sorted["prev_close"] = df_sorted.groupby("ts_code")["close"].shift(1)
    df_sorted["daily_return"] = (df_sorted["close"] - df_sorted["prev_close"]) / df_sorted["prev_close"]
    extreme_returns = df_sorted[df_sorted["daily_return"].abs() > PRICE_CHANGE_LIMIT]
    if not extreme_returns.empty:
        issues.append(f"Found {len(extreme_returns)} rows with daily return > ±{PRICE_CHANGE_LIMIT*100:.0f}%")

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


def clean_pipeline(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Run the full cleaning pipeline.

    Order: fill missing -> filter outliers -> mark suspended -> align dates -> validate.

    Args:
        df: Raw DataFrame from fetcher.

    Returns:
        Tuple of (cleaned DataFrame, validation report dict).
    """
    df = fill_missing_values(df)
    df = filter_outliers(df)
    df = mark_suspended(df)
    df = align_dates(df)

    report = validate_data(df)

    return df, report

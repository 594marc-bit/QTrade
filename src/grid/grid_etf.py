"""ETF universe filtering for grid trading mode."""

import pandas as pd

# Standard ETF code patterns
ETF_PATTERNS = ("51%SH", "159%SZ")


def is_etf(ts_code: str) -> bool:
    """Check if a stock code is a standard ETF."""
    return ts_code.startswith("51") and ts_code.endswith(".SH") or \
           ts_code.startswith("159") and ts_code.endswith(".SZ")


def filter_etf_universe(df: pd.DataFrame) -> pd.DataFrame:
    """Filter DataFrame to ETF stocks only.

    Args:
        df: DataFrame with 'ts_code' column.

    Returns:
        Filtered DataFrame containing only ETF rows.
    """
    if df.empty or "ts_code" not in df.columns:
        return df
    mask = df["ts_code"].str.startswith("51") & df["ts_code"].str.endswith(".SH") | \
           df["ts_code"].str.startswith("159") & df["ts_code"].str.endswith(".SZ")
    return df[mask]


def get_etf_codes(df: pd.DataFrame) -> list[str]:
    """Return sorted list of unique ETF codes from a DataFrame.

    Args:
        df: DataFrame with 'ts_code' column.

    Returns:
        Sorted list of ETF ts_code strings.
    """
    filtered = filter_etf_universe(df)
    if filtered.empty:
        return []
    return sorted(filtered["ts_code"].unique().tolist())

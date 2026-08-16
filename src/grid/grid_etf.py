"""ETF universe filtering for grid trading mode."""

import pandas as pd

# Standard ETF code patterns
ETF_PATTERNS = ("51%SH", "159%SZ")


def is_etf(ts_code: str) -> bool:
    """Check if a stock code is an ETF or fund (non-individual-stock).

    A-share stock code ranges:
      SH: 600-605 (main board), 688-689 (STAR)
      SZ: 000-004 (main board), 300-301 (ChiNext)
    Everything else is funds, ETFs, LOFs, REITs, bonds, etc.
    """
    code, _, suffix = ts_code.partition(".")
    if suffix == "SH":
        # 5xxxxx = all SH funds/ETFs/REITs
        return code.startswith("5")
    if suffix == "SZ":
        # 159xxx = ETFs, 16xxxx = LOFs, 18xxxx = funds
        return code.startswith("159") or code.startswith("16") or code.startswith("18")
    return False


def filter_etf_universe(df: pd.DataFrame) -> pd.DataFrame:
    """Filter DataFrame to ETF stocks only.

    Args:
        df: DataFrame with 'ts_code' column.

    Returns:
        Filtered DataFrame containing only ETF rows.
    """
    if df.empty or "ts_code" not in df.columns:
        return df
    mask = df["ts_code"].apply(is_etf)
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

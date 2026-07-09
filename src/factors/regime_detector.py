"""Market regime detection: classifies each date as 'trend' or 'mean_revert'.

Regime Rules:
    trend: close > MA60 AND MA60 slope (20d) > 0
    mean_revert: otherwise (range-bound or downtrend)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_regime(
    index_df: pd.DataFrame,
    ma_window: int = 60,
    slope_window: int = 20,
) -> pd.Series:
    """Classify each trading date as 'trend' or 'mean_revert'.

    Args:
        index_df: DataFrame with [trade_date, close] for the benchmark index.
        ma_window: MA window for trend detection.
        slope_window: Window for computing MA slope.

    Returns:
        Series with trade_date as index and 'trend' or 'mean_revert' as values.
    """
    df = index_df.sort_values("trade_date").copy()
    df["ma60"] = df["close"].rolling(window=ma_window, min_periods=ma_window).mean()
    df["ma60_slope"] = df["ma60"].diff(periods=slope_window)

    regime = np.where(
        (df["close"] > df["ma60"]) & (df["ma60_slope"] > 0),
        "trend",
        "mean_revert",
    )
    return pd.Series(regime, index=df["trade_date"], name="regime")


def add_regime_column(
    stock_df: pd.DataFrame,
    index_df: pd.DataFrame,
    ma_window: int = 60,
    slope_window: int = 20,
) -> pd.DataFrame:
    """Add a 'regime' column to stock DataFrame mapped from index regime.

    Args:
        stock_df: Stock data with trade_date column.
        index_df: Index data with trade_date, close columns.
        ma_window: MA window.
        slope_window: MA slope window.

    Returns:
        stock_df with added 'regime' column.
    """
    regime_series = detect_regime(index_df, ma_window, slope_window)
    df = stock_df.copy()
    df["regime"] = df["trade_date"].map(regime_series)
    return df

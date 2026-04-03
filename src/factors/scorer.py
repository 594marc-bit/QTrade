"""Factor scoring: Z-Score standardization, weighted scoring, and stock ranking."""

import re

import pandas as pd
import numpy as np
from scipy import stats

from src.config import DEFAULT_WEIGHTS


def _factor_to_score_col(factor_col: str) -> str:
    """Convert raw factor column name to standardized score column name."""
    name = re.sub(r"_\d+d$", "", factor_col)  # strip _20d, _14d, _10d
    name = name.replace("_ratio", "")
    return name + "_score"


def zscore_cross_section(df: pd.DataFrame, factor_col: str) -> pd.Series:
    """Compute cross-sectional Z-Score for a factor on each trading date.

    Args:
        df: DataFrame with trade_date, ts_code, and factor_col.
        factor_col: Name of the raw factor column.

    Returns:
        Series of Z-Score values, named factor_col + "_score".
    """
    score_col = _factor_to_score_col(factor_col)

    scores = df.groupby("trade_date")[factor_col].transform(
        lambda x: _safe_zscore(x)
    )
    scores.name = score_col
    return scores


def _safe_zscore(series: pd.Series) -> pd.Series:
    """Z-Score with NaN handling. Returns all NaN if std is 0."""
    valid = series.dropna()
    if len(valid) < 2 or valid.std() == 0:
        return pd.Series(np.nan, index=series.index)
    result = stats.zscore(series, nan_policy="omit")
    return pd.Series(result, index=series.index)


def standardize_factors(df: pd.DataFrame, factor_cols: list[str]) -> pd.DataFrame:
    """Apply cross-sectional Z-Score to multiple factor columns.

    Args:
        df: DataFrame with raw factor columns.
        factor_cols: List of raw factor column names.

    Returns:
        DataFrame with added score columns.
    """
    df = df.copy()
    for col in factor_cols:
        if col in df.columns:
            score_col = _factor_to_score_col(col)
            df[score_col] = zscore_cross_section(df, col)
    return df


def compute_total_score(
    df: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compute weighted total score from standardized factor scores.

    Args:
        df: DataFrame with factor score columns.
        weights: Dict mapping score column name to weight. Defaults to DEFAULT_WEIGHTS.

    Returns:
        DataFrame with added total_score column.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    df = df.copy()
    df["total_score"] = 0.0

    for score_col, weight in weights.items():
        if score_col in df.columns:
            df["total_score"] += df[score_col].fillna(0) * weight

    return df


def select_top_n(
    df: pd.DataFrame,
    date: str,
    n: int = 10,
    exclude_suspended: bool = True,
) -> pd.DataFrame:
    """Select top N stocks by total_score on a given date.

    Args:
        df: DataFrame with total_score, is_trading columns.
        date: Trade date string YYYYMMDD.
        n: Number of stocks to select.
        exclude_suspended: Whether to exclude suspended stocks.

    Returns:
        DataFrame of top N stocks with ts_code, total_score.
    """
    day_df = df[df["trade_date"] == date].copy()

    if exclude_suspended and "is_trading" in day_df.columns:
        day_df = day_df[day_df["is_trading"] == True]

    day_df = day_df.dropna(subset=["total_score"])
    day_df = day_df.nlargest(n, "total_score")

    return day_df[["ts_code", "trade_date", "total_score"]].reset_index(drop=True)

"""IC (Information Coefficient) analysis for factor effectiveness evaluation."""

import pandas as pd
import numpy as np
from scipy import stats

from src.config import IC_THRESHOLD, IC_WIN_RATE_THRESHOLD


def compute_future_return(df: pd.DataFrame, n_days: int = 5) -> pd.DataFrame:
    """Compute forward N-day return for each stock.

    Args:
        df: DataFrame with [trade_date, ts_code, close], sorted by ts_code, trade_date.
        n_days: Number of forward days.

    Returns:
        DataFrame with added future_return_{n_days}d column.
    """
    df = df.copy()
    df = df.sort_values(["ts_code", "trade_date"])
    col_name = f"future_return_{n_days}d"
    df[col_name] = df.groupby("ts_code")["close"].pct_change(n_days).shift(-n_days)
    return df


def compute_daily_ic(
    df: pd.DataFrame,
    factor_col: str,
    return_col: str,
    method: str = "spearman",
) -> float:
    """Compute IC for a single day (cross-sectional correlation).

    Args:
        df: DataFrame for a single trading date with factor and return columns.
        factor_col: Factor column name.
        return_col: Future return column name.
        method: "spearman" (rank correlation) or "pearson".

    Returns:
        IC value, or NaN if insufficient data.
    """
    valid = df[[factor_col, return_col]].dropna()

    if len(valid) < 10:
        return np.nan

    if method == "spearman":
        corr, _ = stats.spearmanr(valid[factor_col], valid[return_col])
    else:
        corr, _ = stats.pearsonr(valid[factor_col], valid[return_col])

    return corr


def compute_ic_series(
    df: pd.DataFrame,
    factor_col: str,
    return_col: str,
    method: str = "spearman",
) -> pd.Series:
    """Compute IC for every trading day.

    Args:
        df: Full DataFrame with trade_date, factor, and return columns.
        factor_col: Factor column name.
        return_col: Future return column name.
        method: Correlation method.

    Returns:
        Series indexed by trade_date with IC values.
    """
    ic_series = df.groupby("trade_date").apply(
        lambda x: compute_daily_ic(x, factor_col, return_col, method)
    )
    ic_series.name = "IC"
    return ic_series


def compute_ic_summary(ic_series: pd.Series) -> dict:
    """Compute IC statistics summary.

    Args:
        ic_series: Series of daily IC values.

    Returns:
        Dict with ic_mean, ic_std, icir, win_rate, count.
    """
    valid = ic_series.dropna()

    if len(valid) == 0:
        return {
            "ic_mean": np.nan,
            "ic_std": np.nan,
            "icir": np.nan,
            "win_rate": np.nan,
            "ic_direction": 0,
            "count": 0,
        }

    ic_mean = valid.mean()
    ic_std = valid.std()
    icir = ic_mean / ic_std if ic_std != 0 else np.nan
    ic_direction = int(np.sign(ic_mean))
    win_rate = (np.sign(valid) == ic_direction).sum() / len(valid)

    return {
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "icir": round(icir, 4) if not np.isnan(icir) else np.nan,
        "win_rate": round(win_rate, 4),
        "ic_direction": ic_direction,
        "count": len(valid),
    }


def evaluate_factor(
    df: pd.DataFrame,
    factor_col: str,
    return_col: str,
    method: str = "spearman",
    ic_threshold: float = IC_THRESHOLD,
    win_rate_threshold: float = IC_WIN_RATE_THRESHOLD,
) -> dict:
    """Full factor evaluation: IC series, summary, and effectiveness verdict.

    Args:
        df: DataFrame with all needed columns.
        factor_col: Factor column name.
        return_col: Future return column name.
        method: Correlation method.
        ic_threshold: Minimum mean IC for effectiveness.
        win_rate_threshold: Minimum IC > 0 ratio for effectiveness.

    Returns:
        Dict with ic_series, summary, and is_effective flag.
    """
    ic_series = compute_ic_series(df, factor_col, return_col, method)
    summary = compute_ic_summary(ic_series)

    is_effective = (
        not np.isnan(summary["ic_mean"])
        and abs(summary["ic_mean"]) > ic_threshold
        and summary["win_rate"] > win_rate_threshold
    )

    return {
        "factor": factor_col,
        "ic_series": ic_series,
        "summary": summary,
        "is_effective": is_effective,
        "verdict": "有效" if is_effective else "无效",
    }

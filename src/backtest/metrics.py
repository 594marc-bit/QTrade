"""Backtest performance metrics calculation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(
    nav_series: pd.Series,
    benchmark_nav: pd.Series | None = None,
    risk_free_rate: float = 0.02,
    trading_days: int = 242,
) -> dict:
    """Compute backtest performance metrics from a NAV series.

    Args:
        nav_series: Series of portfolio NAV values indexed by date.
        benchmark_nav: Optional benchmark NAV series (same index).
        risk_free_rate: Annual risk-free rate (default 2%).
        trading_days: Number of trading days per year (default 242 for A-share).

    Returns:
        Dict of performance metrics.
    """
    if len(nav_series) < 2:
        return {}

    daily_returns = nav_series.pct_change().dropna()

    # Total & annualized return
    total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
    n_days = len(nav_series)
    annual_return = (1 + total_return) ** (trading_days / n_days) - 1

    # Sharpe ratio
    daily_rf = risk_free_rate / trading_days
    excess_returns = daily_returns - daily_rf
    sharpe = (
        excess_returns.mean() / excess_returns.std() * np.sqrt(trading_days)
        if excess_returns.std() > 0
        else 0.0
    )

    # Max drawdown
    peak = nav_series.cummax()
    drawdown = (nav_series - peak) / peak
    max_drawdown = drawdown.min()

    # Calmar ratio
    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # Win rate
    win_rate = (daily_returns > 0).mean()

    metrics = {
        "total_return": total_return,
        "annual_return": annual_return,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "trade_count": 0,  # Filled by engine
        "n_days": n_days,
    }

    # Benchmark comparison
    if benchmark_nav is not None and len(benchmark_nav) >= 2:
        bm_daily = benchmark_nav.pct_change().dropna()
        bm_total = benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1
        bm_annual = (1 + bm_total) ** (trading_days / len(benchmark_nav)) - 1

        alpha = annual_return - bm_annual
        active = daily_returns - bm_daily
        tracking_error = active.std() * np.sqrt(trading_days)
        info_ratio = (
            active.mean() * trading_days / tracking_error
            if tracking_error > 0
            else 0.0
        )

        metrics["alpha"] = alpha
        metrics["information_ratio"] = info_ratio
        metrics["tracking_error"] = tracking_error
        metrics["benchmark_return"] = bm_annual

    return metrics


def compute_monthly_returns(nav_series: pd.Series) -> pd.DataFrame:
    """Compute monthly returns from NAV series.

    Args:
        nav_series: NAV series with datetime index.

    Returns:
        DataFrame with columns [year, month, return].
    """
    if len(nav_series) < 2:
        return pd.DataFrame(columns=["year", "month", "return"])

    dates = pd.to_datetime(nav_series.index)
    monthly_nav = nav_series.copy()
    monthly_nav.index = dates

    # Group by year-month, take last NAV of each month
    monthly_nav["ym"] = monthly_nav.index.to_period("M")
    grouped = monthly_nav.groupby("ym")
    month_end = grouped.last()

    # Remove the helper column
    month_end = month_end.drop(columns=["ym"])

    # Calculate returns
    returns = month_end.pct_change().dropna()

    result = pd.DataFrame({
        "year": [p.year for p in returns.index],
        "month": [p.month for p in returns.index],
        "return": returns.values,
    })
    return result


def compute_drawdown_series(nav_series: pd.Series) -> pd.Series:
    """Compute drawdown series from NAV.

    Args:
        nav_series: NAV series.

    Returns:
        Series of drawdown values (negative percentages).
    """
    peak = nav_series.cummax()
    return (nav_series - peak) / peak

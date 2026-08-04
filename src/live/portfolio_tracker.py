"""Portfolio tracker: maintains target holding snapshots for signal diffing."""

import pandas as pd

from src.data.storage import load_latest_snapshot, save_portfolio_snapshot


def get_current_target_portfolio() -> pd.DataFrame:
    """Load the most recent target portfolio snapshot.

    Returns:
        DataFrame with columns [rebalance_date, ts_code, target_weight,
        target_shares, score], or empty if no snapshot exists.
    """
    return load_latest_snapshot()


def save_target_portfolio(
    rebalance_date: str,
    positions: list[dict],
) -> int:
    """Save a new target portfolio snapshot after rebalance.

    Args:
        rebalance_date: Rebalance date in YYYYMMDD format.
        positions: List of dicts with keys: ts_code, target_weight,
                   target_shares, score.

    Returns:
        Number of rows saved.
    """
    if not positions:
        return 0

    df = pd.DataFrame(positions)
    df["rebalance_date"] = rebalance_date
    return save_portfolio_snapshot(df)

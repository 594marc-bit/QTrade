"""Industry neutral constraint for stock selection."""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def apply_industry_constraint(
    top_stocks: pd.DataFrame,
    industry_map: dict[str, str],
    max_pct: float = 0.30,
    n: int | None = None,
) -> pd.DataFrame:
    """Apply industry concentration constraint to top stock selection.

    Iterates through stocks by total_score (descending), skipping any stock
    whose industry would exceed the max_pct threshold.

    Args:
        top_stocks: DataFrame with ts_code and total_score, sorted by score descending.
        industry_map: Dict mapping ts_code to industry_name.
        max_pct: Maximum fraction of portfolio allowed in a single industry (0-1).
        n: Target number of stocks to select. Defaults to len(top_stocks).

    Returns:
        DataFrame of selected stocks satisfying the industry constraint.
    """
    if not industry_map:
        logger.info("No industry data available, skipping industry constraint.")
        return top_stocks

    if n is None:
        n = len(top_stocks)

    max_per_industry = max(1, int(n * max_pct))
    industry_count: dict[str, int] = {}
    selected_rows = []

    for _, row in top_stocks.iterrows():
        code = row["ts_code"]
        industry = industry_map.get(code, "未知")

        if industry_count.get(industry, 0) >= max_per_industry:
            continue

        selected_rows.append(row)
        industry_count[industry] = industry_count.get(industry, 0) + 1

    if not selected_rows:
        return top_stocks.iloc[:0]

    result = pd.DataFrame(selected_rows).reset_index(drop=True)

    if len(result) < len(top_stocks):
        skipped = len(top_stocks) - len(result)
        logger.info(
            "Industry constraint: selected %d/%d stocks (%d skipped for concentration). "
            "Industry counts: %s",
            len(result), len(top_stocks), skipped, industry_count,
        )

    return result

"""Adaptive factor weights based on rolling IC analysis."""

import logging

import pandas as pd

from src.config import DEFAULT_WEIGHTS

logger = logging.getLogger(__name__)


def compute_adaptive_weights(
    ic_df: pd.DataFrame,
    window: int = 60,
    base_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute adaptive factor weights from rolling IC means.

    Uses the mean of daily IC values over the rolling window as the weight
    basis, preserving the sign direction from base_weights.

    Args:
        ic_df: DataFrame with columns [trade_date, <factor_col>, ...] where
               each factor column contains daily IC values.
        window: Number of recent trading days to use for IC mean.
        base_weights: Fallback weights if data is insufficient. Defaults to DEFAULT_WEIGHTS.

    Returns:
        Dict mapping score column names to adaptive weights, normalized so
        absolute values sum to 1 (preserving signs from base_weights).
    """
    if base_weights is None:
        base_weights = DEFAULT_WEIGHTS

    # Take the most recent `window` rows
    if len(ic_df) < window:
        logger.warning(
            "IC data insufficient (%d rows < window %d). Falling back to default weights.",
            len(ic_df), window,
        )
        return dict(base_weights)

    recent_ic = ic_df.tail(window)

    # Compute mean IC per factor column (only columns that exist in ic_df)
    ic_means = {}
    for col in recent_ic.columns:
        if col == "trade_date":
            continue
        mean_val = recent_ic[col].mean()
        if pd.notna(mean_val):
            ic_means[col] = mean_val

    if not ic_means:
        logger.warning("No valid IC means computed. Falling back to default weights.")
        return dict(base_weights)

    # Convert factor column names to score column names
    # IC columns are named like "momentum_20d" → score "momentum_score"
    from src.factors.scorer import _factor_to_score_col

    adaptive = {}
    for factor_col, ic_mean in ic_means.items():
        score_col = _factor_to_score_col(factor_col)
        # Preserve sign direction from base_weights
        base_sign = 1.0
        if score_col in base_weights:
            base_sign = -1.0 if base_weights[score_col] < 0 else 1.0
        adaptive[score_col] = base_sign * abs(ic_mean)

    # Normalize: absolute values sum to 1
    total = sum(abs(v) for v in adaptive.values())
    if total == 0:
        return dict(base_weights)

    adaptive = {k: v / total for k, v in adaptive.items()}

    # Fill missing factors from base_weights with 0 weight
    for score_col in base_weights:
        if score_col not in adaptive:
            adaptive[score_col] = 0.0

    return adaptive

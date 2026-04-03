"""Position sizing: equal_weight, score_weighted, risk_parity."""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_position_weights(
    method: str,
    codes: list[str],
    scores: dict[str, float] | None = None,
    volatilities: dict[str, float] | None = None,
    min_weight: float = 0.05,
    max_weight: float = 0.20,
) -> dict[str, float]:
    """Compute position weights based on the chosen method.

    Args:
        method: One of "equal_weight", "score_weighted", "risk_parity".
        codes: List of stock codes in the portfolio.
        scores: Dict mapping ts_code -> total_score (for score_weighted).
        volatilities: Dict mapping ts_code -> volatility (for risk_parity).
        min_weight: Minimum weight per stock.
        max_weight: Maximum weight per stock.

    Returns:
        Dict mapping ts_code -> weight, summing to 1.0.
    """
    if not codes:
        return {}

    if method == "score_weighted":
        weights = _score_weighted(codes, scores)
    elif method == "risk_parity":
        weights = _risk_parity(codes, volatilities)
    else:
        weights = {code: 1.0 / len(codes) for code in codes}

    # Apply min/max bounds and redistribute
    weights = _clip_and_redistribute(weights, min_weight, max_weight)

    return weights


def _score_weighted(
    codes: list[str],
    scores: dict[str, float] | None,
) -> dict[str, float]:
    """Score-weighted allocation: higher score → larger weight."""
    if not scores:
        logger.warning("No scores provided for score_weighted sizing, falling back to equal weight.")
        return {code: 1.0 / len(codes) for code in codes}

    raw = {}
    for code in codes:
        s = scores.get(code, 0.0)
        # Use absolute score to ensure positive weights
        raw[code] = max(abs(s), 1e-6)

    total = sum(raw.values())
    if total == 0:
        return {code: 1.0 / len(codes) for code in codes}

    return {code: v / total for code, v in raw.items()}


def _risk_parity(
    codes: list[str],
    volatilities: dict[str, float] | None,
) -> dict[str, float]:
    """Risk parity allocation: weight ∝ 1/volatility."""
    if not volatilities:
        logger.warning("No volatility data for risk_parity sizing, falling back to equal weight.")
        return {code: 1.0 / len(codes) for code in codes}

    raw = {}
    for code in codes:
        vol = volatilities.get(code)
        if vol is None or vol <= 0:
            logger.warning("Insufficient volatility data for %s, using equal weight.", code)
            raw[code] = 1.0  # Fallback to equal
        else:
            raw[code] = 1.0 / vol

    total = sum(raw.values())
    if total == 0:
        return {code: 1.0 / len(codes) for code in codes}

    return {code: v / total for code, v in raw.items()}


def _clip_and_redistribute(
    weights: dict[str, float],
    min_weight: float,
    max_weight: float,
) -> dict[str, float]:
    """Clip weights to [min_weight, max_weight] and redistribute excess."""

    def _normalize(w: dict[str, float]) -> dict[str, float]:
        total = sum(w.values())
        if total == 0:
            return w
        return {k: v / total for k, v in w.items()}

    for _ in range(5):  # Iterate to converge
        clipped = {}
        excess = 0.0
        free_codes = []

        for code, w in weights.items():
            if w > max_weight:
                excess += w - max_weight
                clipped[code] = max_weight
            elif w < min_weight and len(weights) > 1:
                excess += w - min_weight  # negative → adds to excess
                clipped[code] = min_weight
            else:
                clipped[code] = w
                free_codes.append(code)

        # Distribute excess among free codes
        if free_codes and abs(excess) > 1e-8:
            per_code = excess / len(free_codes)
            for code in free_codes:
                clipped[code] += per_code

        weights = _normalize(clipped)

    return weights

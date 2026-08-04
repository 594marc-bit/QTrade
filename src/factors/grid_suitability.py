"""Grid suitability factor: scores stocks on fitness for grid trading.

Evaluates four dimensions, with range-purity as the dominant signal:
1. Range purity (double weight): avg daily oscillation / total period movement.
   High = jittery but not trending. Low = trending or dead.
2. Liquidity: can the stock actually be traded?
3. Mean-reversion: does price return to a central level?
4. Trend penalty (multiplier): strong directional moves kill grid profits.
"""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class GridSuitabilityFactor(FactorBase):
    """Grid trading suitability — prioritises range-bound oscillators."""

    factor_name: str = "grid_suitability"
    description: str = "Grid trading suitability (range purity weighted)"
    description_cn: str = "网格适用度（震荡纯度加权）"
    category: str = "网格"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # ── 1. Range purity ──────────────────────────────────────
        # High daily oscillation / low total drift = pure oscillation.
        df["intraday_range"] = (df["high"] - df["low"]) / df["close"]
        # 60-day avg daily range
        df["avg_range_60"] = (
            df.groupby("ts_code")["intraday_range"]
            .transform(lambda x: x.rolling(60, min_periods=20).mean())
        )
        # 120-day total drift (abs)
        df["total_drift_120"] = (
            df.groupby("ts_code")["close"]
            .transform(lambda x:
                np.abs(x.rolling(120, min_periods=60).apply(
                    lambda w: (w.iloc[-1] / w.iloc[0] - 1), raw=False))
            )
        )
        # Purity: avg_daily_range / total_drift.
        # CRITICAL: if the daily range is too small to cover trading costs
        # (~0.11% round-trip), grid trading is impossible regardless of purity.
        # Also guard against divide-by-zero for ultra-flat stocks.
        df["purity_raw"] = np.where(
            (df["total_drift_120"] > 0.001) & (df["avg_range_60"] > 0.005),
            df["avg_range_60"] / df["total_drift_120"],
            0.0,
        )
        # Score: purity=0.5 → 1.0 base
        df["purity_score"] = df["purity_raw"] / 0.5

        # ── 2. Liquidity ─────────────────────────────────────────
        df["avg_amount_20d"] = (
            df.groupby("ts_code")["amount"]
            .transform(lambda x: x.rolling(20, min_periods=10).mean())
        )
        amount_threshold = 5000  # 5M yuan (thousands)
        df["liquidity_score"] = np.minimum(
            df["avg_amount_20d"] / amount_threshold, 1.0
        )

        # ── 3. Mean-reversion ────────────────────────────────────
        df["ma_20"] = (
            df.groupby("ts_code")["close"]
            .transform(lambda x: x.rolling(20, min_periods=10).mean())
        )
        df["std_20"] = (
            df.groupby("ts_code")["close"]
            .transform(lambda x: x.rolling(20, min_periods=10).std())
        )
        upper = df["ma_20"] + 2 * df["std_20"]
        lower = df["ma_20"] - 2 * df["std_20"]
        df["in_bands"] = ((df["close"] >= lower) & (df["close"] <= upper)).astype(float)
        df["bands_ratio"] = (
            df.groupby("ts_code")["in_bands"]
            .transform(lambda x: x.rolling(120, min_periods=40).mean())
        )
        df["reversion_score"] = np.minimum(df["bands_ratio"] / 0.6, 1.0)

        # ── 4. Trend penalty (multiplicative) ────────────────────
        df["close_120d_ago"] = (
            df.groupby("ts_code")["close"].transform(lambda x: x.shift(120))
        )
        df["return_120d_abs"] = np.abs(
            (df["close"] - df["close_120d_ago"]) / df["close_120d_ago"]
        )
        df["trend_score"] = np.maximum(0, 1.0 - (df["return_120d_abs"] / 0.60) ** 2)

        # ── Composite ────────────────────────────────────────────
        # Range purity gets 2x weight; trend acts as a multiplier.
        # HARD GATE: if avg daily range is too small to cover costs, stock is
        # worthless for grid trading regardless of other dimensions.
        df["tradable"] = df["avg_range_60"] >= 0.005  # 0.5% min daily range
        df["grid_suitability_raw"] = np.where(
            df["tradable"],
            (2.0 * df["purity_score"] + df["liquidity_score"] + df["reversion_score"])
            / 4.0 * df["trend_score"],
            -10.0,  # well below any plausible score → excluded
        )

        # Cross-sectional Z-score
        df["grid_suitability"] = (
            df.groupby("trade_date")["grid_suitability_raw"]
            .transform(lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0.0)
        )

        # Cleanup
        drop_cols = [
            "intraday_range", "avg_range_60", "total_drift_120", "purity_raw",
            "purity_score", "avg_amount_20d", "liquidity_score",
            "tradable",
            "ma_20", "std_20", "in_bands", "bands_ratio", "reversion_score",
            "close_120d_ago", "return_120d_abs", "trend_score",
            "grid_suitability_raw",
        ]
        df = df.drop(columns=[c for c in drop_cols if c in df.columns])
        return df

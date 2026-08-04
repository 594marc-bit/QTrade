"""Profitability / quality factors: ROE level rank and ROE stability."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class RoeRank(FactorBase):
    factor_name = "roe_rank"
    description = "Cross-sectional percentile rank of ROE level (high = more profitable)"
    description_cn = "ROE绝对水平截面百分位排名"
    category = "基本面类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        if "roe" not in df.columns:
            df[self.factor_name] = np.nan
            return df

        df[self.factor_name] = df.groupby("trade_date")["roe"].rank(pct=True) * 100

        return df


@register_factor
class RoeStability(FactorBase):
    factor_name = "roe_stability"
    description = "Negative std of quarterly ROE (more stable = higher score, longer history = more stable)"
    description_cn = "ROE稳定性（季度ROE波动率取负）"
    category = "基本面类"

    def __init__(self, quarters: int = 4):
        self.quarters = quarters

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        if "roe" not in df.columns:
            df[self.factor_name] = np.nan
            return df

        # ROE is quarterly, forward-filled to daily.
        # We compute std of unique quarterly ROE values over time to get true quarterly dispersion.
        # Using rolling std on daily data with a large window approximates this
        # since ROE updates quarterly (forward-filled), the daily rolling std
        # captures quarterly variation. Use ~252 days to cover ~4 quarters.
        window_days = self.quarters * 63  # ~63 trading days per quarter

        roe_std = (
            df.groupby("ts_code")["roe"]
            .rolling(window_days, min_periods=window_days)
            .std()
            .reset_index(level=0, drop=True)
        )

        # Negative sign: more stable = less variation = higher score
        df[self.factor_name] = -roe_std

        return df

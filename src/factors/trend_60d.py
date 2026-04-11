"""60-day trend factor: combines MA60 slope and price deviation to measure medium-term trend strength."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class Trend60dFactor(FactorBase):
    factor_name = "trend_60d"
    description = "60-day trend strength (MA60 slope + price deviation)"
    description_cn = "60日趋势强度（均线斜率+价格偏离）"
    category = "量价类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # MA60
        ma60 = df.groupby("ts_code")["close"].transform(
            lambda x: x.rolling(60, min_periods=60).mean()
        )

        # MA60 5 days ago (for slope)
        ma60_5d_ago = df.groupby("ts_code")["close"].transform(
            lambda x: x.rolling(60, min_periods=60).mean().shift(5)
        )

        # MA slope = (MA60 - MA60_5d_ago) / MA60_5d_ago
        ma_slope = np.where(ma60_5d_ago == 0, np.nan, (ma60 - ma60_5d_ago) / ma60_5d_ago)

        # Price deviation = (close - MA60) / MA60
        price_deviation = np.where(ma60 == 0, np.nan, (df["close"] - ma60) / ma60)

        # Combined: slope * 0.6 + deviation * 0.4
        df[self.factor_name] = ma_slope * 0.6 + price_deviation * 0.4

        return df

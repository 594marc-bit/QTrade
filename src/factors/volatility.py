"""Volatility factor: 20-day return standard deviation."""

import pandas as pd

from src.config import VOLATILITY_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class VolatilityFactor(FactorBase):
    factor_name = "volatility_20d"
    description = "20-day return standard deviation (volatility)"

    def __init__(self, window: int = VOLATILITY_WINDOW):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Daily returns
        daily_ret = df.groupby("ts_code")["close"].pct_change()

        # Rolling standard deviation
        df[self.factor_name] = daily_ret.rolling(self.window, min_periods=self.window).std()

        return df

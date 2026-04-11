"""Intraday range factor: 10-day average intraday volatility proxy."""

import numpy as np
import pandas as pd

from src.config import INTRADAY_RANGE_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class IntradayRangeFactor(FactorBase):
    factor_name = "intraday_range_10d"
    description = "10-day avg intraday range (high-low)/close"
    description_cn = "10日均日内振幅"
    category = "波动率类"

    def __init__(self, window: int = INTRADAY_RANGE_WINDOW):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_range = np.where(df["close"] == 0, np.nan, (df["high"] - df["low"]) / df["close"])
        daily_range = pd.Series(daily_range, index=df.index)

        df[self.factor_name] = daily_range.groupby(df["ts_code"]).transform(
            lambda x: x.rolling(self.window, min_periods=self.window).mean()
        )

        return df

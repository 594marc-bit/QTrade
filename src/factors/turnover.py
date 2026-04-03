"""Turnover momentum factor: 5d/20d average volume ratio (volume surge detection)."""

import numpy as np
import pandas as pd

from src.config import TURNOVER_SHORT_WINDOW, TURNOVER_LONG_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class TurnoverFactor(FactorBase):
    factor_name = "turnover_momentum_10d"
    description = "5d/20d average volume ratio (volume surge detection)"

    def __init__(
        self,
        short_window: int = TURNOVER_SHORT_WINDOW,
        long_window: int = TURNOVER_LONG_WINDOW,
    ):
        self.short_window = short_window
        self.long_window = long_window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        short_ma = df.groupby("ts_code")["vol"].transform(
            lambda x: x.rolling(self.short_window, min_periods=self.short_window).mean()
        )
        long_ma = df.groupby("ts_code")["vol"].transform(
            lambda x: x.rolling(self.long_window, min_periods=self.long_window).mean()
        )

        df[self.factor_name] = np.where(long_ma == 0, np.nan, short_ma / long_ma)

        return df

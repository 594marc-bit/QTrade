"""Volume momentum factor: 5d/20d average amount ratio."""

import numpy as np
import pandas as pd

from src.config import VOLUME_SHORT_WINDOW, VOLUME_LONG_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class VolumeFactor(FactorBase):
    factor_name = "vol_ratio"
    description = "5-day / 20-day average amount ratio"
    description_cn = "5日/20日成交额比率"
    category = "量价类"

    def __init__(
        self,
        short_window: int = VOLUME_SHORT_WINDOW,
        long_window: int = VOLUME_LONG_WINDOW,
    ):
        self.short_window = short_window
        self.long_window = long_window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Rolling averages per stock
        short_ma = df.groupby("ts_code")["amount"].transform(
            lambda x: x.rolling(self.short_window, min_periods=self.short_window).mean()
        )
        long_ma = df.groupby("ts_code")["amount"].transform(
            lambda x: x.rolling(self.long_window, min_periods=self.long_window).mean()
        )

        # Ratio with zero-division protection
        df[self.factor_name] = np.where(long_ma == 0, np.nan, short_ma / long_ma)

        return df

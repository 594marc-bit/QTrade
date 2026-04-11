"""MA deviation factor: price deviation from 20-day moving average (mean reversion)."""

import numpy as np
import pandas as pd

from src.config import MA_DEVIATION_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class MaDeviationFactor(FactorBase):
    factor_name = "ma_deviation_20d"
    description = "Price deviation from 20-day MA (mean reversion)"
    description_cn = "价格偏离20日均线"
    category = "量价类"

    def __init__(self, window: int = MA_DEVIATION_WINDOW):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        ma = df.groupby("ts_code")["close"].transform(
            lambda x: x.rolling(self.window, min_periods=self.window).mean()
        )

        df[self.factor_name] = np.where(ma == 0, np.nan, (df["close"] - ma) / ma)

        return df

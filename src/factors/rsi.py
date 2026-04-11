"""RSI factor: 14-day Relative Strength Index (Wilder's smoothing)."""

import numpy as np
import pandas as pd

from src.config import RSI_WINDOW
from src.factors.base import FactorBase, register_factor


@register_factor
class RsiFactor(FactorBase):
    factor_name = "rsi_14d"
    description = "14-day RSI (Wilder's smoothing)"
    description_cn = "14日相对强弱指标"
    category = "波动率类"

    def __init__(self, window: int = RSI_WINDOW):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        delta = df.groupby("ts_code")["close"].transform(lambda x: x.diff())

        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        avg_gain = gain.groupby(df["ts_code"]).transform(
            lambda x: x.ewm(alpha=1 / self.window, min_periods=self.window).mean()
        )
        avg_loss = loss.groupby(df["ts_code"]).transform(
            lambda x: x.ewm(alpha=1 / self.window, min_periods=self.window).mean()
        )

        rs = np.where(avg_loss == 0, np.inf, avg_gain / avg_loss)
        df[self.factor_name] = 100 - 100 / (1 + rs)

        return df

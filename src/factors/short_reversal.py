"""Short-term reversal and price acceleration factors."""

import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class ShortReversal(FactorBase):
    factor_name = "reversal_5d"
    description = "5-day price return (short-term reversal, negative expected premium)"
    description_cn = "5日短期反转"
    category = "量价类"

    def __init__(self, window: int = 5):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        df[self.factor_name] = df.groupby("ts_code")["close"].pct_change(self.window)

        return df


@register_factor
class PriceAcceleration(FactorBase):
    factor_name = "price_acceleration"
    description = "Momentum acceleration: 5-day return minus 20-day return"
    description_cn = "价格加速度（短期动量 − 中期动量）"
    category = "量价类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        mom_5d = df.groupby("ts_code")["close"].pct_change(5)
        mom_20d = df.groupby("ts_code")["close"].pct_change(20)

        df[self.factor_name] = mom_5d - mom_20d

        return df

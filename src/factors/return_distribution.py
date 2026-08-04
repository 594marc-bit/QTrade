"""Return distribution factors: skewness and kurtosis of daily returns."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class Skewness(FactorBase):
    factor_name = "skewness_60d"
    description = "60-day return distribution skewness (negative skew predicts lower returns)"
    description_cn = "60日收益偏度"
    category = "尾部风险类"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()

        df[self.factor_name] = (
            daily_ret.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .skew()
            .reset_index(level=0, drop=True)
        )

        return df


@register_factor
class Kurtosis(FactorBase):
    factor_name = "kurtosis_60d"
    description = "60-day return distribution kurtosis (tail risk pricing)"
    description_cn = "60日收益峰度"
    category = "尾部风险类"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()

        df[self.factor_name] = (
            daily_ret.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .kurt()
            .reset_index(level=0, drop=True)
        )

        return df

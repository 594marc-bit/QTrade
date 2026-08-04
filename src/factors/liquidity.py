"""Liquidity factors: dollar volume and Amihud illiquidity."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class DollarVolume(FactorBase):
    factor_name = "dollar_volume_20d"
    description = "Log of 20-day average daily dollar volume (size/liquidity proxy)"
    description_cn = "20日均成交额对数（流动性/规模）"
    category = "流动性类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Average daily amount
        avg_amount = (
            df.groupby("ts_code")["amount"]
            .rolling(self.window, min_periods=self.window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        df[self.factor_name] = np.log(avg_amount.replace(0, np.nan))

        return df


@register_factor
class Amihud(FactorBase):
    factor_name = "amihud_20d"
    description = "20-day Amihud illiquidity measure: avg(|return|/amount * 1e6)"
    description_cn = "20日Amihud非流动性指标"
    category = "流动性类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()
        illiq = np.abs(daily_ret) / df["amount"].replace(0, np.nan) * 1e6

        df[self.factor_name] = (
            illiq.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        return df

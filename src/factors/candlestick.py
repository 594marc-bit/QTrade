"""Candlestick pattern factors: upper/lower shadows and body ratio."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class UpperShadow(FactorBase):
    factor_name = "upper_shadow_20d"
    description = "20-day average upper shadow ratio (selling pressure)"
    description_cn = "20日均上影线占比"
    category = "形态类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Upper shadow = high - max(open, close), normalized by close
        upper = df["high"] - df[["open", "close"]].max(axis=1)
        upper_ratio = upper / df["close"].replace(0, np.nan)

        df[self.factor_name] = (
            upper_ratio.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        return df


@register_factor
class LowerShadow(FactorBase):
    factor_name = "lower_shadow_20d"
    description = "20-day average lower shadow ratio (buying support)"
    description_cn = "20日均下影线占比"
    category = "形态类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Lower shadow = min(open, close) - low, normalized by close
        lower = df[["open", "close"]].min(axis=1) - df["low"]
        lower_ratio = lower / df["close"].replace(0, np.nan)

        df[self.factor_name] = (
            lower_ratio.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        return df


@register_factor
class BodyRatio(FactorBase):
    factor_name = "body_ratio_20d"
    description = "20-day average candlestick body-to-range ratio"
    description_cn = "20日均实体占比"
    category = "形态类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        # Body = |open - close|, Range = high - low
        body = np.abs(df["open"] - df["close"])
        range_val = (df["high"] - df["low"]).replace(0, np.nan)
        body_ratio = body / range_val

        df[self.factor_name] = (
            body_ratio.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        return df

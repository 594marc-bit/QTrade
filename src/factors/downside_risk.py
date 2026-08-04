"""Downside risk factors: downside volatility and max drawdown."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class DownsideVolatility(FactorBase):
    factor_name = "downside_volatility_60d"
    description = "60-day standard deviation of negative daily returns"
    description_cn = "60日下行波动率（仅负收益的std）"
    category = "尾部风险类"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()
        # Only negative returns
        downside = daily_ret.clip(upper=0)

        df[self.factor_name] = (
            downside.groupby(df["ts_code"])
            .rolling(self.window, min_periods=self.window)
            .std()
            .reset_index(level=0, drop=True)
        )

        return df


@register_factor
class MaxDrawdown(FactorBase):
    factor_name = "max_drawdown_60d"
    description = "60-day rolling maximum drawdown (peak-to-trough)"
    description_cn = "60日滚动最大回撤"
    category = "尾部风险类"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        def rolling_max_drawdown(close_series):
            """Calculate rolling max drawdown for a single stock."""
            result = pd.Series(np.nan, index=close_series.index)
            for i in range(self.window - 1, len(close_series)):
                window_data = close_series.iloc[i - self.window + 1 : i + 1]
                cummax = window_data.cummax()
                drawdowns = (window_data - cummax) / cummax
                result.iloc[i] = drawdowns.min()
            return result

        df[self.factor_name] = df.groupby("ts_code")["close"].transform(
            rolling_max_drawdown
        )

        return df

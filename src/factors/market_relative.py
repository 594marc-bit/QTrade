"""Market-relative factors: Beta, Alpha, and Relative Strength vs benchmark index.

These factors require index return data, optionally passed via constructor.
"""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class Beta(FactorBase):
    factor_name = "beta_60d"
    description = "60-day CAPM beta vs benchmark index"
    description_cn = "60日Beta（相对基准指数）"
    category = "市场相对类"

    def __init__(self, window: int = 60, index_returns: pd.Series | None = None):
        self.window = window
        self.index_returns = index_returns  # Series indexed by trade_date

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()

        if self.index_returns is None:
            df[self.factor_name] = np.nan
            return df

        # Align index returns to the DataFrame
        idx_ret = self.index_returns.reindex(df["trade_date"].values)
        idx_ret.index = df.index

        def rolling_beta(stock_ret):
            """Compute rolling beta = cov(stock, index) / var(index)."""
            cov = stock_ret.rolling(self.window, min_periods=self.window).cov(idx_ret)
            var = idx_ret.rolling(self.window, min_periods=self.window).var()
            return np.where(var == 0, np.nan, cov / var)

        df[self.factor_name] = daily_ret.groupby(df["ts_code"]).transform(rolling_beta)

        return df


@register_factor
class Alpha(FactorBase):
    factor_name = "alpha_60d"
    description = "60-day Jensen's alpha (annualized) vs benchmark index"
    description_cn = "60日年化Alpha（相对基准指数）"
    category = "市场相对类"

    def __init__(self, window: int = 60, index_returns: pd.Series | None = None):
        self.window = window
        self.index_returns = index_returns

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()

        if self.index_returns is None:
            df[self.factor_name] = np.nan
            return df

        idx_ret = self.index_returns.reindex(df["trade_date"].values)
        idx_ret.index = df.index

        def rolling_alpha(stock_ret):
            """Annualized alpha = (mean(stock) - mean(index)) * 252."""
            stock_mean = stock_ret.rolling(self.window, min_periods=self.window).mean()
            idx_mean = idx_ret.rolling(self.window, min_periods=self.window).mean()
            return (stock_mean - idx_mean) * 252

        df[self.factor_name] = daily_ret.groupby(df["ts_code"]).transform(rolling_alpha)

        return df


@register_factor
class RelativeStrength(FactorBase):
    factor_name = "relative_strength_20d"
    description = "20-day excess return over benchmark index"
    description_cn = "20日相对强度（超额收益）"
    category = "市场相对类"

    def __init__(self, index_returns: pd.Series | None = None):
        self.index_returns = index_returns

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        stock_ret = df.groupby("ts_code")["close"].pct_change(20)

        if self.index_returns is None:
            df[self.factor_name] = stock_ret
            return df

        # 20-day cumulative index return
        idx_cum_ret = (1 + self.index_returns).rolling(20).apply(
            lambda x: np.prod(1 + x) - 1, raw=True
        )
        idx_20d = idx_cum_ret.reindex(df["trade_date"].values)
        idx_20d.index = df.index

        df[self.factor_name] = stock_ret - idx_20d

        return df

"""Volume-price interaction factor: correlation between volume changes and returns."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class VolumePriceCorr(FactorBase):
    factor_name = "volume_price_corr_20d"
    description = "20-day rolling correlation between volume change and price return"
    description_cn = "20日量价相关系数"
    category = "量价类"

    def __init__(self, window: int = 20):
        self.window = window

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        daily_ret = df.groupby("ts_code")["close"].pct_change()
        vol_change = df.groupby("ts_code")["vol"].pct_change()

        def rolling_corr(group_ret, group_vol):
            """Compute rolling correlation between two series within each stock group."""
            result = pd.Series(np.nan, index=group_ret.index)
            for ts_code in df["ts_code"].unique():
                mask = df["ts_code"] == ts_code
                r = group_ret[mask]
                v = group_vol[mask]
                rolling_corr = r.rolling(self.window, min_periods=self.window).corr(v)
                result[mask] = rolling_corr.values
            return result

        df[self.factor_name] = rolling_corr(daily_ret, vol_change)

        return df

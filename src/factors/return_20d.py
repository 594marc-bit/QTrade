"""20-day return factor: percentage price change over 20 trading days."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class Return20dFactor(FactorBase):
    factor_name = "return_20d"
    description = "20-day price return percentage"
    description_cn = "20日涨幅"
    category = "量价类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])
        df[self.factor_name] = df.groupby("ts_code")["close"].pct_change(20)
        return df

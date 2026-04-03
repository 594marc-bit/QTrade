"""Momentum factor: 20-day return."""

import pandas as pd

from src.config import MOMENTUM_LOOKBACK
from src.factors.base import FactorBase, register_factor


@register_factor
class MomentumFactor(FactorBase):
    factor_name = "momentum_20d"
    description = "20-day price return (momentum)"

    def __init__(self, lookback: int = MOMENTUM_LOOKBACK):
        self.lookback = lookback

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])
        df[self.factor_name] = df.groupby("ts_code")["close"].pct_change(self.lookback)
        return df

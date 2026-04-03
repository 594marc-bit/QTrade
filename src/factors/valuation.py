"""Valuation factors: cross-sectional percentile ranking of PE_TTM and PB."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class PeFactor(FactorBase):
    factor_name = "pe_ttm_rank"
    description = "Cross-sectional percentile rank of PE_TTM (low = undervalued)"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = df.groupby("trade_date")["pe_ttm"].rank(pct=True) * 100
        return df


@register_factor
class PbFactor(FactorBase):
    factor_name = "pb_rank"
    description = "Cross-sectional percentile rank of PB (low = undervalued)"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = df.groupby("trade_date")["pb"].rank(pct=True) * 100
        return df

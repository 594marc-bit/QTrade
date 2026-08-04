"""Extended valuation factors: PS_TTM rank and earnings yield."""

import numpy as np
import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class PsTtmRank(FactorBase):
    factor_name = "ps_ttm_rank"
    description = "Cross-sectional percentile rank of PS_TTM (low = undervalued)"
    description_cn = "PS_TTM截面百分位排名"
    category = "估值类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.sort_values(["ts_code", "trade_date"])

        if "ps_ttm" not in df.columns:
            df[self.factor_name] = np.nan
            return df

        # Lower PS = better value, so rank ascending
        df[self.factor_name] = df.groupby("trade_date")["ps_ttm"].rank(pct=True) * 100

        return df


@register_factor
class EpRatio(FactorBase):
    factor_name = "ep_ratio"
    description = "Earnings yield = 1 / PE_TTM (higher = cheaper)"
    description_cn = "盈利收益率（1/PE）"
    category = "估值类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        if "pe_ttm" not in df.columns:
            df[self.factor_name] = np.nan
            return df

        pe = df["pe_ttm"].replace(0, np.nan)
        df[self.factor_name] = 1.0 / pe

        return df

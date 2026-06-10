"""ROE change factor: cross-sectional percentile ranking of ROE year-over-year change."""

import pandas as pd

from src.factors.base import FactorBase, register_factor


@register_factor
class RoeChangeFactor(FactorBase):
    factor_name = "roe_yoy_rank"
    description = "Cross-sectional percentile rank of ROE YoY change (high = improving profitability)"
    description_cn = "ROE同比变化率截面百分位排名"
    category = "基本面类"

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[self.factor_name] = df.groupby("trade_date")["roe_yoy"].rank(pct=True) * 100
        return df

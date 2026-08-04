"""Grid backtest result container, compatible with existing BacktestResult."""

from dataclasses import dataclass, field

import pandas as pd

from src.backtest.result import BacktestResult


@dataclass
class GridBacktestResult:
    """Grid trading backtest result.

    Reuses the existing BacktestResult structure for nav_series, trades,
    and metrics compatibility. Adds grid-specific fields.
    """

    ts_code: str = ""
    params_summary: str = ""

    # Standard fields (BacktestResult compatible)
    nav_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)

    # Grid-specific fields
    grid_level_stats: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            columns=["grid_level", "grid_price", "buy_count", "sell_count",
                     "total_buy_amount", "total_sell_amount", "grid_profit"]
        )
    )
    attribution: dict = field(default_factory=lambda: {
        "grid_trading_return": 0.0,
        "base_position_return": 0.0,
        "total_return": 0.0,
        "total_commission": 0.0,
        "total_stamp_tax": 0.0,
    })

    def to_backtest_result(self) -> BacktestResult:
        """Convert to standard BacktestResult for charting compatibility."""
        return BacktestResult(
            nav_series=self.nav_series,
            trades=self.trades,
            metrics=self.metrics,
        )

    @property
    def summary(self) -> str:
        """One-line summary string."""
        m = self.metrics
        return (
            f"{self.ts_code} | "
            f"年化={m.get('annual_return', 0):.2%} | "
            f"回撤={m.get('max_drawdown', 0):.2%} | "
            f"成交={m.get('trade_count', 0)}笔 | "
            f"网格收益={self.attribution.get('grid_trading_return', 0):.2%} | "
            f"底仓收益={self.attribution.get('base_position_return', 0):.2%}"
        )

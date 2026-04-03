"""Backtest result data container."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BacktestResult:
    """Container for backtest outputs.

    Attributes:
        nav_series: DataFrame with columns [date, nav, benchmark_nav, position_count].
        trades: DataFrame with columns [date, ts_code, action, shares, price, amount, cost].
        metrics: Dict of performance metrics.
    """

    nav_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        """Return a formatted summary string of backtest metrics."""
        m = self.metrics
        lines = [
            "=" * 50,
            "  回测绩效摘要",
            "=" * 50,
            f"总收益率:     {m.get('total_return', 0):.2%}",
            f"年化收益率:   {m.get('annual_return', 0):.2%}",
            f"夏普比率:     {m.get('sharpe_ratio', 0):.4f}",
            f"最大回撤:     {m.get('max_drawdown', 0):.2%}",
            f"Calmar比率:   {m.get('calmar_ratio', 0):.4f}",
            f"胜率:         {m.get('win_rate', 0):.2%}",
            f"调仓次数:     {m.get('trade_count', 0)}",
        ]
        if "alpha" in m:
            lines.append(f"Alpha:        {m['alpha']:.2%}")
            lines.append(f"信息比率:    {m.get('information_ratio', 0):.4f}")
        return "\n".join(lines)

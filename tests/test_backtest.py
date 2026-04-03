"""Tests for backtest engine modules."""

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.metrics import compute_metrics, compute_drawdown_series
from src.backtest.portfolio import Portfolio
from src.backtest.result import BacktestResult


class TestPortfolio:
    def test_initial_state(self):
        p = Portfolio(initial_capital=500_000)
        assert p.cash == 500_000
        assert len(p.positions) == 0

    def test_buy_reduces_cash(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"600519.SH": 100.0}
        p.rebalance(["600519.SH"], prices, "20260101")
        assert p.cash < 1_000_000
        assert "600519.SH" in p.positions
        assert p.positions["600519.SH"]["shares"] >= 100

    def test_rebalance_equal_weight(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"A": 100.0, "B": 200.0, "C": 50.0}
        p.rebalance(["A", "B", "C"], prices, "20260101")
        assert len(p.positions) == 3
        # Each should have roughly equal value
        values = [p.positions[c]["shares"] * prices[c] for c in p.positions]
        if len(values) >= 2:
            avg = np.mean(values)
            for v in values:
                assert abs(v - avg) / avg < 0.15  # Within 15%

    def test_sell_old_positions(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"A": 100.0, "B": 200.0}
        p.rebalance(["A", "B"], prices, "20260101")

        # Rebalance to only B — must include A's price so it can be sold
        new_prices = {"A": 100.0, "B": 200.0, "C": 150.0}
        p.rebalance(["B", "C"], new_prices, "20260102")
        assert "A" not in p.positions
        assert any(t["action"] == "sell" and t["ts_code"] == "A" for t in p.trades)

    def test_suspended_stock_cannot_sell(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"A": 100.0, "B": 200.0}
        p.rebalance(["A", "B"], prices, "20260101")

        # A is suspended — should be kept
        new_prices = {"B": 200.0, "C": 150.0}
        p.rebalance(
            ["B", "C"], new_prices, "20260102",
            suspended_codes={"A"},
        )
        assert "A" in p.positions  # Cannot sell suspended

    def test_trading_costs_deducted(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"A": 100.0}
        p.rebalance(["A"], prices, "20260101")
        assert p.cash < 1_000_000  # Cash should be less due to commission

    def test_market_value(self):
        p = Portfolio(initial_capital=1_000_000)
        prices = {"A": 100.0}
        p.rebalance(["A"], prices, "20260101")
        mv = p.market_value(prices)
        assert mv < 1_000_000  # Due to trading costs


class TestMetrics:
    def test_basic_metrics(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        nav = pd.Series(np.linspace(1.0, 1.5, 100), index=dates)
        m = compute_metrics(nav)
        assert m["total_return"] > 0
        assert m["annual_return"] > 0
        assert m["sharpe_ratio"] != 0
        assert m["max_drawdown"] <= 0
        assert 0 < m["win_rate"] <= 1

    def test_sharpe_positive_for_good_strategy(self):
        dates = pd.date_range("2025-01-01", periods=500, freq="B")
        # Steadily increasing NAV with small noise
        np.random.seed(42)
        returns = 0.001 + np.random.normal(0, 0.005, 500)
        nav = pd.Series(np.cumprod(1 + returns), index=dates)
        m = compute_metrics(nav)
        assert m["sharpe_ratio"] > 0.5  # Good strategy should have positive Sharpe

    def test_max_drawdown_with_decline(self):
        nav_values = [1.0, 1.1, 1.2, 1.0, 0.9, 1.1, 1.3]
        dates = pd.date_range("2025-01-01", periods=len(nav_values), freq="B")
        nav = pd.Series(nav_values, index=dates)
        m = compute_metrics(nav)
        # Max drawdown should be (0.9 - 1.2) / 1.2 = -0.25
        assert abs(m["max_drawdown"] - (-0.25)) < 0.01

    def test_benchmark_alpha(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        nav = pd.Series(np.linspace(1.0, 1.5, 100), index=dates)
        benchmark = pd.Series(np.linspace(1.0, 1.2, 100), index=dates)
        m = compute_metrics(nav, benchmark)
        assert m["alpha"] > 0
        assert "information_ratio" in m

    def test_drawdown_series(self):
        nav_values = [1.0, 1.1, 1.2, 1.0, 0.9, 1.1]
        dates = pd.date_range("2025-01-01", periods=len(nav_values), freq="B")
        nav = pd.Series(nav_values, index=dates)
        dd = compute_drawdown_series(nav)
        assert dd.min() < 0
        assert abs(dd.iloc[4] - (-0.25)) < 0.01  # (0.9 - 1.2) / 1.2

    def test_empty_nav(self):
        m = compute_metrics(pd.Series(dtype=float))
        assert m == {}

    def test_short_nav(self):
        nav = pd.Series([1.0], index=pd.date_range("2025-01-01", periods=1))
        m = compute_metrics(nav)
        assert m == {}


class TestBacktestEngine:
    def _make_test_data(self, n_stocks=20, n_days=60):
        """Create synthetic test data with total_score."""
        dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
        codes = [f"{i:06d}.SH" for i in range(1, n_stocks + 1)]

        rows = []
        for d in dates:
            for c in codes:
                score = np.random.randn()
                close = 10 + np.random.rand() * 100
                rows.append({
                    "trade_date": d.strftime("%Y%m%d"),
                    "ts_code": c,
                    "close": close,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "vol": 10000,
                    "amount": close * 10000,
                    "total_score": score,
                })
        return pd.DataFrame(rows)

    def test_basic_backtest(self):
        df = self._make_test_data()
        engine = BacktestEngine(initial_capital=1_000_000, top_n=5, rebalance_freq="M")
        result = engine.run(df)
        assert isinstance(result, BacktestResult)
        assert not result.nav_series.empty
        assert "total_return" in result.metrics
        assert len(result.trades) > 0

    def test_weekly_rebalance(self):
        df = self._make_test_data()
        engine = BacktestEngine(rebalance_freq="W")
        result = engine.run(df)
        assert not result.nav_series.empty

    def test_daily_rebalance(self):
        df = self._make_test_data()
        engine = BacktestEngine(rebalance_freq="D")
        result = engine.run(df)
        assert not result.nav_series.empty

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        engine = BacktestEngine()
        with pytest.raises(ValueError, match="Missing required columns"):
            engine.run(df)

    def test_missing_score_raises(self):
        df = pd.DataFrame({
            "trade_date": ["20250101"],
            "ts_code": ["000001.SZ"],
            "close": [10.0],
            "open": [9.9],
        })
        engine = BacktestEngine()
        with pytest.raises(ValueError, match="total_score"):
            engine.run(df)

    def test_summary_output(self):
        df = self._make_test_data()
        engine = BacktestEngine(top_n=5)
        result = engine.run(df)
        text = result.summary()
        assert "总收益率" in text
        assert "夏普比率" in text


class TestBacktestMetricsPrecision:
    """Verify metric calculation precision with known inputs."""

    def test_calmar_ratio(self):
        # NAV goes from 1.0 to 1.3 over ~242 days = ~30% annual return
        # Max drawdown = -0.1 → Calmar = 0.3 / 0.1 = 3.0
        nav_values = [1.0, 1.05, 1.1, 1.2, 1.1, 1.2, 1.3]
        dates = pd.date_range("2025-01-01", periods=len(nav_values), freq="B")
        nav = pd.Series(nav_values, index=dates)
        m = compute_metrics(nav)
        assert m["calmar_ratio"] > 0

    def test_win_rate_all_positive(self):
        # Monotonically increasing NAV → win rate should be 100%
        nav_values = list(np.linspace(1.0, 2.0, 50))
        dates = pd.date_range("2025-01-01", periods=len(nav_values), freq="B")
        nav = pd.Series(nav_values, index=dates)
        m = compute_metrics(nav)
        assert m["win_rate"] == 1.0

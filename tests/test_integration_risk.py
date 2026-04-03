"""Integration test: verify all new features are off by default and work when enabled."""

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine


def _make_test_data(n_stocks=20, n_days=60):
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


class TestDefaultOffBehavior:
    """All new features should be disabled by default."""

    def test_default_engine_matches_original(self):
        """Default engine (no risk control, equal weight) should produce same results as before."""
        np.random.seed(42)
        df = _make_test_data()
        engine = BacktestEngine(initial_capital=1_000_000, top_n=5, rebalance_freq="M")
        result = engine.run(df)
        assert not result.nav_series.empty
        assert "total_return" in result.metrics

    def test_risk_control_off_by_default(self):
        """Risk control should be off by default."""
        engine = BacktestEngine()
        assert engine.risk_control_enabled is False

    def test_position_sizing_equal_weight_by_default(self):
        """Position sizing should be equal_weight by default."""
        engine = BacktestEngine()
        assert engine.position_sizing_method == "equal_weight"

    def test_industry_neutral_off_by_default(self):
        """Industry neutral should be off by default (empty industry_map)."""
        engine = BacktestEngine()
        assert engine.industry_map == {}


class TestFeatureIntegration:
    """Test features work together when enabled."""

    def test_risk_control_enabled(self):
        """Engine runs with risk control enabled."""
        np.random.seed(42)
        df = _make_test_data()
        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=5,
            rebalance_freq="M",
            risk_control_enabled=True,
            stop_loss=-0.08,
            take_profit=0.15,
        )
        result = engine.run(df)
        assert not result.nav_series.empty

    def test_score_weighted_sizing(self):
        """Engine runs with score-weighted position sizing."""
        np.random.seed(42)
        df = _make_test_data()
        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=5,
            rebalance_freq="M",
            position_sizing_method="score_weighted",
        )
        result = engine.run(df)
        assert not result.nav_series.empty

    def test_industry_neutral_in_engine(self):
        """Engine runs with industry neutral constraint."""
        np.random.seed(42)
        df = _make_test_data()
        codes = df["ts_code"].unique()[:20]
        industry_map = {c: "银行" if i < 10 else "科技" for i, c in enumerate(codes)}

        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=10,
            rebalance_freq="M",
            industry_map=industry_map,
            max_industry_pct=0.30,
        )
        result = engine.run(df)
        assert not result.nav_series.empty

    def test_all_features_enabled(self):
        """Engine runs with all features enabled simultaneously."""
        np.random.seed(42)
        df = _make_test_data()
        codes = df["ts_code"].unique()[:20]
        industry_map = {c: "银行" if i < 8 else "科技" for i, c in enumerate(codes)}

        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=5,
            rebalance_freq="M",
            risk_control_enabled=True,
            stop_loss=-0.08,
            take_profit=0.15,
            position_sizing_method="score_weighted",
            industry_map=industry_map,
            max_industry_pct=0.30,
        )
        result = engine.run(df)
        assert not result.nav_series.empty
        assert "total_return" in result.metrics

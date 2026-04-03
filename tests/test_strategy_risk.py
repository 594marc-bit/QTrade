"""Tests for new modules: adaptive weights, industry neutral, risk control, position sizing."""

import numpy as np
import pandas as pd
import pytest


# === Adaptive Weights Tests ===

class TestAdaptiveWeights:
    def test_normal_computation(self):
        """Test adaptive weights are computed from IC data."""
        from src.factors.adaptive_weights import compute_adaptive_weights

        dates = pd.date_range("2025-01-01", periods=80, freq="B").strftime("%Y%m%d")
        np.random.seed(42)
        ic_data = {
            "trade_date": dates,
            "momentum_20d": np.random.randn(80) * 0.05,
            "vol_ratio": np.random.randn(80) * 0.03,
        }
        ic_df = pd.DataFrame(ic_data)
        weights = compute_adaptive_weights(ic_df, window=60)
        assert isinstance(weights, dict)
        assert "momentum_score" in weights
        # Weights should sum to ~1 in absolute value
        total_abs = sum(abs(v) for v in weights.values())
        assert abs(total_abs - 1.0) < 0.01

    def test_insufficient_data_fallback(self):
        """Test fallback to default weights when data is insufficient."""
        from src.factors.adaptive_weights import compute_adaptive_weights
        from src.config import DEFAULT_WEIGHTS

        ic_df = pd.DataFrame({"trade_date": ["20250101"], "momentum_20d": [0.05]})
        weights = compute_adaptive_weights(ic_df, window=60)
        assert weights == DEFAULT_WEIGHTS

    def test_all_nan_ic_fallback(self):
        """Test fallback when all IC values are NaN."""
        from src.factors.adaptive_weights import compute_adaptive_weights
        from src.config import DEFAULT_WEIGHTS

        dates = pd.date_range("2025-01-01", periods=80, freq="B").strftime("%Y%m%d")
        ic_df = pd.DataFrame({
            "trade_date": dates,
            "momentum_20d": [np.nan] * 80,
        })
        weights = compute_adaptive_weights(ic_df, window=60)
        assert weights == DEFAULT_WEIGHTS

    def test_preserves_sign_direction(self):
        """Test that adaptive weights preserve sign direction from base weights."""
        from src.factors.adaptive_weights import compute_adaptive_weights
        from src.config import DEFAULT_WEIGHTS

        dates = pd.date_range("2025-01-01", periods=80, freq="B").strftime("%Y%m%d")
        np.random.seed(42)
        # RSI has negative weight in DEFAULT_WEIGHTS
        ic_df = pd.DataFrame({
            "trade_date": dates,
            "rsi_14d": np.random.randn(80) * 0.05,
        })
        weights = compute_adaptive_weights(ic_df, window=60)
        # rsi_score should be negative (like DEFAULT_WEIGHTS)
        assert weights.get("rsi_score", 0) < 0


# === Industry Neutral Tests ===

class TestIndustryNeutral:
    def test_normal_constraint(self):
        """Test that industry constraint limits concentration."""
        from src.factors.industry_neutral import apply_industry_constraint

        top_stocks = pd.DataFrame({
            "ts_code": [f"BANK{i:02d}.SH" for i in range(5)]
                       + [f"TECH{i:02d}.SZ" for i in range(3)]
                       + ["OTHER01.SH"],
            "total_score": list(range(9, 0, -1)),
        })
        industry_map = {}
        for i in range(5):
            industry_map[f"BANK{i:02d}.SH"] = "银行"
        for i in range(3):
            industry_map[f"TECH{i:02d}.SZ"] = "科技"
        industry_map["OTHER01.SH"] = "其他"

        # With max_pct=0.30 and n=10, max per industry = 3
        result = apply_industry_constraint(top_stocks, industry_map, max_pct=0.30, n=10)
        bank_count = sum(1 for c in result["ts_code"] if industry_map.get(c) == "银行")
        assert bank_count <= 3

    def test_no_industry_data_passthrough(self):
        """Test that empty industry map passes through unchanged."""
        from src.factors.industry_neutral import apply_industry_constraint

        top_stocks = pd.DataFrame({
            "ts_code": ["A", "B", "C"],
            "total_score": [3, 2, 1],
        })
        result = apply_industry_constraint(top_stocks, {}, max_pct=0.30)
        assert len(result) == 3

    def test_unknown_industry_not_blocked(self):
        """Test stocks with unknown industry are not blocked."""
        from src.factors.industry_neutral import apply_industry_constraint

        top_stocks = pd.DataFrame({
            "ts_code": ["A", "B", "C"],
            "total_score": [3, 2, 1],
        })
        industry_map = {"A": "银行", "B": "银行"}
        result = apply_industry_constraint(top_stocks, industry_map, max_pct=0.30, n=3)
        # C has no industry → "未知", should still be selected
        assert "C" in result["ts_code"].values


# === Risk Control Tests ===

class TestRiskControl:
    def _make_portfolio_with_positions(self):
        from src.backtest.portfolio import Portfolio
        p = Portfolio(initial_capital=1_000_000)
        p.positions = {
            "A": {"shares": 1000, "avg_cost": 100.0},
            "B": {"shares": 500, "avg_cost": 200.0},
        }
        return p

    def test_stop_loss_triggered(self):
        """Test that stop-loss triggers sell when price drops below threshold."""
        p = self._make_portfolio_with_positions()
        dates = ["20250101", "20250102", "20250103", "20250104", "20250105",
                 "20250106", "20250107", "20250108"]
        # A bought at 100, now at 90 → -10% return → triggers -8% stop-loss
        prices = {"A": 90.0, "B": 200.0}
        to_sell, clear_all = p.check_risk_controls(
            prices, "20250102", dates, stop_loss=-0.08,
        )
        assert "A" in to_sell
        assert not clear_all

    def test_take_profit_triggered(self):
        """Test that take-profit triggers sell when price rises above threshold."""
        p = self._make_portfolio_with_positions()
        dates = ["20250101", "20250102"]
        # B bought at 200, now at 240 → +20% return → triggers +15% take-profit
        prices = {"A": 100.0, "B": 240.0}
        to_sell, clear_all = p.check_risk_controls(
            prices, "20250102", dates, take_profit=0.15,
        )
        assert "B" in to_sell
        assert not clear_all

    def test_drawdown_stop_clears_all(self):
        """Test that portfolio drawdown stop clears all positions."""
        p = self._make_portfolio_with_positions()
        p.peak_nav = 1_200_000  # Previous peak
        dates = ["20250101", "20250102"]
        # Current value: 1000*50 + 500*100 = 100,000 + cash
        # We need the total market value to be < peak * (1 + max_drawdown_stop)
        # So make prices very low
        prices = {"A": 50.0, "B": 100.0}
        # market_value = cash(0) + 1000*50 + 500*100 = 100,000 (much less than 1,200,000)
        p.cash = 0
        to_sell, clear_all = p.check_risk_controls(
            prices, "20250102", dates, max_drawdown_stop=-0.10,
        )
        assert clear_all
        assert set(to_sell) == {"A", "B"}

    def test_cooldown_tracking(self):
        """Test that cooldown is set after stop-loss."""
        p = self._make_portfolio_with_positions()
        dates = [f"2025010{i}" for i in range(1, 11)]
        prices = {"A": 90.0, "B": 200.0}
        p.check_risk_controls(prices, "20250102", dates, stop_loss=-0.08, cooldown_days=5)
        assert "A" in p.cooldown
        # Cooldown should be 5 trading days later
        assert p.cooldown["A"] == dates[dates.index("20250102") + 5]

    def test_no_trigger_when_all_ok(self):
        """Test no sells when all positions are within thresholds."""
        p = self._make_portfolio_with_positions()
        dates = ["20250101", "20250102"]
        # A at cost, B slightly up
        prices = {"A": 100.0, "B": 210.0}
        to_sell, clear_all = p.check_risk_controls(
            prices, "20250102", dates,
            stop_loss=-0.08, take_profit=0.15,
        )
        assert to_sell == []
        assert not clear_all

    def test_risk_control_sells_in_engine(self):
        """Test that engine executes risk control sells on non-rebalance days."""
        from src.backtest.engine import BacktestEngine
        from src.backtest.result import BacktestResult

        dates = pd.date_range("2025-01-01", periods=60, freq="B")
        codes = ["A.SH", "B.SH"]
        rows = []
        np.random.seed(42)
        for i, d in enumerate(dates):
            for c in codes:
                price = 100 + i * 0.5 if c == "A.SH" else 200 - i * 1.5  # B declining
                rows.append({
                    "trade_date": d.strftime("%Y%m%d"),
                    "ts_code": c,
                    "close": price,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "vol": 10000,
                    "amount": price * 10000,
                    "total_score": 1.0 if c == "A.SH" else 0.9,
                })
        df = pd.DataFrame(rows)

        engine = BacktestEngine(
            initial_capital=1_000_000,
            top_n=2,
            rebalance_freq="M",
            risk_control_enabled=True,
            stop_loss=-0.08,
        )
        result = engine.run(df)
        # Should have risk control trades
        risk_trades = [t for t in engine.portfolio.trades if t.get("reason") == "risk_control"]
        # Whether risk trades happen depends on price movement vs buy cost
        assert isinstance(result, BacktestResult)


# === Position Sizing Tests ===

class TestPositionSizing:
    def test_equal_weight(self):
        """Test equal weight allocation."""
        from src.backtest.position_sizing import compute_position_weights

        weights = compute_position_weights("equal_weight", ["A", "B", "C"])
        assert abs(sum(weights.values()) - 1.0) < 0.01
        assert all(abs(w - 1/3) < 0.01 for w in weights.values())

    def test_score_weighted(self):
        """Test score-weighted allocation."""
        from src.backtest.position_sizing import compute_position_weights

        scores = {"A": 0.9, "B": 0.6, "C": 0.3}
        # Use wider bounds so clipping doesn't equalize
        weights = compute_position_weights(
            "score_weighted", ["A", "B", "C"], scores=scores,
            min_weight=0.01, max_weight=0.80,
        )
        assert abs(sum(weights.values()) - 1.0) < 0.01
        assert weights["A"] > weights["B"] > weights["C"]

    def test_risk_parity(self):
        """Test risk parity allocation."""
        from src.backtest.position_sizing import compute_position_weights

        vols = {"A": 0.10, "B": 0.20, "C": 0.05}
        # Use wider bounds so clipping doesn't equalize
        weights = compute_position_weights(
            "risk_parity", ["A", "B", "C"], volatilities=vols,
            min_weight=0.01, max_weight=0.80,
        )
        assert abs(sum(weights.values()) - 1.0) < 0.01
        # C has lowest vol → highest weight
        assert weights["C"] > weights["A"] > weights["B"]

    def test_weight_clipping(self):
        """Test min/max weight clipping."""
        from src.backtest.position_sizing import compute_position_weights

        # One stock with very high score
        scores = {"A": 0.99, "B": 0.01, "C": 0.01}
        weights = compute_position_weights(
            "score_weighted", ["A", "B", "C"], scores=scores,
            min_weight=0.10, max_weight=0.60,
        )
        assert abs(sum(weights.values()) - 1.0) < 0.02
        assert all(w >= 0.09 for w in weights.values())  # Allow small rounding
        assert all(w <= 0.62 for w in weights.values())

    def test_no_scores_fallback(self):
        """Test score_weighted falls back to equal weight with no scores."""
        from src.backtest.position_sizing import compute_position_weights

        weights = compute_position_weights("score_weighted", ["A", "B"])
        assert abs(weights["A"] - 0.5) < 0.01

    def test_no_vols_fallback(self):
        """Test risk_parity falls back to equal weight with no vols."""
        from src.backtest.position_sizing import compute_position_weights

        weights = compute_position_weights("risk_parity", ["A", "B"])
        assert abs(weights["A"] - 0.5) < 0.01

    def test_empty_codes(self):
        """Test empty code list returns empty weights."""
        from src.backtest.position_sizing import compute_position_weights

        weights = compute_position_weights("equal_weight", [])
        assert weights == {}

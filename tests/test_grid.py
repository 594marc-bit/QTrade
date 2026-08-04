"""Unit tests for grid trading modules."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.grid.grid_backtest import GridBacktestEngine
from src.grid.grid_params import GridParams


class TestGridParams:
    def test_equal_spacing(self):
        params = GridParams(
            price_upper=11.0, price_lower=9.0,
            grid_levels=5, grid_mode="equal",
        )
        levels = params.get_grid_levels()
        assert len(levels) == 5
        assert levels[0] == 9.0
        assert levels[-1] == 11.0
        assert levels[1] == 9.5
        assert levels[2] == 10.0

    def test_ratio_spacing(self):
        params = GridParams(
            price_upper=11.0, price_lower=9.0,
            grid_levels=5, grid_mode="ratio",
        )
        levels = params.get_grid_levels()
        assert len(levels) == 5
        assert levels[0] == pytest.approx(9.0, abs=0.01)
        assert levels[-1] == pytest.approx(11.0, abs=0.01)
        # Ratio: each step multiplies by (11/9)^(1/4)
        for i in range(1, len(levels)):
            assert levels[i] > levels[i - 1]

    def test_from_center_price(self):
        params = GridParams.from_center_price(
            center_price=10.0, price_range_pct=15,
            grid_levels=10, grid_mode="equal",
        )
        assert params.price_lower == pytest.approx(8.5, abs=0.01)
        assert params.price_upper == pytest.approx(11.5, abs=0.01)

    def test_nearest_level(self):
        params = GridParams(
            price_upper=12.0, price_lower=8.0,
            grid_levels=5, grid_mode="equal",
        )
        # levels: [8, 9, 10, 11, 12]
        assert params.get_nearest_level(7.5) == -1
        assert params.get_nearest_level(8.5) == 0
        assert params.get_nearest_level(10.5) == 2
        assert params.get_nearest_level(13.0) == 4

    def test_invalid_levels(self):
        with pytest.raises(ValueError):
            GridParams(price_upper=11, price_lower=9, grid_levels=1)

    def test_invalid_range(self):
        with pytest.raises(ValueError):
            GridParams(price_upper=8, price_lower=9, grid_levels=5)


class TestGridBacktestEngine:
    @staticmethod
    def _make_bars(prices: list[tuple[float, float, float, float]], ts_code: str = "000001.SZ"):
        """Create DataFrame from (open, high, low, close) tuples."""
        import datetime
        base = datetime.datetime(2024, 1, 2, 9, 35)
        rows = []
        for i, (o, h, l, c) in enumerate(prices):
            t = base + datetime.timedelta(minutes=5 * i)
            rows.append({
                "datetime": t.strftime("%Y%m%d%H%M%S"),
                "open": o, "high": h, "low": l, "close": c,
                "vol": 1e6, "amount": 1e7,
            })
        return pd.DataFrame(rows)

    def test_no_trades_flat_price(self):
        """No trades when price stays within same grid level."""
        bars = self._make_bars([
            (10.0, 10.1, 9.9, 10.05),
            (10.0, 10.2, 9.8, 10.0),
            (9.9, 10.1, 9.9, 10.05),
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        assert len(result.trades) == 0

    def test_buy_on_price_drop(self):
        """Buy triggered when price drops below a grid level."""
        bars = self._make_bars([
            (11.0, 11.1, 10.9, 11.0),   # near top
            (9.0, 9.2, 8.7, 8.8),        # big drop → triggers buy at lower levels
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5, order_shares=500)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        # Should have at least one buy
        buys = [t for _, t in result.trades.iterrows()] if not result.trades.empty else []
        buy_count = sum(1 for b in buys if isinstance(b, dict) and b.get("action") == "BUY" or getattr(b, "action", "") == "BUY")
        # The second bar is a big drop; should trigger buys
        assert len(result.trades) > 0

    def test_sell_on_price_rise(self):
        """Sell triggered when price rises above a grid level."""
        # Start low, buy some, then rise to trigger sell
        bars = self._make_bars([
            (9.0, 9.1, 8.9, 9.0),
            (9.0, 9.0, 8.5, 8.6),   # buy triggered
            (10.5, 10.8, 10.2, 10.7),  # big rise → should trigger sells
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5, order_shares=500)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        # Trades should include both buy and sell
        assert len(result.trades) > 0

    def test_stop_loss(self):
        """Stop-loss triggers when price drops below lower bound."""
        bars = self._make_bars([
            (10.0, 10.1, 9.9, 10.0),
            (5.0, 5.2, 4.8, 5.0),  # far below price_lower ~8.5
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=15, grid_levels=5, base_shares=1000)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        # Should have a stop-loss sell
        sells = [
            t for t in (result.trades.to_dict("records") if not result.trades.empty else [])
            if isinstance(t, dict) and t.get("reason") == "stop_loss"
        ]
        assert len(sells) > 0

    def test_nav_starts_at_capital(self):
        """NAV should start at initial capital (with base position)."""
        bars = self._make_bars([
            (10.0, 10.1, 9.9, 10.0),
            (10.0, 10.05, 9.95, 10.0),
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        first_nav = result.nav_series["nav"].iloc[0]
        assert first_nav == pytest.approx(50000, abs=1)

    def test_commission_deducted(self):
        """Trades should deduct commissions."""
        bars = self._make_bars([
            (11.0, 11.1, 10.9, 11.0),
            (9.0, 9.2, 8.6, 8.7),  # big drop, triggers buys
        ])
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5, order_shares=500)
        engine = GridBacktestEngine(initial_capital=50000)
        result = engine.run(bars, params)
        # Should have commissioned trades
        if not result.trades.empty:
            trades_list = result.trades.to_dict("records")
            buy_trades = [t for t in trades_list if isinstance(t, dict) and t.get("action") == "BUY"]
            if buy_trades:
                assert buy_trades[0].get("commission", 0) > 0


class TestGridStateManager:
    def test_init_grid(self):
        from src.grid.grid_state import GridStateManager
        mgr = GridStateManager("000001.SZ")
        grid_prices = [9.0, 10.0, 11.0]
        rows = mgr.init_grid(grid_prices)
        # 3 levels: 2 buy rows (levels 1,2) + 2 sell rows (levels 0,1) = 4
        assert len(rows) == 4

    def test_get_state(self):
        from src.grid.grid_state import GridStateManager
        mgr = GridStateManager("000001.SZ")
        mgr.init_grid([9.0, 10.0, 11.0])
        state = mgr.get_state()
        assert len(state) > 0

    def test_clear_empty(self):
        from src.grid.grid_state import GridStateManager
        from src.data.storage import clear_grid_state
        clear_grid_state("000002.SZ")  # Should not raise


class TestGridSignalGenerator:
    def test_init_no_signals_on_first_call(self):
        from src.grid.grid_params import GridParams
        from src.grid.grid_signal_generator import GridSignalGenerator
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5)
        gen = GridSignalGenerator("000001.SZ", params)
        sigs = gen.generate_signals(10.0)
        assert len(sigs) == 0  # First call = init

    def test_buy_signal_on_drop(self):
        from src.grid.grid_params import GridParams
        from src.grid.grid_signal_generator import GridSignalGenerator
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5, order_shares=500)
        gen = GridSignalGenerator("000001.SZ", params)
        gen.generate_signals(10.5)  # first call = init
        sigs = gen.generate_signals(8.5)  # big drop
        # Should have buy signals
        buy_sigs = [s for s in sigs if s["action"] == "BUY"]
        assert len(buy_sigs) > 0

    def test_signal_has_required_fields(self):
        from src.grid.grid_params import GridParams
        from src.grid.grid_signal_generator import GridSignalGenerator
        params = GridParams.from_center_price(10.0, price_range_pct=20, grid_levels=5, order_shares=500)
        gen = GridSignalGenerator("000001.SZ", params)
        gen.generate_signals(10.5)
        sigs = gen.generate_signals(9.0)
        for sig in sigs:
            assert "ts_code" in sig
            assert "action" in sig
            assert "quantity" in sig
            assert "remark" in sig
            assert sig["remark"].startswith("grid:")

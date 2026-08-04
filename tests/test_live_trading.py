"""Integration tests for live trading system.

Tests that don't require QMT hardware:
  - test_signal_generator_consistency: SignalGenerator vs backtest engine
  - test_api_endpoints: REST API health, auth, pending signals
  - test_status_transitions: Signal status state machine validation

Tests requiring QMT/miniQMT (manual):
  - test_qmt_e2e: End-to-end with QMT simulator account (run on Windows)
"""

import json
import os
import sys
import sqlite3
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _setup_test_api_key():
    """Temporarily set LIVE_API_KEY for testing."""
    os.environ.setdefault("QTRAE_TEST_API_KEY", "test-api-key-12345")


def _get_test_config():
    """Get test configuration dict matching config.ini [live] defaults."""
    return {
        "api_port": 8999,  # non-default port for testing
        "api_key": "test-api-key-12345",
        "scheme_name": "default",
        "top_n": 10,
        "total_capital": 1_000_000,
    }


# ---------------------------------------------------------------------------
# 7.1 SignalGenerator consistency with backtest
# ---------------------------------------------------------------------------

class TestSignalGeneratorConsistency:
    """Verify SignalGenerator uses the same factor code as backtest."""

    def test_same_factor_modules_imported(self):
        """All factors imported by signal_generator match backtest imports."""
        from src.live.signal_generator import SignalGenerator
        from src.factors.base import get_registered_factors

        gen = SignalGenerator(scheme_name="default")
        factors = get_registered_factors()

        # All scheme factors should be registered
        for name in gen._enabled_factors:
            assert name in factors, f"Factor '{name}' in scheme but not registered"

        # All registered factors should have calculate() method
        for name, cls in factors.items():
            assert hasattr(cls(), "calculate"), f"Factor '{name}' missing calculate()"

    def test_scheme_weights_loaded(self):
        """Scheme weights are correctly loaded from schemes.yaml."""
        from src.live.signal_generator import SignalGenerator

        gen = SignalGenerator(scheme_name="default")
        assert gen._weights, "Weights should not be empty"
        assert "intraday_range_score" in gen._weights
        assert "pb_rank_score" in gen._weights

    def test_quantity_lot_rounding(self):
        """Quantities are multiples of 100 (A-share lot size)."""
        from src.live.signal_generator import SignalGenerator

        gen = SignalGenerator(scheme_name="default", top_n=10, total_capital=1_000_000)

        # Mock _df with some close prices
        gen._df = pd.DataFrame({
            "trade_date": ["20260101", "20260101"],
            "ts_code": ["000001.SZ", "000002.SZ"],
            "close": [15.0, 25.0],
        })

        # Create a mock top_picks with same structure as select_top_n output
        top_picks = pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20260101", "20260101"],
            "total_score": [1.5, 1.2],
        })

        qty1 = gen._calc_quantity(top_picks, "000001.SZ")
        qty2 = gen._calc_quantity(top_picks, "000002.SZ")

        # target = 100000 per stock
        # stock 1: 100000 / 15 = 6666.6 → 6600 shares (66 lots)
        assert qty1 == 6600, f"Expected 6600, got {qty1}"
        # stock 2: 100000 / 25 = 4000 → 4000 shares (40 lots)
        assert qty2 == 4000, f"Expected 4000, got {qty2}"

        # Both must be multiples of 100
        assert qty1 % 100 == 0
        assert qty2 % 100 == 0

    def test_invalid_scheme_raises(self):
        """Invalid scheme name raises ValueError."""
        from src.live.signal_generator import SignalGenerator

        with pytest.raises(ValueError):
            SignalGenerator(scheme_name="nonexistent_scheme_xyz")


# ---------------------------------------------------------------------------
# 7.2 API endpoint tests (requires running server in test fixture)
# ---------------------------------------------------------------------------

class TestAPIEndpoints:
    """Test REST API endpoints using FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        """Create a FastAPI test client with test config."""
        # Override config for testing. NOTE: server.py imports LIVE_API_KEY
        # by value (`from src.config import LIVE_API_KEY`), so mutating
        # cfg.LIVE_API_KEY alone has no effect once server is imported —
        # patch the server module's binding directly too.
        import src.config as cfg
        import src.live.server as srv
        cfg.LIVE_API_KEY = "test-api-key-12345"
        srv.LIVE_API_KEY = "test-api-key-12345"
        cfg.LIVE_API_PORT = 8999

        from src.live.server import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    @pytest.fixture
    def auth_headers(self):
        return {"Authorization": "Bearer test-api-key-12345"}

    def test_health_no_auth_required(self, client):
        """Health endpoint should work without auth."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "pending_signals" in data

    def test_pending_requires_auth(self, client):
        """Pending endpoint requires Bearer token."""
        resp = client.get("/api/trade/pending")
        assert resp.status_code in (401, 403)  # 401 or 403 for missing auth

    def test_pending_with_valid_auth(self, client, auth_headers):
        """Pending endpoint works with valid auth."""
        resp = client.get("/api/trade/pending", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_pending_with_invalid_auth(self, client):
        """Pending endpoint rejects invalid auth."""
        resp = client.get(
            "/api/trade/pending",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_status_update_invalid_id(self, client, auth_headers):
        """Status update on non-existent signal returns 400."""
        resp = client.put(
            "/api/trade/99999/status",
            json={"status": "sent"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_portfolio_sync(self, client, auth_headers):
        """Portfolio sync endpoint accepts holdings."""
        holdings = [
            {"ts_code": "600036.SH", "shares": 1000, "avg_cost": 12.34},
        ]
        resp = client.post(
            "/api/portfolio/sync",
            json=holdings,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["positions"] == 1


# ---------------------------------------------------------------------------
# 7.3 Signal status state machine
# ---------------------------------------------------------------------------

class TestSignalStatusTransitions:
    """Verify status transition validation."""

    def test_valid_transitions(self):
        """All valid transitions should be defined."""
        from src.data.storage import VALID_STATUS_TRANSITIONS

        assert "sent" in VALID_STATUS_TRANSITIONS["pending"]
        assert "cancelled" in VALID_STATUS_TRANSITIONS["pending"]
        assert "filled" in VALID_STATUS_TRANSITIONS["sent"]
        assert "partial" in VALID_STATUS_TRANSITIONS["sent"]
        assert "rejected" in VALID_STATUS_TRANSITIONS["sent"]

    def test_terminal_states(self):
        """Terminal states should have no valid transitions."""
        from src.data.storage import VALID_STATUS_TRANSITIONS

        for terminal in ("filled", "rejected", "cancelled"):
            assert VALID_STATUS_TRANSITIONS[terminal] == [], \
                f"Terminal state '{terminal}' should have no transitions"

    def test_pending_to_filled_invalid(self):
        """Direct pending→filled is invalid (must go through sent)."""
        from src.data.storage import VALID_STATUS_TRANSITIONS
        assert "filled" not in VALID_STATUS_TRANSITIONS["pending"]


# ---------------------------------------------------------------------------
# 7.4 Manual: QMT end-to-end test (runs on Windows)
# ---------------------------------------------------------------------------

# This test requires QMT simulator and is run manually on Windows:
#
# 1. Start Mac API server:
#    uvicorn src.live.server:app --host 0.0.0.0 --port 8000
#
# 2. On Windows, configure config_windows.json with Mac's IP
#
# 3. For miniQMT: python qmt_executor.py
#    For 大QMT: Load qtrade_bridge.py into QMT strategy manager
#
# 4. On Mac, generate a test signal:
#    from src.live.signal_generator import SignalGenerator
#    gen = SignalGenerator(scheme_name="default", top_n=10)
#    signals = gen.generate_signals(rebalance_date="20260701")
#
# 5. Verify:
#    - Signal appears in QMT order log
#    - Status updated to "sent" on Mac API
#    - Check QMT simulator account for filled orders

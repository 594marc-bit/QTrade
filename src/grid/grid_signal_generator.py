"""Grid trading signal generator for live/paper trading.

Generates BUY/SELL signals when the close price crosses grid levels,
avoiding duplicates for levels that already have pending signals.
"""

from datetime import datetime as dt
from typing import Any

import pandas as pd

from src.data.storage import get_connection
from src.grid.grid_params import GridParams
from src.grid.grid_state import GridStateManager


class GridSignalGenerator:
    """Generate grid trading signals for live execution.

    Compares latest close prices against grid levels and generates
    BUY/SELL signals when price crosses a level. Signals are written
    to the trade_signals table with tag='grid'.
    """

    def __init__(
        self,
        ts_code: str,
        params: GridParams,
        total_capital: float = 1_000_000,
    ):
        self.ts_code = ts_code
        self.params = params
        self.total_capital = total_capital
        self.state = GridStateManager(ts_code)
        self._prev_close: float | None = None

    def init_state(self) -> None:
        """Initialize grid state on first run."""
        self.state.init_grid(self.params.get_grid_levels())

    def generate_signals(
        self,
        latest_close: float,
        latest_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate grid signals based on latest close price.

        Args:
            latest_close: Latest closing price.
            latest_date: Trade date for the signal.

        Returns:
            List of signal dicts with keys:
            ts_code, action, quantity, price_type, limit_price,
            scheme_name, rebalance_date, remark.
        """
        if latest_date is None:
            latest_date = dt.now().strftime("%Y%m%d")

        grid_prices = self.params.get_grid_levels()
        signals = []

        # Initialize on first call
        if self._prev_close is None:
            self._prev_close = latest_close
            self.init_state()
            return signals

        prev_level = self.params.get_nearest_level(self._prev_close)
        curr_level = self.params.get_nearest_level(latest_close)

        # Price moved up: check for sell signals at crossed levels
        if curr_level > prev_level:
            for level in range(prev_level + 1, curr_level + 1):
                gp = grid_prices[level]
                pending = self.state.get_pending_levels()
                already_pending = any(
                    p.get("grid_level") == level and p.get("direction") == "SELL"
                    for p in pending
                )
                if already_pending:
                    continue
                qty = self.params.order_shares
                signals.append({
                    "ts_code": self.ts_code,
                    "action": "SELL",
                    "quantity": qty,
                    "price_type": "LIMIT",
                    "limit_price": gp,
                    "scheme_name": f"grid_{self.ts_code}",
                    "rebalance_date": latest_date,
                    "remark": f"grid:{self.ts_code}:{level}:SELL",
                })
                # Mark level as pending sell
                self.state.update_after_fill(level, "SELL", 0, status="sent")

        # Price moved down: check for buy signals at crossed levels
        elif curr_level < prev_level:
            for level in range(prev_level, curr_level, -1):
                gp = grid_prices[level]
                pending = self.state.get_pending_levels()
                already_pending = any(
                    p.get("grid_level") == level and p.get("direction") == "BUY"
                    for p in pending
                )
                if already_pending:
                    continue
                qty = self.params.order_shares
                signals.append({
                    "ts_code": self.ts_code,
                    "action": "BUY",
                    "quantity": qty,
                    "price_type": "LIMIT",
                    "limit_price": gp,
                    "scheme_name": f"grid_{self.ts_code}",
                    "rebalance_date": latest_date,
                    "remark": f"grid:{self.ts_code}:{level}:BUY",
                })
                self.state.update_after_fill(level, "BUY", 0, status="sent")

        self._prev_close = latest_close
        return signals

    def save_signals(self, signals: list[dict[str, Any]]) -> int:
        """Write signals to trade_signals table.

        Returns:
            Number of signals saved.
        """
        if not signals:
            return 0
        conn = get_connection()
        cursor = conn.cursor()
        saved = 0
        for sig in signals:
            cursor.execute(
                """INSERT INTO trade_signals
                   (ts_code, action, quantity, price_type, limit_price,
                    scheme_name, rebalance_date, status, remark, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, datetime('now', 'localtime'))""",
                (
                    sig["ts_code"],
                    sig["action"],
                    sig["quantity"],
                    sig.get("price_type", "LIMIT"),
                    sig.get("limit_price"),
                    sig.get("scheme_name", f"grid_{sig['ts_code']}"),
                    sig["rebalance_date"],
                    sig.get("remark", ""),
                ),
            )
            saved += 1
        conn.commit()
        conn.close()
        return saved

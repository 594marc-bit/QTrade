"""Grid trading state manager for live/paper trading.

Tracks per-stock grid positions, pending orders per grid level, and
execution status via the grid_state SQLite table.
"""

from src.data.storage import (
    clear_grid_state,
    load_grid_state,
    save_grid_state,
)


class GridStateManager:
    """Manages grid trading state for live execution."""

    def __init__(self, ts_code: str):
        self.ts_code = ts_code

    def init_grid(self, grid_prices: list[float]) -> list[dict]:
        """Initialize grid state for a stock.

        Creates two rows per grid level: one for BUY direction (waiting
        to buy when price drops to this level) and one for SELL direction
        (waiting to sell when price rises to this level). The topmost level
        gets no buy row, the bottommost gets no sell row.

        Args:
            grid_prices: Sorted grid price levels (low to high).

        Returns:
            List of initialized grid state rows.
        """
        clear_grid_state(self.ts_code)
        rows = []
        n = len(grid_prices)
        for i, gp in enumerate(grid_prices):
            if i > 0:  # Can buy at this level (price dropping)
                rows.append({
                    "ts_code": self.ts_code,
                    "grid_level": i,
                    "grid_price": gp,
                    "direction": "BUY",
                    "status": "idle",
                })
            if i < n - 1:  # Can sell at this level (price rising)
                rows.append({
                    "ts_code": self.ts_code,
                    "grid_level": i,
                    "grid_price": gp,
                    "direction": "SELL",
                    "status": "idle",
                })
        save_grid_state(rows)
        return rows

    def get_state(self) -> list[dict]:
        """Get current grid state for this stock."""
        df = load_grid_state(self.ts_code)
        if df.empty:
            return []
        return df.to_dict("records")

    def update_after_fill(
        self,
        grid_level: int,
        direction: str,
        signal_id: int,
        filled_shares: int = 0,
        filled_price: float = 0.0,
        status: str = "filled",
    ):
        """Update grid state after a signal is generated or filled.

        When a BUY order fills at a grid level, mark the SELL side as
        ready (has shares to sell). When a SELL order fills, mark the
        BUY side back to idle (ready to buy again).

        Set status='sent' to prevent duplicate signals before execution
        confirmation arrives.

        Args:
            grid_level: Grid level index.
            direction: 'BUY' or 'SELL'.
            signal_id: Signal ID (0 for pre-fill marking).
            filled_shares: Shares filled.
            filled_price: Fill price.
            status: State status — 'filled' (default) or 'sent' (pre-confirmation).
        """
        save_grid_state([{
            "ts_code": self.ts_code,
            "grid_level": grid_level,
            "grid_price": 0,  # not updated, use existing
            "direction": direction,
            "status": status,
            "signal_id": signal_id,
            "filled_shares": filled_shares,
            "filled_price": filled_price,
        }])

    def get_pending_levels(self) -> list[dict]:
        """Return grid levels with pending signals (status=sent)."""
        rows = self.get_state()
        return [r for r in rows if r.get("status") in ("pending", "sent")]

    def get_ready_to_buy(self) -> list[dict]:
        """Return grid levels where a buy order could be placed."""
        rows = self.get_state()
        return [
            r for r in rows
            if r.get("direction") == "BUY" and r.get("status") == "idle"
        ]

    def get_ready_to_sell(self) -> list[dict]:
        """Return grid levels where a sell order could be placed."""
        rows = self.get_state()
        return [
            r for r in rows
            if r.get("direction") == "SELL" and r.get("status") == "filled"
        ]

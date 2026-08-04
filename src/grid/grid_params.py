"""Grid trading parameters and grid level calculation."""

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class GridParams:
    """Grid trading configuration for a single stock.

    Attributes:
        price_upper: Upper price bound of the grid.
        price_lower: Lower price bound of the grid.
        grid_levels: Number of grid levels (>= 2).
        grid_mode: Spacing mode — 'equal' or 'ratio'.
        order_shares: Order amount per grid level (yuan).
        base_shares: Initial base position shares (0 = bare grid).
        buy_commission: Buy commission rate.
        sell_commission: Sell commission rate.
        stamp_tax: Stamp tax rate (sell only).
    """

    price_upper: float
    price_lower: float
    grid_levels: int = 10
    grid_mode: str = "ratio"
    order_shares: float = 1000
    base_shares: int = 0
    buy_commission: float = 0.0003
    sell_commission: float = 0.0003
    stamp_tax: float = 0.0005
    grid_prices: list[float] = field(default_factory=list)

    def __post_init__(self):
        if self.grid_levels < 2:
            raise ValueError("grid_levels must be >= 2")
        if self.price_upper <= self.price_lower:
            raise ValueError("price_upper must be > price_lower")
        if not self.grid_prices:
            self.grid_prices = self._compute_grid_prices()

    def _compute_grid_prices(self) -> list[float]:
        """Compute grid price levels from low to high."""
        if self.grid_mode == "equal":
            step = (self.price_upper - self.price_lower) / (self.grid_levels - 1)
            return [
                round(self.price_lower + i * step, 3)
                for i in range(self.grid_levels)
            ]
        else:  # ratio
            ratio = math.pow(
                self.price_upper / self.price_lower,
                1.0 / (self.grid_levels - 1),
            )
            return [
                round(self.price_lower * math.pow(ratio, i), 3)
                for i in range(self.grid_levels)
            ]

    def get_grid_levels(self) -> list[float]:
        """Return grid price levels sorted from low to high."""
        return self.grid_prices

    def get_nearest_level(self, price: float) -> int:
        """Return index of nearest grid level below the given price.

        Returns -1 if price is below all levels.
        """
        for i in range(len(self.grid_prices) - 1, -1, -1):
            if price >= self.grid_prices[i]:
                return i
        return -1

    @classmethod
    def from_center_price(
        cls,
        center_price: float,
        price_range_pct: float = 15,
        grid_levels: int = 10,
        grid_mode: str = "ratio",
        order_shares: float = 1000,
        base_shares: int = 0,
        buy_commission: float = 0.0003,
        sell_commission: float = 0.0003,
        stamp_tax: float = 0.0005,
    ) -> "GridParams":
        """Create GridParams from a center price and range percentage."""
        half_range = price_range_pct / 100.0
        return cls(
            price_upper=round(center_price * (1 + half_range), 3),
            price_lower=round(center_price * (1 - half_range), 3),
            grid_levels=grid_levels,
            grid_mode=grid_mode,
            order_shares=order_shares,
            base_shares=base_shares,
            buy_commission=buy_commission,
            sell_commission=sell_commission,
            stamp_tax=stamp_tax,
        )

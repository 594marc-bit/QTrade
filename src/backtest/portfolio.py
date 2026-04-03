"""Portfolio management for backtesting: positions, cash, rebalancing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _date_offset(date: str, dates_list: list[str], offset: int) -> str:
    """Get the date that is `offset` trading days after `date`.

    Args:
        date: Current date string YYYYMMDD.
        dates_list: Sorted list of all trade dates.
        offset: Number of trading days to offset.

    Returns:
        Date string offset days later, or the last date if beyond range.
    """
    try:
        idx = dates_list.index(date)
        target_idx = min(idx + offset, len(dates_list) - 1)
        return dates_list[target_idx]
    except ValueError:
        return date


@dataclass
class Portfolio:
    """Manages portfolio state during backtest.

    Attributes:
        cash: Available cash.
        positions: Dict mapping ts_code -> {shares, avg_cost}.
        initial_capital: Starting capital.
        buy_commission: Buy commission rate (default 0.0003).
        sell_commission: Sell commission rate (default 0.0003).
        stamp_tax: Stamp tax rate on sells (default 0.001).
    """

    initial_capital: float = 1_000_000.0
    cash: float = field(default=None)
    positions: dict = field(default_factory=dict)
    buy_commission: float = 0.0003
    sell_commission: float = 0.0003
    stamp_tax: float = 0.001

    # Trade log
    trades: list = field(default_factory=list)

    # Risk control state
    cooldown: dict = field(default_factory=dict)  # ts_code -> cooldown_until_date
    peak_nav: float = 0.0

    def __post_init__(self):
        if self.cash is None:
            self.cash = self.initial_capital
        self.peak_nav = self.initial_capital

    @property
    def total_assets(self) -> float:
        """Total portfolio value at cost basis (excludes market value)."""
        return self.cash + sum(
            p["shares"] * p["avg_cost"] for p in self.positions.values()
        )

    def market_value(self, prices: dict[str, float]) -> float:
        """Total portfolio value at current market prices.

        Args:
            prices: Dict mapping ts_code -> current price.
        """
        pos_value = sum(
            p["shares"] * prices.get(code, p["avg_cost"])
            for code, p in self.positions.items()
        )
        return self.cash + pos_value

    def rebalance(
        self,
        target_codes: list[str],
        prices: dict[str, float],
        date: str,
        suspended_codes: set[str] | None = None,
        target_weights: dict[str, float] | None = None,
    ):
        """Rebalance to target portfolio with optional custom weights.

        1. Sell positions not in target (skip suspended / on cooldown).
        2. Allocate cash per target_weights (or equal-weight if None).
        3. Buy target stocks.

        Args:
            target_codes: List of stock codes to hold.
            prices: Dict mapping ts_code -> current price.
            date: Trade date string.
            suspended_codes: Set of suspended stock codes (cannot trade).
            target_weights: Dict mapping ts_code -> target weight (0-1). If None, equal-weight.
        """
        if suspended_codes is None:
            suspended_codes = set()

        # Filter out stocks on cooldown
        buyable_codes = [
            c for c in target_codes
            if c not in self.cooldown or self.cooldown[c] <= date
        ]

        # Phase 1: Sell positions not in target
        sell_codes = [c for c in self.positions if c not in target_codes]
        for code in sell_codes:
            if code in suspended_codes:
                continue
            self._sell_position(code, prices.get(code), date)

        # Phase 2: Determine buyable targets
        buyable = [c for c in buyable_codes if c not in suspended_codes and c in prices]
        if not buyable:
            return

        # Include existing positions that are still in target
        all_holdings = set(self.positions.keys()) | set(buyable)

        # Total portfolio value at current prices
        total_value = self.market_value(prices)

        # Compute target weight per stock
        weights = self._resolve_weights(all_holdings, target_weights)

        # Phase 3: Sell overweight positions (partial sell)
        for code in list(self.positions.keys()):
            if code in suspended_codes:
                continue
            price = prices.get(code)
            if price is None or price <= 0:
                continue
            current_value = self.positions[code]["shares"] * price
            target_value = total_value * weights.get(code, 0)
            if target_value > 0 and current_value > target_value * 1.01:
                excess = current_value - target_value
                sell_shares = int(excess / price / 100) * 100
                if sell_shares >= 100:
                    self._sell_partial(code, price, date, sell_shares)

        # Phase 4: Buy underweight positions
        for code in buyable:
            price = prices[code]
            if price <= 0:
                continue

            current_value = self.positions.get(code, {}).get("shares", 0) * price
            target_value = total_value * weights.get(code, 1.0 / len(all_holdings))
            deficit = target_value - current_value

            if deficit > price * 100:
                max_spend = min(self.cash, deficit / (1 + self.buy_commission))
                buy_shares = int(max_spend / price / 100) * 100
                if buy_shares >= 100:
                    self._buy_position(code, price, date, buy_shares)

    def check_risk_controls(
        self,
        current_prices: dict[str, float],
        date: str,
        dates_list: list[str],
        stop_loss: float = -0.08,
        take_profit: float = 0.15,
        max_drawdown_stop: float = -0.10,
        cooldown_days: int = 5,
    ) -> tuple[list[str], bool]:
        """Check risk controls and return stocks to sell and whether to clear all.

        Args:
            current_prices: Dict mapping ts_code -> current price.
            date: Current trade date string.
            dates_list: Sorted list of all trade dates (for cooldown calculation).
            stop_loss: Stop-loss threshold (e.g. -0.08 = -8%).
            take_profit: Take-profit threshold (e.g. 0.15 = 15%).
            max_drawdown_stop: Portfolio drawdown stop threshold (e.g. -0.10).
            cooldown_days: Days to wait after stop-loss before re-buying.

        Returns:
            Tuple of (list of ts_codes to sell, bool whether to clear entire portfolio).
        """
        codes_to_sell = []
        clear_all = False

        if not self.positions:
            return codes_to_sell, clear_all

        # Check portfolio drawdown
        total_value = self.market_value(current_prices)
        if total_value > self.peak_nav:
            self.peak_nav = total_value

        if self.peak_nav > 0:
            drawdown = (total_value - self.peak_nav) / self.peak_nav
            if drawdown <= max_drawdown_stop:
                clear_all = True
                logger.info("Portfolio drawdown %.1f%% exceeded threshold %.1f%%. Clearing all positions.",
                             drawdown * 100, max_drawdown_stop * 100)
                return list(self.positions.keys()), True

        # Check individual stock stop-loss / take-profit
        for code, pos in list(self.positions.items()):
            price = current_prices.get(code)
            if price is None or price <= 0:
                continue
            ret = (price - pos["avg_cost"]) / pos["avg_cost"]

            if ret <= stop_loss:
                codes_to_sell.append(code)
                # Set cooldown
                cooldown_until = _date_offset(date, dates_list, cooldown_days)
                self.cooldown[code] = cooldown_until
                logger.debug("Stop-loss triggered for %s: return=%.1f%%", code, ret * 100)
            elif ret >= take_profit:
                codes_to_sell.append(code)
                logger.debug("Take-profit triggered for %s: return=%.1f%%", code, ret * 100)

        return codes_to_sell, clear_all

    def sell_stocks(self, codes: list[str], prices: dict[str, float], date: str):
        """Sell specific stocks from portfolio.

        Args:
            codes: List of ts_codes to sell.
            prices: Dict mapping ts_code -> current price.
            date: Trade date string.
        """
        for code in codes:
            if code in self.positions:
                self._sell_position(code, prices.get(code), date)

    def _sell_position(self, code: str, price: float | None, date: str):
        """Sell entire position in a stock."""
        if code not in self.positions:
            return
        pos = self.positions[code]
        if price is None or price <= 0:
            return

        sell_amount = pos["shares"] * price
        cost = sell_amount * (self.sell_commission + self.stamp_tax)
        self.cash += sell_amount - cost
        self.trades.append({
            "date": date,
            "ts_code": code,
            "action": "sell",
            "shares": pos["shares"],
            "price": price,
            "amount": sell_amount,
            "cost": cost,
            "reason": "rebalance",
        })
        del self.positions[code]

    def _sell_partial(self, code: str, price: float, date: str, sell_shares: int):
        """Sell partial position in a stock."""
        if code not in self.positions or sell_shares < 100:
            return

        sell_shares = min(sell_shares, self.positions[code]["shares"])
        sell_amount = sell_shares * price
        cost = sell_amount * (self.sell_commission + self.stamp_tax)
        self.cash += sell_amount - cost
        self.positions[code]["shares"] -= sell_shares
        self.trades.append({
            "date": date,
            "ts_code": code,
            "action": "sell",
            "shares": sell_shares,
            "price": price,
            "amount": sell_amount,
            "cost": cost,
            "reason": "rebalance",
        })

    def _buy_position(self, code: str, price: float, date: str, buy_shares: int):
        """Buy position in a stock."""
        buy_amount = buy_shares * price
        cost = buy_amount * self.buy_commission
        total_cost = buy_amount + cost

        if total_cost > self.cash:
            return

        self.cash -= total_cost
        if code in self.positions:
            old = self.positions[code]
            total_shares = old["shares"] + buy_shares
            avg_cost = (old["shares"] * old["avg_cost"] + buy_amount) / total_shares
            self.positions[code] = {"shares": total_shares, "avg_cost": avg_cost}
        else:
            self.positions[code] = {"shares": buy_shares, "avg_cost": price}

        self.trades.append({
            "date": date,
            "ts_code": code,
            "action": "buy",
            "shares": buy_shares,
            "price": price,
            "amount": buy_amount,
            "cost": cost,
            "reason": "rebalance",
        })

    @staticmethod
    def _resolve_weights(
        all_holdings: set[str],
        target_weights: dict[str, float] | None,
    ) -> dict[str, float]:
        """Resolve target weights for holdings. Equal-weight if target_weights is None."""
        if target_weights is not None:
            return {code: target_weights.get(code, 0.0) for code in all_holdings}
        n = len(all_holdings)
        return {code: 1.0 / n for code in all_holdings}

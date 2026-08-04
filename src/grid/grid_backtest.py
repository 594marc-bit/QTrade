"""Minute-level bar-by-bar grid trading backtest engine."""

import numpy as np
import pandas as pd

from src.grid.grid_params import GridParams
from src.grid.grid_result import GridBacktestResult


class GridBacktestEngine:
    """Bar-by-bar grid trading backtest engine.

    Iterates over minute-level K-line bars and simulates grid trading:
    buy when price drops to a grid level, sell when price rises to a level.

    Uses bar OHLC to determine grid level crosses within a single bar.
    When a bar's range spans multiple grid levels, triggers them in order
    from nearest to farthest.
    """

    def __init__(self, initial_capital: float = 1_000_000):
        self.initial_capital = initial_capital

    def run(self, df: pd.DataFrame, params: GridParams) -> GridBacktestResult:
        """Run grid backtest on minute K-line data.

        Args:
            df: DataFrame with columns [datetime, open, high, low, close, vol, amount].
                Must be sorted by datetime ascending.
            params: GridParams for this stock.

        Returns:
            GridBacktestResult with nav_series, trades, metrics, grid_level_stats, attribution.
        """
        required = {"open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Sort by time column: use bar_time (minute) or datetime (daily fallback)
        time_col = "bar_time" if "bar_time" in df.columns else df.columns[0]
        df = df.sort_values(time_col).reset_index(drop=True)
        grid_prices = params.get_grid_levels()
        n_levels = len(grid_prices)

        # --- state ---
        cash = self.initial_capital
        shares = params.base_shares
        # Track filled shares per level (bought at this level, ready to sell)
        level_shares = {i: 0 for i in range(n_levels)}

        nav_records = []
        trade_records = []
        total_commission = 0.0
        total_stamp_tax = 0.0
        initial_price = df.iloc[0]["close"]
        stopped_out = False

        # --- grid level stats accumulator ---
        level_buy_count = {i: 0 for i in range(n_levels)}
        level_sell_count = {i: 0 for i in range(n_levels)}
        level_buy_amount = {i: 0.0 for i in range(n_levels)}
        level_sell_amount = {i: 0.0 for i in range(n_levels)}
        level_profit = {i: 0.0 for i in range(n_levels)}

        for idx in range(len(df)):
            bar = df.iloc[idx]
            bar_time = bar[df.columns[0]]
            o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

            if idx == 0:
                nav_records.append({"time": bar_time, "nav": cash + shares * c})
                prev_c = c
                continue

            if stopped_out:
                nav_records.append({"time": bar_time, "nav": cash + shares * c})
                prev_c = c
                continue

            # --- Determine bar direction for multi-level trigger order ---
            mid_price = (grid_prices[0] + grid_prices[-1]) / 2
            sell_first = o > mid_price  # above midpoint: prioritize sells

            # Find all grid levels crossed by this bar
            triggered_buys = []   # levels where price dropped to trigger buy
            triggered_sells = []  # levels where price rose to trigger sell

            for i, gp in enumerate(grid_prices):
                # SELL: bar high >= gp and previous close < gp (crossed from below)
                if h >= gp and prev_c < gp:
                    triggered_sells.append(i)
                # BUY: bar low <= gp and previous close > gp (crossed from above)
                elif l <= gp and prev_c > gp:
                    triggered_buys.append(i)

            # Sort: nearest to current position first
            if sell_first:
                triggered_sells.sort(reverse=True)  # high to low
                triggered_buys.sort(reverse=True)
            else:
                triggered_buys.sort()    # low to high
                triggered_sells.sort()

            # --- Execute sells first if selling first ---
            if sell_first:
                cash, shares, level_shares = self._execute_sells(
                    triggered_sells, level_shares, grid_prices, cash, shares, params,
                    bar_time, trade_records, level_sell_count, level_sell_amount,
                    level_profit, total_commission, total_stamp_tax,
                )
                cash, shares, level_shares = self._execute_buys(
                    triggered_buys, level_shares, grid_prices, cash, shares, params,
                    bar_time, trade_records, level_buy_count, level_buy_amount,
                )
            else:
                cash, shares, level_shares = self._execute_buys(
                    triggered_buys, level_shares, grid_prices, cash, shares, params,
                    bar_time, trade_records, level_buy_count, level_buy_amount,
                )
                cash, shares, level_shares = self._execute_sells(
                    triggered_sells, level_shares, grid_prices, cash, shares, params,
                    bar_time, trade_records, level_sell_count, level_sell_amount,
                    level_profit, total_commission, total_stamp_tax,
                )

            # --- Stop-loss / take-profit ---
            if c < params.price_lower or l < params.price_lower:
                # Sell all remaining
                if shares > 0:
                    commission = shares * c * params.sell_commission
                    tax = shares * c * params.stamp_tax
                    cash += shares * c - commission - tax
                    trade_records.append({
                        "time": bar_time, "action": "SELL", "price": c,
                        "shares": shares, "amount": shares * c,
                        "commission": commission, "stamp_tax": tax,
                        "level": -1, "reason": "stop_loss",
                    })
                    shares = 0
                stopped_out = True

            if c > params.price_upper or h > params.price_upper:
                if shares > 0:
                    commission = shares * c * params.sell_commission
                    tax = shares * c * params.stamp_tax
                    cash += shares * c - commission - tax
                    trade_records.append({
                        "time": bar_time, "action": "SELL", "price": c,
                        "shares": shares, "amount": shares * c,
                        "commission": commission, "stamp_tax": tax,
                        "level": -1, "reason": "take_profit",
                    })
                    shares = 0
                stopped_out = True

            # --- Record NAV and advance bar ---
            nav = cash + shares * c
            nav_records.append({"time": bar_time, "nav": nav})
            prev_c = c

        # --- Compute metrics ---
        nav_df = pd.DataFrame(nav_records)
        trades_df = pd.DataFrame(trade_records)

        metrics = self._compute_metrics(nav_df, trades_df, initial_price)

        # Grid level stats
        level_stats = []
        for i in range(n_levels):
            level_stats.append({
                "grid_level": i,
                "grid_price": grid_prices[i],
                "buy_count": level_buy_count[i],
                "sell_count": level_sell_count[i],
                "total_buy_amount": round(level_buy_amount[i], 2),
                "total_sell_amount": round(level_sell_amount[i], 2),
                "grid_profit": round(level_profit[i], 2),
            })
        level_stats_df = pd.DataFrame(level_stats)

        # Attribution — use initial NAV as denominator (includes base_shares value)
        initial_nav_val = nav_df["nav"].iloc[0] if not nav_df.empty else self.initial_capital
        grid_trading_return = sum(level_profit.values()) / initial_nav_val
        base_return = (shares * df.iloc[-1]["close"] - shares * initial_price) / initial_nav_val if shares > 0 else 0.0
        total_commission_val = sum(t.get("commission", 0) for t in trade_records)
        total_stamp_tax_val = sum(t.get("stamp_tax", 0) for t in trade_records)

        final_nav = nav_df["nav"].iloc[-1] if not nav_df.empty else self.initial_capital
        total_return = (final_nav - initial_nav_val) / initial_nav_val

        return GridBacktestResult(
            ts_code=params.ts_code if hasattr(params, "ts_code") else "",
            nav_series=nav_df,
            trades=trades_df,
            metrics=metrics,
            grid_level_stats=level_stats_df,
            attribution={
                "grid_trading_return": grid_trading_return,
                "base_position_return": base_return,
                "total_return": total_return,
                "total_commission": total_commission_val,
                "total_stamp_tax": total_stamp_tax_val,
            },
        )

    # ── helpers ──

    def _execute_buys(self, levels, level_shares, grid_prices, cash, shares,
                      params, bar_time, trades, level_buy_count, level_buy_amount):
        for i in levels:
            gp = grid_prices[i]
            buy_qty = params.order_shares
            if buy_qty < 100:
                continue
            cost = buy_qty * gp
            commission = cost * params.buy_commission
            total_cost = cost + commission
            if total_cost > cash:
                continue  # Skip this level if not enough cash
            cash -= total_cost
            shares += buy_qty
            level_shares[i] = level_shares.get(i, 0) + buy_qty
            level_buy_count[i] = level_buy_count.get(i, 0) + 1
            level_buy_amount[i] = level_buy_amount.get(i, 0) + cost
            trades.append({
                "time": bar_time, "action": "BUY", "price": gp,
                "shares": buy_qty, "amount": cost,
                "commission": commission, "level": i,
            })
        return cash, shares, level_shares

    def _execute_sells(self, levels, level_shares, grid_prices, cash, shares,
                       params, bar_time, trades, level_sell_count, level_sell_amount,
                       level_profit, _total_commission, _total_stamp_tax):
        for sell_level in levels:
            sell_price = grid_prices[sell_level]
            # Match against shares bought at *lower* levels (buy low, sell high)
            for buy_level in range(0, sell_level):
                held = level_shares.get(buy_level, 0)
                if held <= 0:
                    continue
                buy_price = grid_prices[buy_level]
                revenue = held * sell_price
                commission = revenue * params.sell_commission
                tax = revenue * params.stamp_tax
                cash += revenue - commission - tax
                shares -= held
                # Profit = sell_revenue - buy_cost - buy_commission - sell_commission - stamp_tax
                buy_cost = held * buy_price
                buy_commission = buy_cost * params.buy_commission
                level_profit[buy_level] = level_profit.get(buy_level, 0) + (
                    revenue - commission - tax - buy_cost - buy_commission
                )
                level_shares[buy_level] = 0
                level_sell_count[buy_level] = level_sell_count.get(buy_level, 0) + 1
                level_sell_amount[buy_level] = level_sell_amount.get(buy_level, 0) + revenue
                trades.append({
                    "time": bar_time, "action": "SELL", "price": sell_price,
                    "shares": held, "amount": revenue,
                    "commission": commission, "stamp_tax": tax,
                    "level": buy_level, "sell_level": sell_level,
                })
        return cash, shares, level_shares

    def _compute_metrics(
        self,
        nav_df: pd.DataFrame,
        trades_df: pd.DataFrame,
        initial_price: float,
    ) -> dict:
        """Compute standard backtest metrics."""
        if nav_df.empty or "nav" not in nav_df.columns:
            return {}

        nav = nav_df["nav"].values
        initial_nav = nav[0]  # includes base_shares value
        final_nav = nav[-1]
        total_return = (final_nav - initial_nav) / initial_nav
        n_bars = len(nav)

        # Annualized return using actual calendar days from bar timestamps.
        # Extract date from bar_time (YYYYMMDDHHMMSS) or datetime column.
        days_in_period = 252  # fallback
        if n_bars >= 2:
            try:
                time_col = nav_df.columns[0]  # 'time' or 'bar_time'
                start_str = str(nav_df[time_col].iloc[0])[:8]
                end_str = str(nav_df[time_col].iloc[-1])[:8]
                if len(start_str) == 8 and len(end_str) == 8:
                    from datetime import datetime as _dt
                    d0 = _dt.strptime(start_str, "%Y%m%d")
                    d1 = _dt.strptime(end_str, "%Y%m%d")
                    days_in_period = max((d1 - d0).days, 1)
            except (ValueError, KeyError):
                pass
        years = days_in_period / 365.25
        annual_return = (final_nav / initial_nav) ** (1.0 / max(years, 0.01)) - 1

        # Max drawdown
        peak = np.maximum.accumulate(nav)
        drawdown = (nav - peak) / peak
        max_drawdown = float(drawdown.min())

        # Sharpe ratio (annualized using actual period length)
        returns = np.diff(nav) / nav[:-1]
        if np.std(returns) > 0 and years > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 / years * n_bars))
        else:
            sharpe = 0.0

        # Win rate: sell_price > corresponding buy_price (level i buy → level j sell, j > i)
        win_count = 0
        sell_count = 0
        total_trades = len(trades_df)
        if total_trades > 0 and "action" in trades_df.columns:
            sells = trades_df[trades_df["action"] == "SELL"]
            sell_count = len(sells)
            for _, s in sells.iterrows():
                sell_lv = s.get("sell_level", s.get("level", -1))
                buy_lv = s.get("level", -1)
                if sell_lv > buy_lv:
                    win_count += 1
        win_rate = win_count / max(sell_count, 1)

        return {
            "final_nav": round(final_nav, 4),
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
            "trade_count": total_trades,
            "win_rate": win_rate,
        }

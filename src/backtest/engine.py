"""Backtest engine: drives the backtest loop."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.backtest.metrics import compute_metrics
from src.backtest.portfolio import Portfolio
from src.backtest.position_sizing import compute_position_weights
from src.backtest.result import BacktestResult
from src.factors.industry_neutral import apply_industry_constraint

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Vectorized backtest engine for multi-factor stock selection.

    Drives the backtest loop (T+1 execution to avoid look-ahead bias):
    1. Determine rebalance dates based on frequency.
    2. On each rebalance date T, select top_n stocks by total_score.
    3. Execute rebalance on T+1 at OPEN price (no look-ahead bias).
    4. Track daily NAV and trades.
    5. Optionally check risk controls every trading day.

    Args:
        initial_capital: Starting capital (default 1,000,000).
        top_n: Number of stocks to hold (default 10).
        rebalance_freq: Rebalance frequency — "D" (daily), "W" (weekly), "M" (monthly).
        buy_commission: Buy commission rate.
        sell_commission: Sell commission rate.
        stamp_tax: Stamp tax rate on sells.
        risk_control_enabled: Whether to enable stop-loss/take-profit/drawdown stop.
        stop_loss: Individual stock stop-loss threshold.
        take_profit: Individual stock take-profit threshold.
        max_drawdown_stop: Portfolio max drawdown stop threshold.
        cooldown_days: Cooldown days after stop-loss.
        position_sizing_method: "equal_weight", "score_weighted", or "risk_parity".
        min_weight: Minimum weight per stock.
        max_weight: Maximum weight per stock.
        industry_map: Dict mapping ts_code to industry name (for industry neutral).
        max_industry_pct: Max single-industry percentage.
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        top_n: int = 10,
        rebalance_freq: str = "M",
        buy_commission: float = 0.0003,
        sell_commission: float = 0.0003,
        stamp_tax: float = 0.001,
        # Risk control
        risk_control_enabled: bool = False,
        stop_loss: float = -0.08,
        take_profit: float = 0.15,
        max_drawdown_stop: float = -0.10,
        cooldown_days: int = 5,
        # Position sizing
        position_sizing_method: str = "equal_weight",
        min_weight: float = 0.05,
        max_weight: float = 0.20,
        # Industry neutral
        industry_map: dict[str, str] | None = None,
        max_industry_pct: float = 0.30,
    ):
        self.initial_capital = initial_capital
        self.top_n = top_n
        self.rebalance_freq = rebalance_freq
        self.portfolio = Portfolio(
            initial_capital=initial_capital,
            buy_commission=buy_commission,
            sell_commission=sell_commission,
            stamp_tax=stamp_tax,
        )
        # Risk control
        self.risk_control_enabled = risk_control_enabled
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.max_drawdown_stop = max_drawdown_stop
        self.cooldown_days = cooldown_days
        # Position sizing
        self.position_sizing_method = position_sizing_method
        self.min_weight = min_weight
        self.max_weight = max_weight
        # Industry neutral
        self.industry_map = industry_map or {}
        self.max_industry_pct = max_industry_pct

    def run(
        self,
        df: pd.DataFrame,
        benchmark_df: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Run backtest on scored stock data.

        Args:
            df: DataFrame with columns [trade_date, ts_code, close, total_score, ...].
                Must be sorted by trade_date ascending.
            benchmark_df: Optional DataFrame with [trade_date, close] for benchmark index.

        Returns:
            BacktestResult with NAV series, trades, and metrics.

        Raises:
            ValueError: If required columns are missing.
        """
        required = {"trade_date", "ts_code", "close", "open"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        if "total_score" not in df.columns:
            raise ValueError("Missing 'total_score' column — run factor scoring first")

        # Get sorted unique dates
        dates = sorted(df["trade_date"].unique())

        # Determine rebalance dates
        rebalance_dates = self._get_rebalance_dates(dates)

        # Pre-build price lookup: {(date, ts_code): close}
        price_map = {}
        for _, row in df[["trade_date", "ts_code", "close"]].iterrows():
            price_map[(row["trade_date"], row["ts_code"])] = row["close"]

        # Pre-build open price lookup: {(date, ts_code): open}
        open_map = {}
        for _, row in df[["trade_date", "ts_code", "open"]].iterrows():
            open_map[(row["trade_date"], row["ts_code"])] = row["open"]

        # Pre-build score lookup per date
        score_map = {}
        for date in rebalance_dates:
            day = df[df["trade_date"] == date]
            if "is_trading" in day.columns:
                day = day[day["is_trading"] == True]
            day = day.dropna(subset=["total_score"])
            top = day.nlargest(self.top_n * 2, "total_score")  # Get extra for industry filtering

            # Apply industry neutral constraint
            if self.industry_map:
                top_df = top[["ts_code", "trade_date", "total_score"]].reset_index(drop=True)
                top = apply_industry_constraint(
                    top_df, self.industry_map,
                    max_pct=self.max_industry_pct, n=self.top_n,
                )
            selected = top.head(self.top_n)
            score_map[date] = {
                "codes": selected["ts_code"].tolist(),
                "scores": dict(zip(selected["ts_code"], selected["total_score"])),
            }

        # Pre-build suspended stocks per date
        suspended_map = {}
        if "is_trading" in df.columns:
            for date in dates:
                day = df[df["trade_date"] == date]
                suspended_map[date] = set(
                    day.loc[day["is_trading"] != True, "ts_code"].tolist()
                )

        # Pre-build volatility per (date, ts_code) for risk_parity
        vol_map = {}
        if self.position_sizing_method == "risk_parity" and "vol" in df.columns:
            df_sorted = df.sort_values(["ts_code", "trade_date"])
            df_sorted["vol_20d"] = df_sorted.groupby("ts_code")["close"].pct_change().rolling(20).std()
            for _, row in df_sorted[df_sorted["vol_20d"].notna()].iterrows():
                vol_map[(row["trade_date"], row["ts_code"])] = row["vol_20d"]

        # Track daily NAV
        nav_records = []

        # Pending rebalance: signal recorded on day T, executed on day T+1 at open
        pending_rebalance = None

        # Build benchmark NAV if provided
        bm_nav_map = {}
        if benchmark_df is not None and not benchmark_df.empty:
            bm_dates = sorted(benchmark_df["trade_date"].unique())
            bm_start = benchmark_df[benchmark_df["trade_date"] == bm_dates[0]]["close"].iloc[0]
            for d in bm_dates:
                price = benchmark_df[benchmark_df["trade_date"] == d]["close"].iloc[0]
                bm_nav_map[d] = price / bm_start

        for date in dates:
            # === Risk control check (every trading day) ===
            if self.risk_control_enabled and self.portfolio.positions:
                # Get close prices for risk check
                rc_prices = {}
                for code in self.portfolio.positions:
                    p = price_map.get((date, code))
                    if p is not None:
                        rc_prices[code] = p

                if rc_prices:
                    codes_to_sell, clear_all = self.portfolio.check_risk_controls(
                        rc_prices, date, dates,
                        stop_loss=self.stop_loss,
                        take_profit=self.take_profit,
                        max_drawdown_stop=self.max_drawdown_stop,
                        cooldown_days=self.cooldown_days,
                    )

                    if codes_to_sell:
                        # Use close prices for risk-control sells
                        self.portfolio.sell_stocks(codes_to_sell, rc_prices, date)
                        for t in self.portfolio.trades[-len(codes_to_sell):]:
                            t["reason"] = "risk_control"

            # === Execute pending rebalance from previous day's signal ===
            if pending_rebalance is not None:
                target_codes = pending_rebalance["codes"]
                target_scores = pending_rebalance["scores"]
                suspended = suspended_map.get(date, set())

                # Use TODAY'S OPEN prices for execution
                exec_prices = {}
                for code in target_codes:
                    p = open_map.get((date, code))
                    if p is not None and p > 0:
                        exec_prices[code] = p

                # Also get open prices for existing positions being sold
                for code in list(self.portfolio.positions.keys()):
                    if code not in exec_prices:
                        p = open_map.get((date, code))
                        if p is not None:
                            exec_prices[code] = p

                # Compute position weights
                target_weights = None
                if self.position_sizing_method != "equal_weight":
                    vols = {}
                    if self.position_sizing_method == "risk_parity":
                        for code in target_codes:
                            v = vol_map.get((date, code))
                            if v is not None:
                                vols[code] = v

                    target_weights = compute_position_weights(
                        self.position_sizing_method,
                        target_codes,
                        scores=target_scores,
                        volatilities=vols,
                        min_weight=self.min_weight,
                        max_weight=self.max_weight,
                    )

                self.portfolio.rebalance(
                    target_codes, exec_prices, date, suspended,
                    target_weights=target_weights,
                )
                pending_rebalance = None

            # Record signal on rebalance date (execute NEXT trading day)
            if date in rebalance_dates and date in score_map:
                pending_rebalance = score_map[date]

            # Calculate daily NAV (still using close prices)
            pos_prices = {}
            for code in self.portfolio.positions:
                p = price_map.get((date, code))
                if p is not None:
                    pos_prices[code] = p

            nav = self.portfolio.market_value(pos_prices)

            nav_records.append({
                "date": date,
                "nav": nav,
                "benchmark_nav": bm_nav_map.get(date, np.nan),
                "position_count": len(self.portfolio.positions),
            })

        # Build result
        nav_df = pd.DataFrame(nav_records)
        if nav_df.empty:
            raise ValueError("回测无有效交易日数据，请检查回测日期区间是否有对应数据")
        nav_series = nav_df.set_index("date")["nav"]
        nav_series.index = pd.to_datetime(nav_series.index)

        benchmark_nav = None
        if not nav_df["benchmark_nav"].isna().all():
            benchmark_nav = nav_df.set_index("date")["benchmark_nav"]
            benchmark_nav.index = pd.to_datetime(benchmark_nav.index)

        # Compute metrics
        metrics = compute_metrics(nav_series, benchmark_nav)
        metrics["trade_count"] = len(self.portfolio.trades)

        # Build trades DataFrame
        trades_df = pd.DataFrame(self.portfolio.trades) if self.portfolio.trades else pd.DataFrame(
            columns=["date", "ts_code", "action", "shares", "price", "amount", "cost", "reason"]
        )

        return BacktestResult(
            nav_series=nav_df,
            trades=trades_df,
            metrics=metrics,
        )

    def _get_rebalance_dates(self, dates: list[str]) -> set[str]:
        """Determine rebalance dates based on frequency.

        Args:
            dates: Sorted list of trade dates (YYYYMMDD strings).

        Returns:
            Set of rebalance dates.
        """
        if not dates:
            return set()

        dt_index = pd.to_datetime(dates)

        if self.rebalance_freq == "D":
            return set(dates)

        if self.rebalance_freq == "W":
            # First trading day of each week
            weeks = dt_index.to_series().dt.isocalendar().week.astype(int)
            years = dt_index.to_series().dt.year.astype(int)
            year_week = list(zip(years, weeks))
            seen = set()
            result = []
            for i, yw in enumerate(year_week):
                if yw not in seen:
                    seen.add(yw)
                    result.append(dates[i])
            return set(result)

        if self.rebalance_freq == "M":
            # First trading day of each month
            months = dt_index.to_series().dt.to_period("M")
            seen = set()
            result = []
            for i, m in enumerate(months):
                if m not in seen:
                    seen.add(m)
                    result.append(dates[i])
            return set(result)

        return set(dates)

"""Signal generator for live trading.

Reuses the existing factor pipeline and scheme configuration to produce
BUY/SELL signals by diffing new stock selection against current target holdings.
"""

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure factor modules are imported so they register themselves
import src.factors.candlestick
import src.factors.downside_risk
import src.factors.intraday_range
import src.factors.liquidity
import src.factors.ma_deviation
import src.factors.market_relative
import src.factors.momentum
import src.factors.profitability
import src.factors.return_20d
import src.factors.return_distribution
import src.factors.roe_change
import src.factors.rsi
import src.factors.short_reversal
import src.factors.trend_60d
import src.factors.turnover
import src.factors.valuation
import src.factors.valuation_extended
import src.factors.volume
import src.factors.volume_price
import src.factors.volatility

from src.config import DATA_DIR, DB_PATH
from src.data.cleaner import clean_pipeline
from src.data.storage import (
    load_daily_basic,
    load_daily_price,
    load_fina_indicator,
    merge_fina_indicator,
    merge_fundamentals,
    save_trade_signals,
)
from src.factors.base import get_registered_factors
from src.factors.scorer import compute_total_score, select_top_n, standardize_factors
from src.live.portfolio_tracker import get_current_target_portfolio, save_target_portfolio
from src.scheme import load_scheme


class SignalGenerator:
    """Generates live trading signals from the factor pipeline.

    Loads latest market data, runs factor calculation and scoring using the
    specified scheme, diffs against current target portfolio, and produces
    BUY/SELL signals.

    Args:
        scheme_name: Name of the scheme in schemes.yaml.
        top_n: Number of stocks to hold.
        total_capital: Total capital for position sizing (default 1,000,000).
        position_sizing: "equal_weight" (default) or "score_weighted".
    """

    def __init__(
        self,
        scheme_name: str = "default",
        top_n: int = 10,
        total_capital: float = 1_000_000,
        position_sizing: str = "equal_weight",
        holdings_provider=None,
        exclude_etf: bool = True,
    ):
        self.scheme_name = scheme_name
        self.top_n = top_n
        self.total_capital = total_capital
        self.position_sizing = position_sizing
        self.exclude_etf = exclude_etf

        # Diff 的持仓数据源：默认读 portfolio_snapshots（live 路径行为不变）；
        # paper 传入读 paper_holdings（实际虚拟持仓）的 provider。
        self._holdings_provider = holdings_provider or get_current_target_portfolio

        # Load scheme config (raises ValueError if scheme not found)
        self._enabled_factors, self._weights = load_scheme(scheme_name)

        # Internal state: full DataFrame for price lookups
        self._df: pd.DataFrame | None = None

    def compute_signals(
        self, rebalance_date: str
    ) -> tuple[list[dict[str, Any]], pd.DataFrame, str]:
        """Run factor pipeline + selection + diff and RETURN signals.

        Side-effect-free variant of :meth:`generate_signals`: does NOT write to
        ``trade_signals``, does NOT broadcast, does NOT save a portfolio
        snapshot. Used by paper trading to keep the paper path fully isolated
        from the live QMT path.

        Args:
            rebalance_date: Trade date string YYYYMMDD for the rebalance.

        Returns:
            ``(signals, top_picks, latest_date)``.

        Raises:
            ValueError: If no data available or scheme is invalid.
        """
        top_picks, latest_date = self.compute_selection(rebalance_date)
        signals = self.diff_holdings(top_picks, latest_date)
        return signals, top_picks, latest_date

    def compute_selection(
        self, rebalance_date: str
    ) -> tuple[pd.DataFrame, str]:
        """Run factor pipeline + top-N selection (NO diff). Cacheable.

        Paper worker caches the returned ``top_picks`` for daily-factor schemes
        and re-:meth:`diff_holdings` each tick. Used by :meth:`compute_signals`.
        """
        # 1. Load data
        df = self._load_data()
        if df.empty:
            raise ValueError("No data available for signal generation")

        # 2. Calculate factors
        df = self._calculate_factors(df)
        self._df = df  # store for price lookups

        # 3. Score and select top N
        latest_date = df["trade_date"].max()
        top_picks = self._score_and_select(df, latest_date)

        if top_picks.empty:
            raise ValueError(f"No stocks selected on {latest_date}")

        return top_picks, latest_date

    def diff_holdings(self, top_picks: pd.DataFrame, date: str) -> list[dict[str, Any]]:
        """Diff ``top_picks`` against current holdings → signal list."""
        return self._diff_portfolio(top_picks, date)

    def generate_signals(self, rebalance_date: str) -> list[dict[str, Any]]:
        """Run the full pipeline and generate trade signals for a rebalance date.

        Live path: :meth:`compute_signals` + persist to ``trade_signals`` +
        WebSocket broadcast + save target portfolio snapshot.

        Args:
            rebalance_date: Trade date string YYYYMMDD for the rebalance.

        Returns:
            List of signal dicts with keys: ts_code, action, quantity,
            price_type, scheme_name, rebalance_date.

        Raises:
            ValueError: If no data available or scheme is invalid.
        """
        signals, top_picks, latest_date = self.compute_signals(rebalance_date)

        # Save signals to database
        if signals:
            signals_df = pd.DataFrame(signals)
            saved = save_trade_signals(signals_df)
            print(f"[SignalGenerator] {saved} signals saved to trade_signals")

            # Push to connected WebSocket clients
            try:
                from src.live.server import broadcast_signals_sync
                ws_sent = broadcast_signals_sync(signals)
                if ws_sent:
                    print(f"[SignalGenerator] Broadcast to {ws_sent} WebSocket client(s)")
            except Exception as e:
                print(f"[SignalGenerator] WS broadcast skipped: {e}")

        # Save new target portfolio snapshot
        self._save_new_snapshot(top_picks, rebalance_date or latest_date)

        return signals

    def _load_data(self) -> pd.DataFrame:
        """Load recent market data from SQLite (~2 years for factor warmup)."""
        import datetime as _dt
        today = _dt.date.today().strftime("%Y%m%d")
        lookback = (_dt.date.today() - _dt.timedelta(days=504)).strftime("%Y%m%d")
        df = load_daily_price(start_date=lookback, end_date=today)
        if df.empty:
            return df

        # Reduce min_trading_days proportionally to the shorter lookback window
        # (~340 trading days in 504 calendar days) to avoid filtering all stocks
        df, report = clean_pipeline(df, min_trading_days=200)
        print(f"[SignalGenerator] Loaded {report['total_rows']} rows, "
              f"{report['total_stocks']} stocks, "
              f"date range: {report['date_range']}")

        # Merge fundamentals (PE/PB/PS)
        basic_df = load_daily_basic()
        if not basic_df.empty:
            df = merge_fundamentals(df, basic_df)

        # Merge financial indicators (ROE, ROE YoY) — needed by roe_yoy_rank factor
        fina_df = load_fina_indicator()
        if not fina_df.empty:
            df = merge_fina_indicator(df, fina_df)

        # Exclude ETFs if configured
        if self.exclude_etf:
            from src.grid.grid_etf import is_etf
            before = df["ts_code"].nunique()
            df = df[~df["ts_code"].apply(is_etf)]
            after = df["ts_code"].nunique()
            if before != after:
                print(f"[SignalGenerator] Excluded {before - after} ETFs "
                      f"({after} stocks remaining)")

        return df

    def _calculate_factors(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate only the factors required by this scheme."""
        factors = get_registered_factors()
        needed = self._enabled_factors  # only compute what the scheme uses
        factor_cols = []
        for name in needed:
            cls = factors.get(name)
            if cls is None:
                continue
            factor = cls()
            try:
                df = factor.calculate(df)
                factor_cols.append(factor.factor_name)
            except (KeyError, ValueError) as e:
                print(f"[SignalGenerator] Skip factor '{name}': {e}")

        print(f"[SignalGenerator] Calculated {len(factor_cols)}/{len(needed)} needed factors "
              f"(scheme: {self.scheme_name})")
        return df

    def _score_and_select(
        self, df: pd.DataFrame, date: str
    ) -> pd.DataFrame:
        """Standardize factors, compute scores with scheme weights, select top N."""
        # Only standardize the scheme's factor columns (standardize_factors appends _score)
        factor_cols = list(self._enabled_factors)

        df = standardize_factors(df, factor_cols)
        df = compute_total_score(df, weights=self._weights)

        # Select top N for the latest date
        top_picks = select_top_n(df, date, n=self.top_n)

        print(f"[SignalGenerator] Top {len(top_picks)} selected for {date} "
              f"using scheme '{self.scheme_name}'")
        for _, row in top_picks.iterrows():
            print(f"  {row['ts_code']}  score: {row['total_score']:.2f}")

        return top_picks

    def _diff_portfolio(
        self, top_picks: pd.DataFrame, date: str
    ) -> list[dict[str, Any]]:
        """Compare new selection against current holdings, generate signals.

        Args:
            top_picks: DataFrame of newly selected top N stocks.
            date: The rebalance date.

        Returns:
            List of signal dicts.
        """
        new_codes = set(top_picks["ts_code"].tolist())

        # Get current holdings via the injected provider
        # (live: portfolio_snapshots target state; paper: paper_holdings actual)
        current = self._holdings_provider()
        current_codes = set(current["ts_code"].tolist()) if not current.empty else set()

        signals = []

        # BUY: stocks in new selection but not currently held
        for code in new_codes - current_codes:
            quantity = self._calc_quantity(top_picks, code)
            signals.append({
                "ts_code": code,
                "action": "BUY",
                "quantity": quantity,
                "price_type": "MKT",
                "scheme_name": self.scheme_name,
                "rebalance_date": date,
            })

        # SELL: stocks currently held but not in new selection
        for code in current_codes - new_codes:
            # Sell the full position from snapshot
            existing = current[current["ts_code"] == code]
            quantity = int(existing["target_shares"].iloc[0]) if "target_shares" in existing.columns else 0
            signals.append({
                "ts_code": code,
                "action": "SELL",
                "quantity": quantity,
                "price_type": "MKT",
                "scheme_name": self.scheme_name,
                "rebalance_date": date,
            })

        if not signals:
            print("[SignalGenerator] No changes — portfolio unchanged")
        else:
            buys = sum(1 for s in signals if s["action"] == "BUY")
            sells = sum(1 for s in signals if s["action"] == "SELL")
            print(f"[SignalGenerator] Generated {buys} BUY, {sells} SELL signals")

        return signals

    def _calc_quantity(self, top_picks: pd.DataFrame, ts_code: str) -> int:
        """Calculate target share quantity for a stock.

        Uses equal-weight allocation: total_capital / top_n / price.
        Rounds down to nearest 100 shares (A-share lot size).

        Args:
            top_picks: Selected stocks DataFrame.
            ts_code: The stock code.

        Returns:
            Target share quantity (multiple of 100).
        """
        capital_per_stock = self.total_capital / self.top_n

        # Look up latest close price from the full dataset
        price = None
        if self._df is not None:
            latest_date = self._df["trade_date"].max()
            match = self._df[
                (self._df["ts_code"] == ts_code) &
                (self._df["trade_date"] == latest_date)
            ]
            if not match.empty:
                price = match["close"].iloc[0]

        if price is None or price <= 0 or not np.isfinite(price):
            return 0

        raw_shares = capital_per_stock / price
        lots = int(raw_shares / 100)
        shares = lots * 100

        return max(shares, 100)  # Minimum 1 lot

    def _save_new_snapshot(
        self, top_picks: pd.DataFrame, rebalance_date: str
    ) -> None:
        """Save the new target portfolio snapshot."""
        positions = []
        for _, row in top_picks.iterrows():
            code = row["ts_code"]
            score = row.get("total_score", 0)
            # Equal weight for now
            weight = 1.0 / self.top_n
            shares = self._calc_quantity(top_picks, code)

            positions.append({
                "ts_code": code,
                "target_weight": weight,
                "target_shares": shares,
                "score": score,
            })

        saved = save_target_portfolio(rebalance_date, positions)
        print(f"[SignalGenerator] Portfolio snapshot saved: {saved} positions "
              f"for {rebalance_date}")

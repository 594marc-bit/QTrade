"""Grid trading stock selector: user-specified or factor-based screening."""

import pandas as pd

from src.config import (
    GRID_BASE_SHARES,
    GRID_BUY_COMMISSION,
    GRID_LEVELS,
    GRID_MODE,
    GRID_ORDER_SHARES,
    GRID_PRICE_RANGE_PCT,
    GRID_SELL_COMMISSION,
    GRID_STAMP_TAX,
)
from src.grid.grid_params import GridParams


def select_stocks(
    stock_codes: list[str] | None = None,
    factor_top_n: int | None = None,
    df: pd.DataFrame | None = None,
    factor_col: str = "grid_suitability",
) -> list[str]:
    """Select stocks for grid trading.

    Args:
        stock_codes: User-specified stock codes. If provided, factor
            screening is skipped.
        factor_top_n: Number of top stocks to select by factor ranking.
            Only used when stock_codes is None.
        df: DataFrame with factor scores. Required if factor_top_n is set.
        factor_col: Factor column name to rank by.

    Returns:
        List of ts_code strings.
    """
    if stock_codes:
        return stock_codes

    if factor_top_n and df is not None and factor_col in df.columns:
        latest_date = df["trade_date"].max()
        latest = df[df["trade_date"] == latest_date].dropna(subset=[factor_col])
        top = latest.nlargest(factor_top_n, factor_col)
        return top["ts_code"].tolist()

    return []


def build_grid_params(
    ts_code: str,
    df: pd.DataFrame | None = None,
    price_range_pct: float | None = None,
    grid_levels: int | None = None,
    grid_mode: str | None = None,
    order_shares: float | None = None,
    base_shares: int | None = None,
    custom_params: dict[str, "GridParams"] | None = None,
) -> GridParams:
    """Build GridParams for a stock.

    Priority: custom_params > caller args > config defaults.
    If no center price can be determined, raises ValueError.

    Args:
        ts_code: Stock code.
        df: Optional DataFrame with close prices for auto price range.
        price_range_pct: Override price range percentage.
        grid_levels: Override grid levels.
        grid_mode: Override grid mode.
        order_shares: Override order amount.
        base_shares: Override base shares.
        custom_params: Per-stock GridParams dict keyed by ts_code.

    Returns:
        GridParams instance.
    """
    # Per-stock override
    if custom_params and ts_code in custom_params:
        return custom_params[ts_code]

    # Auto-compute grid bounds from data: use the stock's actual price range
    # in the data period (not just the latest close). This ensures the grid
    # covers the historical price movement instead of only the current level.
    if df is not None and ts_code in df["ts_code"].values:
        stock_df = df[df["ts_code"] == ts_code]
        if not stock_df.empty and "close" in stock_df.columns:
            price_low = float(stock_df["close"].min())
            price_high = float(stock_df["close"].max())
            center_price = float(stock_df["close"].iloc[-1])
        else:
            raise ValueError(f"Cannot determine price range for {ts_code}")
    else:
        raise ValueError(f"No data for {ts_code}, cannot auto-compute grid params")

    pct = price_range_pct or GRID_PRICE_RANGE_PCT
    # Expand the actual range by the configured margin on each side.
    # Enforce a minimum range of pct% around the current price so that
    # ultra-stable stocks (like money-market ETFs at 100±0.01) still get
    # a usable grid width.
    min_range = center_price * (pct / 100.0) * 2
    actual_range = price_high - price_low
    if actual_range < min_range:
        # Data range is too narrow — use center ± pct% instead
        half = center_price * (pct / 100.0)
        price_low = round(center_price - half, 3)
        price_high = round(center_price + half, 3)
    range_half = (price_high - price_low) / 2
    margin = range_half * (pct / 100.0)
    price_lower = round(price_low - margin, 3)
    price_upper = round(price_high + margin, 3)

    # Ensure price bounds are valid — price_lower < price_upper with min 0.01 gap.
    # First clamp upper above lower, then clamp lower below upper.
    price_upper = max(price_upper, price_lower + 0.01)
    price_lower = min(price_lower, price_upper - 0.01)

    return GridParams(
        price_upper=price_upper,
        price_lower=price_lower,
        grid_levels=grid_levels or GRID_LEVELS,
        grid_mode=grid_mode or GRID_MODE,
        order_shares=order_shares or GRID_ORDER_SHARES,
        base_shares=base_shares or GRID_BASE_SHARES,
        buy_commission=GRID_BUY_COMMISSION,
        sell_commission=GRID_SELL_COMMISSION,
        stamp_tax=GRID_STAMP_TAX,
    )

"""Unified data fetching dispatcher.

Routes data requests to the appropriate data source (AKShare or Tushare)
based on the DATA_SOURCE configuration in config.ini.    """

from src.config import DATA_SOURCE

# Validate data source
_VALID_SOURCES = ("akshare", "tushare")
if DATA_SOURCE not in _VALID_SOURCES:
    raise ValueError(
        f"Invalid DATA_SOURCE '{DATA_SOURCE}'. "
        f"Must be one of: {_VALID_SOURCES}. "
        f"Check config.ini [data] data_source."
    )

# Import the selected backend
if DATA_SOURCE == "tushare":
    from src.data.tushare_fetcher import (
        fetch_daily_basic,
        get_index_constituents,
        get_index_daily,
        get_stock_daily,
        get_stocks_batch,
        sync_stocks_data,
    )
else:
    from src.data.akshare_fetcher import (
        fetch_daily_basic,
        get_index_constituents,
        get_index_daily,
        get_stock_daily,
        get_stocks_batch,
        sync_stocks_data,
    )

__all__ = [
    "fetch_daily_basic",
    "get_index_constituents",
    "get_index_daily",
    "get_stock_daily",
    "get_stocks_batch",
    "sync_stocks_data",
]

## ADDED Requirements

### Requirement: Fetch daily basic indicators
The system SHALL provide a `fetch_daily_basic` function that retrieves PE_TTM, PB, PS_TTM per stock per date, supporting both Tushare and AKShare data sources.

#### Scenario: Tushare fetch by trade date
- **WHEN** DATA_SOURCE is "tushare" and fetch_daily_basic is called with a list of ts_codes and date range
- **THEN** the system calls `pro.daily_basic(trade_date=date)` for each trading date and returns a DataFrame with columns: trade_date, ts_code, pe_ttm, pb, ps_ttm

#### Scenario: AKShare fetch by stock
- **WHEN** DATA_SOURCE is "akshare" and fetch_daily_basic is called with a list of ts_codes and date range
- **THEN** the system calls `ak.stock_a_indicator_lg(symbol=code)` for each stock and returns a DataFrame with columns: trade_date, ts_code, pe_ttm, pb, ps_ttm

#### Scenario: Handles API failures gracefully
- **WHEN** an API call fails for a specific stock or date
- **THEN** the system logs a warning and continues with remaining stocks/dates, returning partial data

### Requirement: Store and load daily basic data
The system SHALL provide `save_daily_basic` and `load_daily_basic` functions using a dedicated `daily_basic` SQLite table.

#### Scenario: Save and roundtrip
- **WHEN** save_daily_basic is called with a DataFrame containing trade_date, ts_code, pe_ttm, pb, ps_ttm
- **THEN** data is persisted to the daily_basic table and can be loaded back with load_daily_basic

#### Scenario: Load with date filter
- **WHEN** load_daily_basic is called with start_date and end_date
- **THEN** only rows within the date range are returned

### Requirement: Merge fundamentals into price data
The system SHALL provide a function to left-join daily_basic data onto the main price DataFrame on (trade_date, ts_code).

#### Scenario: Successful merge
- **WHEN** merge_fundamentals is called with price_df and basic_df
- **THEN** columns pe_ttm, pb, ps_ttm are added to price_df, with NaN for missing matches

"""Configuration management for QTrade."""

import configparser
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file (API tokens) — silent if missing
load_dotenv(Path(__file__).parent.parent / ".env")

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "stock_data.db"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Read config.ini (optional — use defaults if missing)
_config = configparser.ConfigParser()
_config_path = PROJECT_ROOT / "config.ini"
if _config_path.exists():
    _config.read(str(_config_path))

# Date range
START_DATE = _config.get("data", "start_date", fallback="20230101")

# END_DATE: read from config.ini, default to latest trading day
_config_end_date = _config.get("data", "end_date", fallback="").strip()
if _config_end_date:
    END_DATE = _config_end_date
else:
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    # Try to get latest trading day from Tushare calendar; fallback to today
    try:
        import tushare as ts
        _token = _config.get("tushare", "token", fallback="").strip()
        if _token:
            ts.set_token(_token)
            from datetime import timedelta
            _start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
            _cal = ts.pro_api().trade_cal(
                exchange="SSE", start_date=_start, end_date=today
            )
            _open = _cal[_cal["is_open"] == 1]
            END_DATE = _open["cal_date"].max() if not _open.empty else today
        else:
            END_DATE = today
    except Exception:
        END_DATE = today

# Index configuration
INDEX_CODE = "000300"  # 沪深300

# Data source: "akshare" or "tushare"
DATA_SOURCE = _config.get("data", "data_source", fallback="tushare").lower().strip()

# Tushare configuration
# Priority: .env (TUSHARE_TOKEN) > config.ini [tushare] token
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip() or _config.get("tushare", "token", fallback="").strip()
TUSHARE_FETCH_INTERVAL = float(_config.get("tushare", "fetch_interval", fallback="0.5"))

# Cache settings
CACHE_EXPIRE_DAYS = 7

# Fetch settings (AKShare)
FETCH_INTERVAL = 2.0  # seconds between API calls (increased to avoid rate-limiting)
FETCH_RETRIES = 3

# Cleaning settings
MAX_CONSECUTIVE_MISSING = 5  # drop rows with >5 consecutive missing
PRICE_CHANGE_LIMIT = 0.20  # ±20% daily change limit

# Factor defaults
MOMENTUM_LOOKBACK = 20
VOLUME_SHORT_WINDOW = 5
VOLUME_LONG_WINDOW = 20
VOLATILITY_WINDOW = 20
RSI_WINDOW = 14
MA_DEVIATION_WINDOW = 20
TURNOVER_SHORT_WINDOW = 5
TURNOVER_LONG_WINDOW = 20
INTRADAY_RANGE_WINDOW = 10

# Scoring weights
DEFAULT_WEIGHTS = {
    "intraday_range_score": -0.15,
    "pb_rank_score": -0.25,
    "pe_ttm_rank_score": -0.20,
    "trend_score": -0.10,
    "volatility_score": -0.15,
}

# IC analysis
IC_THRESHOLD = 0.03
IC_WIN_RATE_THRESHOLD = 0.55

# Backtest settings
BACKTEST_INITIAL_CAPITAL = float(_config.get("backtest", "initial_capital", fallback="1000000"))
BACKTEST_TOP_N = int(_config.get("backtest", "top_n", fallback="10"))
BACKTEST_REBALANCE_FREQ = _config.get("backtest", "rebalance_freq", fallback="M").strip().upper()
BACKTEST_BUY_COMMISSION = float(_config.get("backtest", "buy_commission", fallback="0.0003"))
BACKTEST_SELL_COMMISSION = float(_config.get("backtest", "sell_commission", fallback="0.0003"))
BACKTEST_STAMP_TAX = float(_config.get("backtest", "stamp_tax", fallback="0.001"))
BACKTEST_RISK_FREE_RATE = float(_config.get("backtest", "risk_free_rate", fallback="0.02"))

# Risk control settings
RISK_CONTROL_ENABLED = _config.get("risk_control", "enabled", fallback="false").strip().lower() == "true"
RISK_CONTROL_STOP_LOSS = float(_config.get("risk_control", "stop_loss", fallback="-0.12"))
RISK_CONTROL_TAKE_PROFIT = float(_config.get("risk_control", "take_profit", fallback="0.15"))
RISK_CONTROL_MAX_DRAWDOWN_STOP = float(_config.get("risk_control", "max_drawdown_stop", fallback="-0.10"))
RISK_CONTROL_COOLDOWN_DAYS = int(_config.get("risk_control", "cooldown_days", fallback="5"))

# Industry neutral settings
INDUSTRY_NEUTRAL_ENABLED = _config.get("industry_neutral", "enabled", fallback="false").strip().lower() == "true"
INDUSTRY_NEUTRAL_MAX_PCT = float(_config.get("industry_neutral", "max_industry_pct", fallback="0.30"))

# Position sizing settings
POSITION_SIZING_METHOD = _config.get("position_sizing", "method", fallback="equal_weight").strip().lower()
POSITION_SIZING_MIN_WEIGHT = float(_config.get("position_sizing", "min_weight", fallback="0.05"))
POSITION_SIZING_MAX_WEIGHT = float(_config.get("position_sizing", "max_weight", fallback="0.20"))

# Adaptive weights settings
ADAPTIVE_WEIGHTS_ENABLED = _config.get("adaptive_weights", "enabled", fallback="false").strip().lower() == "true"
ADAPTIVE_WEIGHTS_IC_WINDOW = int(_config.get("adaptive_weights", "ic_window", fallback="60"))

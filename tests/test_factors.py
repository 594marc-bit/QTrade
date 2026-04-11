"""Tests for factor engine: factors, scoring, IC analysis."""

import numpy as np
import pandas as pd
import pytest


# --- Fixtures ---

@pytest.fixture
def sample_price_df():
    """Create sample price data for factor testing."""
    dates = pd.date_range("2023-01-03", periods=80, freq="B").strftime("%Y%m%d")
    rows = []
    np.random.seed(42)
    for code in [
        "600519.SH", "000858.SZ", "603121.SH", "600036.SH", "601628.SH",
        "600809.SH", "002475.SZ", "000333.SZ", "600276.SH", "601318.SH",
        "000001.SZ", "600000.SH",
    ]:
        base_price = np.random.uniform(50, 200)
        for i, date in enumerate(dates):
            price = base_price * (1 + np.random.randn() * 0.02)
            base_price = price
            rows.append({
                "trade_date": date,
                "ts_code": code,
                "open": price - 0.5,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "vol": int(10000 + np.random.randint(0, 5000)),
                "amount": price * 10000,
            })
    return pd.DataFrame(rows)


# --- Factor Base Tests ---

class TestFactorRegistry:
    def test_register_and_get(self):
        from src.factors.base import get_registered_factors, get_factor
        # MomentumFactor should be registered via import
        import src.factors.momentum
        registry = get_registered_factors()
        assert "momentum_20d" in registry

        factor = get_factor("momentum_20d")
        assert factor.factor_name == "momentum_20d"

    def test_unknown_factor_raises(self):
        from src.factors.base import get_factor
        with pytest.raises(ValueError, match="not registered"):
            get_factor("nonexistent_factor")


# --- Momentum Factor Tests ---

class TestMomentumFactor:
    def test_calculates_returns(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        factor = MomentumFactor(lookback=20)
        result = factor.calculate(sample_price_df)
        assert "momentum_20d" in result.columns

        # First 20 rows per stock should be NaN
        for code in result["ts_code"].unique():
            stock = result[result["ts_code"] == code]
            assert stock["momentum_20d"].iloc[:20].isna().all()
            assert stock["momentum_20d"].iloc[20:].notna().any()


# --- Volume Factor Tests ---

class TestVolumeFactor:
    def test_calculates_ratio(self, sample_price_df):
        from src.factors.volume import VolumeFactor
        factor = VolumeFactor(short_window=5, long_window=20)
        result = factor.calculate(sample_price_df)
        assert "vol_ratio" in result.columns

    def test_zero_amount_handling(self):
        from src.factors.volume import VolumeFactor
        # Create data where some amounts are zero
        dates = pd.date_range("2023-01-03", periods=25, freq="B").strftime("%Y%m%d")
        df = pd.DataFrame({
            "trade_date": np.tile(dates, 2),
            "ts_code": ["600519.SH"] * 25 + ["000858.SZ"] * 25,
            "close": np.concatenate([np.random.randn(25) + 100, np.random.randn(25) + 50]),
            "amount": [100] * 25 + [0] * 25,  # Second stock has zero amount
        })
        factor = VolumeFactor()
        result = factor.calculate(df)
        # Second stock should have NaN vol_ratio (division by zero)
        second_stock = result[result["ts_code"] == "000858.SZ"]
        assert second_stock["vol_ratio"].isna().all()


# --- Volatility Factor Tests ---

class TestVolatilityFactor:
    def test_calculates_std(self, sample_price_df):
        from src.factors.volatility import VolatilityFactor
        factor = VolatilityFactor(window=20)
        result = factor.calculate(sample_price_df)
        assert "volatility_20d" in result.columns
        # Values should be positive (std dev)
        valid_vals = result["volatility_20d"].dropna()
        assert all(valid_vals > 0)


# --- RSI Factor Tests ---

class TestRSIFactor:
    def test_calculates_rsi(self, sample_price_df):
        from src.factors.rsi import RsiFactor
        factor = RsiFactor(window=14)
        result = factor.calculate(sample_price_df)
        assert "rsi_14d" in result.columns
        valid_vals = result["rsi_14d"].dropna()
        assert all(valid_vals >= 0)
        assert all(valid_vals <= 100)

    def test_initial_nan(self, sample_price_df):
        from src.factors.rsi import RsiFactor
        factor = RsiFactor(window=14)
        result = factor.calculate(sample_price_df)
        for code in result["ts_code"].unique():
            stock = result[result["ts_code"] == code]
            assert stock["rsi_14d"].iloc[0].item() is pd.NA or stock["rsi_14d"].isna().iloc[0]


# --- MA Deviation Factor Tests ---

class TestMaDeviationFactor:
    def test_calculates_deviation(self, sample_price_df):
        from src.factors.ma_deviation import MaDeviationFactor
        factor = MaDeviationFactor(window=20)
        result = factor.calculate(sample_price_df)
        assert "ma_deviation_20d" in result.columns
        for code in result["ts_code"].unique():
            stock = result[result["ts_code"] == code]
            assert stock["ma_deviation_20d"].iloc[:19].isna().all()

    def test_deviation_near_zero(self):
        from src.factors.ma_deviation import MaDeviationFactor
        dates = pd.date_range("2023-01-03", periods=25, freq="B").strftime("%Y%m%d")
        df = pd.DataFrame({
            "trade_date": np.tile(dates, 2),
            "ts_code": ["A"] * 25 + ["B"] * 25,
            "close": [100.0] * 25 + [200.0] * 25,
        })
        factor = MaDeviationFactor(window=10)
        result = factor.calculate(df)
        valid = result["ma_deviation_20d"].dropna()
        assert all(abs(v) < 0.001 for v in valid)


# --- Turnover Factor Tests ---

class TestTurnoverFactor:
    def test_calculates_ratio(self, sample_price_df):
        from src.factors.turnover import TurnoverFactor
        factor = TurnoverFactor(short_window=5, long_window=20)
        result = factor.calculate(sample_price_df)
        assert "turnover_momentum_10d" in result.columns
        valid_vals = result["turnover_momentum_10d"].dropna()
        assert all(valid_vals > 0)

    def test_zero_vol_handling(self):
        from src.factors.turnover import TurnoverFactor
        dates = pd.date_range("2023-01-03", periods=25, freq="B").strftime("%Y%m%d")
        df = pd.DataFrame({
            "trade_date": np.tile(dates, 2),
            "ts_code": ["A"] * 25 + ["B"] * 25,
            "vol": [10000] * 25 + [0] * 25,
        })
        factor = TurnoverFactor()
        result = factor.calculate(df)
        second = result[result["ts_code"] == "B"]
        assert second["turnover_momentum_10d"].isna().all()


# --- Intraday Range Factor Tests ---

class TestIntradayRangeFactor:
    def test_calculates_range(self, sample_price_df):
        from src.factors.intraday_range import IntradayRangeFactor
        factor = IntradayRangeFactor(window=10)
        result = factor.calculate(sample_price_df)
        assert "intraday_range_10d" in result.columns
        valid_vals = result["intraday_range_10d"].dropna()
        assert all(valid_vals >= 0)

    def test_zero_price_handling(self):
        from src.factors.intraday_range import IntradayRangeFactor
        dates = pd.date_range("2023-01-03", periods=15, freq="B").strftime("%Y%m%d")
        df = pd.DataFrame({
            "trade_date": np.tile(dates, 2),
            "ts_code": ["A"] * 15 + ["B"] * 15,
            "open": [10.0] * 15 + [0.0] * 15,
            "high": [11.0] * 15 + [0.0] * 15,
            "low": [9.0] * 15 + [0.0] * 15,
            "close": [10.0] * 15 + [0.0] * 15,
        })
        factor = IntradayRangeFactor(window=5)
        result = factor.calculate(df)
        second = result[result["ts_code"] == "B"]
        assert second["intraday_range_10d"].isna().all()


# --- Valuation Factor Tests ---

class TestPeFactor:
    def test_calculates_rank(self):
        from src.factors.valuation import PeFactor
        dates = pd.date_range("2023-01-03", periods=5, freq="B").strftime("%Y%m%d")
        rows = []
        for date in dates:
            for code in ["A", "B", "C", "D"]:
                rows.append({
                    "trade_date": date,
                    "ts_code": code,
                    "close": 100.0,
                    "pe_ttm": [10, 20, 30, 40][["A", "B", "C", "D"].index(code)],
                })
        df = pd.DataFrame(rows)
        factor = PeFactor()
        result = factor.calculate(df)
        assert "pe_ttm_rank" in result.columns
        valid = result["pe_ttm_rank"].dropna()
        assert all(valid >= 0)
        assert all(valid <= 100)

    def test_nan_handling(self):
        from src.factors.valuation import PeFactor
        dates = pd.date_range("2023-01-03", periods=5, freq="B").strftime("%Y%m%d")
        rows = []
        for date in dates:
            for code in ["A", "B"]:
                rows.append({
                    "trade_date": date,
                    "ts_code": code,
                    "close": 100.0,
                    "pe_ttm": 15.0 if code == "A" else np.nan,
                })
        df = pd.DataFrame(rows)
        factor = PeFactor()
        result = factor.calculate(df)
        stock_b = result[result["ts_code"] == "B"]
        assert stock_b["pe_ttm_rank"].isna().all()


class TestPbFactor:
    def test_calculates_rank(self):
        from src.factors.valuation import PbFactor
        dates = pd.date_range("2023-01-03", periods=5, freq="B").strftime("%Y%m%d")
        rows = []
        for date in dates:
            for code in ["A", "B", "C"]:
                rows.append({
                    "trade_date": date,
                    "ts_code": code,
                    "close": 100.0,
                    "pb": [1.0, 2.0, 3.0][["A", "B", "C"].index(code)],
                })
        df = pd.DataFrame(rows)
        factor = PbFactor()
        result = factor.calculate(df)
        assert "pb_rank" in result.columns
        valid = result["pb_rank"].dropna()
        assert all(valid >= 0)
        assert all(valid <= 100)

    def test_score_column_name(self):
        from src.factors.valuation import PeFactor, PbFactor
        from src.factors.scorer import standardize_factors
        dates = pd.date_range("2023-01-03", periods=5, freq="B").strftime("%Y%m%d")
        rows = []
        for date in dates:
            for code in ["A", "B", "C"]:
                rows.append({
                    "trade_date": date, "ts_code": code,
                    "close": 100.0, "pe_ttm": 15.0, "pb": 2.0,
                })
        df = pd.DataFrame(rows)
        df = PeFactor().calculate(df)
        df = PbFactor().calculate(df)
        df = standardize_factors(df, ["pe_ttm_rank", "pb_rank"])
        assert "pe_ttm_rank_score" in df.columns
        assert "pb_rank_score" in df.columns


# --- Scorer Tests ---

class TestScorer:
    def test_zscore_standardization(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        from src.factors.scorer import standardize_factors

        factor = MomentumFactor()
        df = factor.calculate(sample_price_df)
        df = standardize_factors(df, ["momentum_20d"])
        assert "momentum_score" in df.columns

    def test_vol_ratio_score_name(self, sample_price_df):
        from src.factors.volume import VolumeFactor
        from src.factors.scorer import standardize_factors

        factor = VolumeFactor()
        df = factor.calculate(sample_price_df)
        df = standardize_factors(df, ["vol_ratio"])
        assert "vol_score" in df.columns

    def test_total_score(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        from src.factors.volume import VolumeFactor
        from src.factors.volatility import VolatilityFactor
        from src.factors.scorer import standardize_factors, compute_total_score

        df = sample_price_df
        for Factor in [MomentumFactor, VolumeFactor, VolatilityFactor]:
            df = Factor().calculate(df)

        df = standardize_factors(df, ["momentum_20d", "vol_ratio", "volatility_20d"])
        df = compute_total_score(df)
        assert "total_score" in df.columns

    def test_select_top_n(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        from src.factors.scorer import standardize_factors, compute_total_score, select_top_n

        df = MomentumFactor().calculate(sample_price_df)
        df = standardize_factors(df, ["momentum_20d"])
        df = compute_total_score(df)

        latest = df["trade_date"].max()
        top = select_top_n(df, latest, n=3)
        assert len(top) <= 3
        assert "ts_code" in top.columns
        assert "total_score" in top.columns


# --- IC Analyzer Tests ---

class TestICAnalyzer:
    def test_future_return(self, sample_price_df):
        from src.factors.ic_analyzer import compute_future_return
        df = compute_future_return(sample_price_df, n_days=5)
        assert "future_return_5d" in df.columns
        # Last 5 rows per stock should be NaN (no future data)
        for code in df["ts_code"].unique():
            stock = df[df["ts_code"] == code]
            assert stock["future_return_5d"].iloc[-5:].isna().all()

    def test_daily_ic(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        from src.factors.ic_analyzer import compute_future_return, compute_daily_ic

        df = MomentumFactor().calculate(sample_price_df)
        df = compute_future_return(df, n_days=5)

        # Pick a date with both factor and future return data available
        valid = df[df["momentum_20d"].notna() & df["future_return_5d"].notna()]
        date = valid["trade_date"].iloc[len(valid) // 2]
        day_df = df[df["trade_date"] == date]
        ic = compute_daily_ic(day_df, "momentum_20d", "future_return_5d")
        assert isinstance(ic, float)
        assert -1 <= ic <= 1

    def test_ic_summary(self):
        from src.factors.ic_analyzer import compute_ic_summary
        ic_series = pd.Series([0.05, 0.03, -0.02, 0.04, 0.01])
        summary = compute_ic_summary(ic_series)
        assert summary["count"] == 5
        assert 0 < summary["win_rate"] < 1
        assert summary["ic_mean"] > 0
        assert summary["ic_direction"] == 1

    def test_ic_summary_negative_direction(self):
        from src.factors.ic_analyzer import compute_ic_summary
        ic_series = pd.Series([-0.05, -0.03, 0.02, -0.04, -0.01])
        summary = compute_ic_summary(ic_series)
        assert summary["ic_direction"] == -1
        # win_rate should be fraction of days where IC sign matches mean sign
        assert summary["win_rate"] == 0.8  # 4 out of 5 are negative

    def test_ic_summary_empty(self):
        from src.factors.ic_analyzer import compute_ic_summary
        ic_series = pd.Series([np.nan, np.nan])
        summary = compute_ic_summary(ic_series)
        assert summary["ic_direction"] == 0
        assert summary["count"] == 0

    def test_evaluate_factor(self, sample_price_df):
        from src.factors.momentum import MomentumFactor
        from src.factors.ic_analyzer import compute_future_return, evaluate_factor

        df = MomentumFactor().calculate(sample_price_df)
        df = compute_future_return(df, n_days=5)
        result = evaluate_factor(df, "momentum_20d", "future_return_5d")

        assert "is_effective" in result
        assert "summary" in result
        assert "verdict" in result
        assert result["verdict"] in ["有效", "无效"]
        assert "ic_direction" in result["summary"]

    def test_evaluate_negative_ic_effective(self):
        from src.factors.ic_analyzer import evaluate_factor
        # Build synthetic data with strong negative cross-sectional IC per day
        np.random.seed(123)
        dates = pd.date_range("2023-01-03", periods=60, freq="B").strftime("%Y%m%d")
        rows = []
        stocks = [f"STK{i:03d}" for i in range(30)]
        for date in dates:
            for code in stocks:
                rank = int(code[3:6])
                # High factor → low return (negative IC)
                factor_val = rank + np.random.randn() * 0.01
                return_val = -rank + np.random.randn() * 0.01
                rows.append({
                    "trade_date": date,
                    "ts_code": code,
                    "factor_neg": factor_val,
                    "future_return": return_val,
                })
        df = pd.DataFrame(rows)
        result = evaluate_factor(df, "factor_neg", "future_return")
        assert result["summary"]["ic_direction"] == -1
        assert result["is_effective"] == True
        assert result["verdict"] == "有效"


# --- Return 20d Factor Tests ---

class TestReturn20dFactor:
    def test_calculates_return(self, sample_price_df):
        from src.factors.return_20d import Return20dFactor
        factor = Return20dFactor()
        result = factor.calculate(sample_price_df)
        assert "return_20d" in result.columns

        # First 20 rows per stock should be NaN
        for code in result["ts_code"].unique():
            stock = result[result["ts_code"] == code]
            assert stock["return_20d"].iloc[:20].isna().all()
            assert stock["return_20d"].iloc[20:].notna().any()

    def test_matches_manual_calculation(self):
        """Verify return_20d matches (close - close_20d_ago) / close_20d_ago."""
        from src.factors.return_20d import Return20dFactor
        dates = pd.date_range("2023-01-03", periods=25, freq="B").strftime("%Y%m%d")
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0,
                  110.0, 111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 119.0,
                  120.0, 121.0, 122.0, 123.0, 124.0]
        df = pd.DataFrame({
            "trade_date": dates,
            "ts_code": "TEST.SH",
            "close": prices,
        })
        factor = Return20dFactor()
        result = factor.calculate(df)
        # Row 20 (index 20): close=120, close 20 ago=100 → (120-100)/100 = 0.2
        row_20 = result.iloc[20]
        assert abs(row_20["return_20d"] - 0.2) < 1e-10


# --- Trend 60d Factor Tests ---

class TestTrend60dFactor:
    def test_calculates_trend(self, sample_price_df):
        from src.factors.trend_60d import Trend60dFactor
        factor = Trend60dFactor()
        result = factor.calculate(sample_price_df)
        assert "trend_60d" in result.columns

        # First 64 rows per stock should be NaN (60 for MA60 + 5 for slope shift)
        for code in result["ts_code"].unique():
            stock = result[result["ts_code"] == code]
            assert stock["trend_60d"].iloc[:64].isna().all()
            assert stock["trend_60d"].iloc[64:].notna().any()

    def test_uptrend_positive(self):
        """Steadily rising prices should produce positive trend_60d."""
        from src.factors.trend_60d import Trend60dFactor
        dates = pd.date_range("2023-01-03", periods=80, freq="B").strftime("%Y%m%d")
        prices = [100.0 + i * 0.5 for i in range(80)]
        df = pd.DataFrame({
            "trade_date": dates,
            "ts_code": "UP.SH",
            "close": prices,
        })
        factor = Trend60dFactor()
        result = factor.calculate(df)
        valid = result["trend_60d"].dropna()
        assert all(valid > 0)

    def test_flat_trend_near_zero(self):
        """Flat prices should produce trend_60d near zero."""
        from src.factors.trend_60d import Trend60dFactor
        dates = pd.date_range("2023-01-03", periods=80, freq="B").strftime("%Y%m%d")
        df = pd.DataFrame({
            "trade_date": dates,
            "ts_code": "FLAT.SH",
            "close": [100.0] * 80,
        })
        factor = Trend60dFactor()
        result = factor.calculate(df)
        valid = result["trend_60d"].dropna()
        assert all(abs(v) < 1e-10 for v in valid)


# --- New Factor Registration Tests ---

class TestNewFactorRegistration:
    def test_return_20d_registered(self):
        import src.factors.return_20d
        from src.factors.base import get_registered_factors
        registry = get_registered_factors()
        assert "return_20d" in registry
        assert registry["return_20d"].category == "量价类"

    def test_trend_60d_registered(self):
        import src.factors.trend_60d
        from src.factors.base import get_registered_factors
        registry = get_registered_factors()
        assert "trend_60d" in registry
        assert registry["trend_60d"].category == "量价类"

    def test_total_factor_count(self):
        """Verify total registered factors is 11 (9 original + 2 new)."""
        import src.factors.momentum
        import src.factors.volume
        import src.factors.volatility
        import src.factors.rsi
        import src.factors.ma_deviation
        import src.factors.turnover
        import src.factors.intraday_range
        import src.factors.valuation
        import src.factors.return_20d
        import src.factors.trend_60d
        from src.factors.base import get_registered_factors
        registry = get_registered_factors()
        assert len(registry) == 11

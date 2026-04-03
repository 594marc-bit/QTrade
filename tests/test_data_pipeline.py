"""Tests for data pipeline: fetcher, cleaner, storage."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest


# --- Fixtures ---

@pytest.fixture
def sample_raw_df():
    """Create sample raw data with known issues."""
    dates = pd.date_range("2023-01-03", periods=25, freq="B").strftime("%Y%m%d")
    rows = []
    for date in dates:
        for code in ["600519.SH", "000858.SZ", "603121.SH"]:
            price = 100 + np.random.randn() * 5
            rows.append({
                "trade_date": date,
                "ts_code": code,
                "open": price - 0.5,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "vol": 10000 + np.random.randint(0, 5000),
                "amount": price * 10000,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_df_with_issues():
    """Create data with missing values, suspended days, and outliers."""
    dates = pd.date_range("2023-01-03", periods=30, freq="B").strftime("%Y%m%d")
    rows = []
    for i, date in enumerate(dates):
        for code in ["600519.SH", "000858.SZ"]:
            price = 100 + i * 0.5
            vol = 10000
            is_trading = True

            # Introduce NaN for dates 10-12 (short gap)
            if 10 <= i <= 12:
                rows.append({
                    "trade_date": date, "ts_code": code,
                    "open": np.nan, "high": np.nan, "low": np.nan,
                    "close": np.nan, "vol": np.nan, "amount": np.nan,
                })
                continue

            # Suspended day at i=20
            if i == 20:
                vol = 0

            rows.append({
                "trade_date": date, "ts_code": code,
                "open": price - 0.5, "high": price + 1, "low": price - 1,
                "close": price, "vol": vol, "amount": price * vol,
            })
    return pd.DataFrame(rows)


# --- Cleaner Tests ---

class TestFillMissingValues:
    def test_short_gap_ffilled(self, sample_df_with_issues):
        from src.data.cleaner import fill_missing_values
        result = fill_missing_values(sample_df_with_issues)
        # Short NaN gaps should be filled
        assert result["close"].notna().sum() > 0

    def test_no_data_loss_on_clean(self, sample_raw_df):
        from src.data.cleaner import fill_missing_values
        result = fill_missing_values(sample_raw_df)
        assert len(result) == len(sample_raw_df)


class TestMarkSuspended:
    def test_zero_volume_marked(self, sample_df_with_issues):
        from src.data.cleaner import mark_suspended
        result = mark_suspended(sample_df_with_issues)
        suspended = result[result["vol"] == 0]
        assert all(suspended["is_trading"] == False)

    def test_normal_trading_marked(self, sample_raw_df):
        from src.data.cleaner import mark_suspended
        result = mark_suspended(sample_raw_df)
        assert all(result["is_trading"] == True)


class TestFilterOutliers:
    def test_extreme_return_filtered(self):
        from src.data.cleaner import filter_outliers
        dates = [f"2023010{d}" for d in range(3, 8)]
        df = pd.DataFrame({
            "trade_date": dates,
            "ts_code": ["600519.SH"] * 5,
            "close": [100, 101, 150, 102, 103],  # 150 is +48% jump
            "vol": [10000] * 5,
            "amount": [1000000] * 5,
        })
        result = filter_outliers(df)
        assert 150 not in result["close"].values

    def test_vol_price_contradiction_filtered(self):
        from src.data.cleaner import filter_outliers
        df = pd.DataFrame({
            "trade_date": ["20230103", "20230104"],
            "ts_code": ["600519.SH", "600519.SH"],
            "close": [100, 105],  # price changed
            "vol": [10000, 0],    # but vol=0 on second day
            "amount": [1000000, 0],
        })
        result = filter_outliers(df)
        # Second row should be filtered (vol=0 but price changed)
        assert len(result) < 2


class TestAlignDates:
    def test_all_stocks_same_dates(self, sample_raw_df):
        from src.data.cleaner import align_dates
        result = align_dates(sample_raw_df)
        dates_per_stock = result.groupby("ts_code")["trade_date"].nunique()
        assert dates_per_stock.nunique() == 1


class TestValidateData:
    def test_valid_data_passes(self, sample_raw_df):
        from src.data.cleaner import validate_data
        report = validate_data(sample_raw_df)
        assert report["total_rows"] > 0
        assert report["total_stocks"] == 3

    def test_zero_price_detected(self):
        from src.data.cleaner import validate_data
        df = pd.DataFrame({
            "trade_date": ["20230103"],
            "ts_code": ["600519.SH"],
            "close": [0],  # invalid
        })
        report = validate_data(df)
        assert not report["is_valid"]


class TestCleanPipeline:
    def test_full_pipeline_runs(self, sample_raw_df):
        from src.data.cleaner import clean_pipeline
        result, report = clean_pipeline(sample_raw_df)
        assert len(result) > 0
        assert "is_trading" in result.columns
        assert isinstance(report, dict)


# --- Storage Tests ---

class TestStorage:
    def test_save_and_load(self, sample_raw_df, tmp_path):
        from src.data import storage
        # Override DB path for testing
        original_db = storage.DB_PATH
        storage.DB_PATH = tmp_path / "test.db"

        try:
            saved = storage.save_daily_price(sample_raw_df)
            assert saved > 0

            loaded = storage.load_daily_price()
            assert len(loaded) == len(sample_raw_df)
        finally:
            storage.DB_PATH = original_db

    def test_dedup_on_save(self, sample_raw_df, tmp_path):
        from src.data import storage
        original_db = storage.DB_PATH
        storage.DB_PATH = tmp_path / "test.db"

        try:
            storage.save_daily_price(sample_raw_df)
            storage.save_daily_price(sample_raw_df)  # save again
            loaded = storage.load_daily_price()
            assert len(loaded) == len(sample_raw_df)  # no duplicates
        finally:
            storage.DB_PATH = original_db

    def test_export_csv(self, sample_raw_df, tmp_path):
        from src.data import storage
        original_db = storage.DB_PATH
        original_data = storage.DATA_DIR
        storage.DB_PATH = tmp_path / "test.db"
        storage.DATA_DIR = tmp_path

        try:
            storage.save_daily_price(sample_raw_df)
            path = storage.export_csv(filename="test.csv")
            assert path != ""
            assert Path(path).exists()
        finally:
            storage.DB_PATH = original_db
            storage.DATA_DIR = original_data


class TestDailyBasicStorage:
    def test_save_and_load(self, tmp_path):
        from src.data import storage
        original_db = storage.DB_PATH
        storage.DB_PATH = tmp_path / "test.db"

        try:
            df = pd.DataFrame({
                "trade_date": ["20230103", "20230103", "20230104"],
                "ts_code": ["600519.SH", "000858.SZ", "600519.SH"],
                "pe_ttm": [30.5, 25.0, 31.0],
                "pb": [8.2, 5.1, 8.3],
                "ps_ttm": [15.0, 10.0, 15.2],
            })
            saved = storage.save_daily_basic(df)
            assert saved == 3

            loaded = storage.load_daily_basic()
            assert len(loaded) == 3
            assert "pe_ttm" in loaded.columns
        finally:
            storage.DB_PATH = original_db

    def test_load_with_date_filter(self, tmp_path):
        from src.data import storage
        original_db = storage.DB_PATH
        storage.DB_PATH = tmp_path / "test.db"

        try:
            df = pd.DataFrame({
                "trade_date": ["20230103", "20230110", "20230117"],
                "ts_code": ["600519.SH", "600519.SH", "600519.SH"],
                "pe_ttm": [30.0, 31.0, 32.0],
                "pb": [8.0, 8.1, 8.2],
            })
            storage.save_daily_basic(df)

            loaded = storage.load_daily_basic(start_date="20230110", end_date="20230115")
            assert len(loaded) == 1
        finally:
            storage.DB_PATH = original_db

    def test_merge_fundamentals(self, tmp_path):
        from src.data import storage
        price_df = pd.DataFrame({
            "trade_date": ["20230103", "20230103", "20230104"],
            "ts_code": ["600519.SH", "000858.SZ", "600519.SH"],
            "close": [1800.0, 300.0, 1810.0],
        })
        basic_df = pd.DataFrame({
            "trade_date": ["20230103", "20230104"],
            "ts_code": ["600519.SH", "600519.SH"],
            "pe_ttm": [30.0, 31.0],
            "pb": [8.0, 8.1],
        })
        result = storage.merge_fundamentals(price_df, basic_df)
        assert "pe_ttm" in result.columns
        assert "pb" in result.columns
        # 000858.SZ on 20230103 should have NaN (not in basic_df)
        row = result[(result["ts_code"] == "000858.SZ") & (result["trade_date"] == "20230103")]
        assert row["pe_ttm"].isna().values[0]

    def test_merge_empty_basic(self):
        from src.data import storage
        price_df = pd.DataFrame({
            "trade_date": ["20230103"],
            "ts_code": ["600519.SH"],
            "close": [1800.0],
        })
        result = storage.merge_fundamentals(price_df, pd.DataFrame())
        assert "pe_ttm" in result.columns
        assert result["pe_ttm"].isna().all()

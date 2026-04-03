"""Tests for Tushare fetcher module."""

from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pandas as pd
import pytest


class TestTushareInit:
    def test_init_raises_without_token(self):
        """Should raise ValueError when token is empty."""
        with patch("src.data.tushare_fetcher.TUSHARE_TOKEN", ""):
            from src.data.tushare_fetcher import _init_pro
            with pytest.raises(ValueError, match="Tushare token not configured"):
                _init_pro()

    def test_init_success_with_token(self):
        """Should return pro_api when token is valid."""
        with patch("src.data.tushare_fetcher.TUSHARE_TOKEN", "test_token_123"):
            with patch("src.data.tushare_fetcher.ts") as mock_ts:
                mock_ts.pro_api.return_value = MagicMock()
                from src.data.tushare_fetcher import _init_pro
                pro = _init_pro()
                assert pro is not None
                mock_ts.set_token.assert_called_once_with("test_token_123")


class TestGetIndexConstituents:
    def test_returns_correct_format(self):
        """Should delegate to AKShare and return ts_code and name columns."""
        mock_result = pd.DataFrame({
            "ts_code": ["600519.SH", "000858.SZ"],
            "name": ["贵州茅台", "五粮液"],
        })

        with patch("src.data.akshare_fetcher.get_index_constituents", return_value=mock_result), \
             patch("src.data.tushare_fetcher.DATA_DIR") as mock_dir:
            import tempfile
            from pathlib import Path
            tmp = tempfile.mkdtemp()
            type(mock_dir).__truediv__ = lambda self, key: Path(tmp) / key
            from src.data.tushare_fetcher import get_index_constituents
            result = get_index_constituents()

        assert "ts_code" in result.columns
        assert "name" in result.columns
        assert len(result) == 2

    def test_uses_cache(self, tmp_path):
        """Should return cached data if within expiry."""
        import pickle
        from src.data import tushare_fetcher

        cache_data = pd.DataFrame({"ts_code": ["600519.SH"], "name": ["贵州茅台"]})
        cache_path = tmp_path / "hs300_constituents_tushare.pkl"
        with open(cache_path, "wb") as f:
            pickle.dump(cache_data, f)

        with patch.object(tushare_fetcher, "DATA_DIR", tmp_path):
            from src.data.tushare_fetcher import get_index_constituents
            result = get_index_constituents()

        assert len(result) == 1
        assert result["ts_code"].iloc[0] == "600519.SH"


class TestGetStockDaily:
    def test_returns_standard_columns(self):
        """Should return DataFrame with standard column names."""
        mock_pro = MagicMock()
        mock_pro.daily.return_value = pd.DataFrame({
            "trade_date": ["20260320", "20260321", "20260322"],
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "vol": [10000, 11000, 12000],
            "amount": [1005000, 1116500, 1230000],
        })
        mock_pro.adj_factor.return_value = pd.DataFrame({
            "trade_date": ["20260320", "20260321", "20260322"],
            "adj_factor": [1.0, 1.0, 1.0],
        })

        with patch("src.data.tushare_fetcher._get_pro", return_value=mock_pro):
            from src.data.tushare_fetcher import get_stock_daily
            result = get_stock_daily("600519.SH")

        assert "trade_date" in result.columns
        assert "ts_code" in result.columns
        assert "close" in result.columns
        assert result["ts_code"].iloc[0] == "600519.SH"

    def test_forward_adjustment(self):
        """Should apply forward adjustment (qfq) correctly."""
        mock_pro = MagicMock()
        mock_pro.daily.return_value = pd.DataFrame({
            "trade_date": ["20260320", "20260321"],
            "open": [100.0, 50.0],  # Stock split: price halved
            "high": [101.0, 51.0],
            "low": [99.0, 49.0],
            "close": [100.5, 50.5],
            "vol": [10000, 20000],
            "amount": [1005000, 1010000],
        })
        mock_pro.adj_factor.return_value = pd.DataFrame({
            "trade_date": ["20260320", "20260321"],
            "adj_factor": [1.0, 2.0],  # 2x factor = 2:1 split
        })

        with patch("src.data.tushare_fetcher._get_pro", return_value=mock_pro):
            from src.data.tushare_fetcher import get_stock_daily
            result = get_stock_daily("600519.SH", adjust="qfq")

        # After qfq: day1 close = 100.5 * 1.0/2.0 = 50.25, day2 close = 50.5 * 2.0/2.0 = 50.5
        assert abs(result["close"].iloc[0] - 50.25) < 0.01
        assert abs(result["close"].iloc[1] - 50.50) < 0.01

    def test_empty_on_failure(self):
        """Should return empty DataFrame on API failure."""
        mock_pro = MagicMock()
        mock_pro.daily.side_effect = Exception("API Error")

        with patch("src.data.tushare_fetcher._get_pro", return_value=mock_pro):
            with patch("src.data.tushare_fetcher.TUSHARE_FETCH_INTERVAL", 0):
                from src.data.tushare_fetcher import get_stock_daily
                result = get_stock_daily("600519.SH")

        assert result.empty


class TestGetStocksBatch:
    def test_batch_with_save(self):
        """Should batch fetch and periodically save."""
        mock_daily_data = pd.DataFrame({
            "trade_date": ["20260320"],
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "vol": [10000], "amount": [1005000],
        })
        mock_adj_data = pd.DataFrame({
            "trade_date": ["20260320"], "adj_factor": [1.0],
        })
        mock_pro = MagicMock()
        mock_pro.daily.return_value = mock_daily_data
        mock_pro.adj_factor.return_value = mock_adj_data

        with patch("src.data.tushare_fetcher._get_pro", return_value=mock_pro):
            with patch("src.data.tushare_fetcher.TUSHARE_FETCH_INTERVAL", 0):
                from src.data.tushare_fetcher import get_stocks_batch
                result = get_stocks_batch(
                    ["600519.SH", "000858.SZ"], save_every=999
                )

        assert len(result) == 2
        assert "ts_code" in result.columns

    def test_batch_stops_on_consecutive_failures(self):
        """Should stop after 10 consecutive failures."""
        mock_pro = MagicMock()
        mock_pro.daily.side_effect = Exception("API Error")

        codes = [f"60{i:04d}.SH" for i in range(20)]

        with patch("src.data.tushare_fetcher._get_pro", return_value=mock_pro):
            with patch("src.data.tushare_fetcher.TUSHARE_FETCH_INTERVAL", 0):
                from src.data.tushare_fetcher import get_stocks_batch
                result = get_stocks_batch(codes, save_every=999)

        assert result.empty


class TestGetStockDailyIncremental:
    def test_incremental_fetch(self):
        """Should only fetch data after last existing date."""
        existing = pd.DataFrame({
            "trade_date": ["20260320", "20260321"],
            "ts_code": ["600519.SH", "600519.SH"],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "vol": [10000, 11000],
            "amount": [1005000, 1116500],
        })

        new_data = pd.DataFrame({
            "trade_date": ["20260322"],
            "ts_code": ["600519.SH"],
            "open": [102.0], "high": [103.0], "low": [101.0],
            "close": [102.5], "vol": [12000], "amount": [1230000],
        })

        with patch("src.data.akshare_fetcher.get_stock_daily", return_value=new_data):
            with patch("src.data.akshare_fetcher.FETCH_INTERVAL", 0):
                from src.data.akshare_fetcher import get_stock_daily_incremental
                result = get_stock_daily_incremental("600519.SH", existing)

        assert len(result) == 3

    def test_no_fetch_if_up_to_date(self):
        """Should return existing data if already up to date."""
        existing = pd.DataFrame({
            "trade_date": ["20261231"],
            "close": [100.0],
            "ts_code": ["600519.SH"],
            "open": [100.0], "high": [100.0], "low": [100.0],
            "vol": [1000], "amount": [100000],
        })

        with patch("src.data.akshare_fetcher.END_DATE", "20261231"):
            from src.data.akshare_fetcher import get_stock_daily_incremental
            result = get_stock_daily_incremental("600519.SH", existing)

        assert len(result) == 1

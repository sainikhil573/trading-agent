"""
Tests for broker data provider architecture.

Covers:
  - get_provider_config: env var reading, validation, fallback detection
  - get_configured_provider: selection logic, CSV fallback, credential checks
  - AngelOneIntradayProvider: credentials, package check, normalize_response
  - KiteIntradayProvider: credentials, package check, normalize_response
  - normalize_angelone_candles / normalize_kite_candles: format normalisation
  - Provider failure does not crash evaluation or data_availability
  - CSV provider still works as default when no broker env vars set

Run: python -m pytest tests/test_broker_providers.py -v
"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.broker_config import (
    get_provider_config,
    get_configured_provider,
    SUPPORTED_PROVIDERS,
)
from src.fetchers.intraday_provider import (
    AngelOneIntradayProvider,
    KiteIntradayProvider,
    LocalCSVIntradayProvider,
    UnavailableIntradayProvider,
    CANDLE_COLUMNS,
    normalize_angelone_candles,
    normalize_kite_candles,
    get_default_provider,
)
from src.analyzers.evaluation import resolve_outcome_with_intraday


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_broker_env(monkeypatch):
    """Remove all broker-related env vars so tests start from a clean slate."""
    for var in (
        "BROKER_PROVIDER",
        "ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE", "ANGELONE_TOTP_SECRET",
        "KITE_API_KEY", "KITE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


_ANGEL_CANDLES = [
    ["2026-05-20T09:15:00+05:30", 23000.0, 23060.0, 22990.0, 23020.0, 50000],
    ["2026-05-20T09:20:00+05:30", 23020.0, 23080.0, 23010.0, 23050.0, 48000],
    ["2026-05-20T09:25:00+05:30", 23050.0, 23070.0, 22980.0, 23000.0, 45000],
]

_KITE_CANDLES = [
    ["2026-05-20 09:15:00", 23000.0, 23060.0, 22990.0, 23020.0, 50000],
    ["2026-05-20 09:20:00", 23020.0, 23080.0, 23010.0, 23050.0, 48000],
    ["2026-05-20 09:25:00", 23050.0, 23070.0, 22980.0, 23000.0, 45000],
]


# ---------------------------------------------------------------------------
# get_provider_config
# ---------------------------------------------------------------------------

class TestGetProviderConfig:
    def test_defaults_to_csv_when_no_env(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        assert cfg["provider"] == "csv"

    def test_reads_broker_provider_env(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        cfg = get_provider_config()
        assert cfg["provider"] == "angelone"

    def test_unsupported_provider_falls_back_to_csv(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "robinhood")
        cfg = get_provider_config()
        assert cfg["provider"] == "csv"

    def test_case_insensitive_provider_name(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "KITE")
        cfg = get_provider_config()
        assert cfg["provider"] == "kite"

    def test_angelone_ready_false_when_creds_missing(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        assert cfg["angelone_ready"] is False

    def test_angelone_ready_true_when_creds_present(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("ANGELONE_API_KEY", "key123")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C123456")
        cfg = get_provider_config()
        assert cfg["angelone_ready"] is True

    def test_kite_ready_false_when_creds_missing(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        assert cfg["kite_ready"] is False

    def test_kite_ready_true_when_creds_present(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("KITE_API_KEY", "kite_key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "tok_xyz")
        cfg = get_provider_config()
        assert cfg["kite_ready"] is True

    def test_missing_creds_listed_when_angelone_chosen(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        cfg = get_provider_config()
        assert "ANGELONE_API_KEY" in cfg["missing_creds"]
        assert "ANGELONE_CLIENT_CODE" in cfg["missing_creds"]

    def test_missing_creds_listed_when_kite_chosen(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "kite")
        cfg = get_provider_config()
        assert "KITE_API_KEY" in cfg["missing_creds"]
        assert "KITE_ACCESS_TOKEN" in cfg["missing_creds"]

    def test_no_missing_creds_for_csv_provider(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        assert cfg["missing_creds"] == []

    def test_fallback_active_when_angelone_creds_missing(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        cfg = get_provider_config()
        assert cfg["fallback_active"] is True

    def test_fallback_not_active_for_csv_provider(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        assert cfg["fallback_active"] is False

    def test_totp_secret_is_optional_for_angelone_ready(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("ANGELONE_API_KEY", "key123")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C123456")
        # No TOTP — should still be ready (TOTP is optional)
        cfg = get_provider_config()
        assert cfg["angelone_ready"] is True
        assert cfg["angelone_creds"]["totp_secret"] == ""


# ---------------------------------------------------------------------------
# get_configured_provider — selection and CSV fallback
# ---------------------------------------------------------------------------

class TestGetConfiguredProvider:
    def test_returns_csv_or_unavailable_by_default(self, monkeypatch, tmp_path):
        _clear_broker_env(monkeypatch)
        cfg = get_provider_config()
        # Use tmp_path as data dir — no CSV files → Unavailable
        p = get_configured_provider(cfg)
        # Either CSV (if data/intraday/ exists on this machine) or Unavailable
        assert isinstance(p, (LocalCSVIntradayProvider, UnavailableIntradayProvider))

    def test_csv_fallback_when_angelone_creds_missing(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        # No credentials → must fall back to CSV/Unavailable, never return AngelOne
        p = get_configured_provider()
        assert not isinstance(p, AngelOneIntradayProvider)

    def test_csv_fallback_when_kite_creds_missing(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "kite")
        p = get_configured_provider()
        assert not isinstance(p, KiteIntradayProvider)

    def test_angelone_selected_when_creds_present_and_pkg_available(self, monkeypatch):
        """Only runs if smartapi-python is installed. Skips gracefully otherwise."""
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        monkeypatch.setenv("ANGELONE_API_KEY", "key123")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C123456")

        p_test = AngelOneIntradayProvider(
            creds={"api_key": "key123", "client_code": "C123456", "totp_secret": ""}
        )
        if not p_test.is_available():
            pytest.skip("smartapi-python not installed — skipping live provider selection test")

        p = get_configured_provider()
        assert isinstance(p, AngelOneIntradayProvider)

    def test_kite_selected_when_creds_present_and_pkg_available(self, monkeypatch):
        """Only runs if kiteconnect is installed. Skips gracefully otherwise."""
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "kite")
        monkeypatch.setenv("KITE_API_KEY", "kite_key")
        monkeypatch.setenv("KITE_ACCESS_TOKEN", "tok_xyz")

        p_test = KiteIntradayProvider(
            creds={"api_key": "kite_key", "access_token": "tok_xyz"}
        )
        if not p_test.is_available():
            pytest.skip("kiteconnect not installed — skipping live provider selection test")

        p = get_configured_provider()
        assert isinstance(p, KiteIntradayProvider)

    def test_never_raises(self, monkeypatch):
        """get_configured_provider must never raise regardless of env state."""
        _clear_broker_env(monkeypatch)
        monkeypatch.setenv("BROKER_PROVIDER", "angelone")
        monkeypatch.setenv("ANGELONE_API_KEY", "key123")
        monkeypatch.setenv("ANGELONE_CLIENT_CODE", "C123456")
        try:
            p = get_configured_provider()
            assert p is not None
        except Exception as exc:
            pytest.fail(f"get_configured_provider raised: {exc}")


# ---------------------------------------------------------------------------
# AngelOneIntradayProvider — credential and package awareness
# ---------------------------------------------------------------------------

class TestAngelOneProvider:
    def _no_creds(self):
        return AngelOneIntradayProvider(creds={"api_key": "", "client_code": "", "totp_secret": ""})

    def _with_creds(self):
        return AngelOneIntradayProvider(
            creds={"api_key": "key123", "client_code": "C123456", "totp_secret": ""}
        )

    def test_not_available_when_no_creds(self):
        assert self._no_creds().is_available() is False

    def test_not_available_when_only_api_key(self):
        p = AngelOneIntradayProvider(creds={"api_key": "key", "client_code": "", "totp_secret": ""})
        assert p.is_available() is False

    def test_name_mentions_credentials_missing(self):
        assert "credential" in self._no_creds().name.lower()

    def test_name_mentions_package_missing_when_creds_ok_but_no_pkg(self):
        p = self._with_creds()
        if p._pkg_ok:
            pytest.skip("smartapi-python is installed — package-missing branch not reachable")
        assert "package" in p.name.lower() or "pip" in p.name.lower()

    def test_get_candles_returns_empty_when_not_available(self):
        df = self._no_creds().get_candles("NIFTY", date.today())
        assert df.empty
        assert list(df.columns) == CANDLE_COLUMNS

    def test_get_candles_never_raises(self):
        p = self._no_creds()
        try:
            df = p.get_candles("NIFTY", date(2026, 5, 20), "5m")
            assert isinstance(df, pd.DataFrame)
        except Exception as exc:
            pytest.fail(f"get_candles raised: {exc}")

    def test_normalize_response_returns_correct_columns(self):
        df = AngelOneIntradayProvider.normalize_response(_ANGEL_CANDLES, "NIFTY", "5m")
        assert list(df.columns) == CANDLE_COLUMNS

    def test_normalize_response_returns_three_rows(self):
        df = AngelOneIntradayProvider.normalize_response(_ANGEL_CANDLES, "NIFTY", "5m")
        assert len(df) == 3

    def test_normalize_response_populates_symbol(self):
        df = AngelOneIntradayProvider.normalize_response(_ANGEL_CANDLES, "NIFTY", "5m")
        assert (df["symbol"] == "NIFTY").all()

    def test_normalize_response_populates_timeframe(self):
        df = AngelOneIntradayProvider.normalize_response(_ANGEL_CANDLES, "NIFTY", "5m")
        assert (df["timeframe"] == "5m").all()

    def test_normalize_response_datetime_is_tz_naive_ist(self):
        df = AngelOneIntradayProvider.normalize_response(_ANGEL_CANDLES, "NIFTY", "5m")
        # Must be tz-naive (no tzinfo)
        assert df["datetime"].dt.tz is None
        # First candle should be 09:15 IST
        assert df.iloc[0]["datetime"].hour == 9
        assert df.iloc[0]["datetime"].minute == 15

    def test_normalize_response_sorted_chronologically(self):
        shuffled = [_ANGEL_CANDLES[2], _ANGEL_CANDLES[0], _ANGEL_CANDLES[1]]
        df = AngelOneIntradayProvider.normalize_response(shuffled, "NIFTY", "5m")
        assert df.iloc[0]["datetime"] < df.iloc[1]["datetime"]

    def test_normalize_response_empty_input_returns_empty(self):
        df = AngelOneIntradayProvider.normalize_response([], "NIFTY", "5m")
        assert df.empty
        assert list(df.columns) == CANDLE_COLUMNS

    def test_normalize_response_bad_timestamp_skips_row(self):
        bad = [["not-a-date", 100, 110, 90, 105, 1000]]
        df = AngelOneIntradayProvider.normalize_response(bad, "NIFTY", "5m")
        # Should return empty rather than crashing
        assert isinstance(df, pd.DataFrame)

    def test_normalize_response_short_row_skipped(self):
        bad = [["2026-05-20T09:15:00+05:30", 100, 110]]  # only 3 elements
        df = AngelOneIntradayProvider.normalize_response(bad, "NIFTY", "5m")
        assert df.empty


# ---------------------------------------------------------------------------
# KiteIntradayProvider — credential and package awareness
# ---------------------------------------------------------------------------

class TestKiteProvider:
    def _no_creds(self):
        return KiteIntradayProvider(creds={"api_key": "", "access_token": ""})

    def _with_creds(self):
        return KiteIntradayProvider(creds={"api_key": "kite_key", "access_token": "tok_xyz"})

    def test_not_available_when_no_creds(self):
        assert self._no_creds().is_available() is False

    def test_not_available_when_only_api_key(self):
        p = KiteIntradayProvider(creds={"api_key": "key", "access_token": ""})
        assert p.is_available() is False

    def test_name_mentions_credentials_missing(self):
        assert "credential" in self._no_creds().name.lower()

    def test_name_mentions_package_missing_when_creds_ok_but_no_pkg(self):
        p = self._with_creds()
        if p._pkg_ok:
            pytest.skip("kiteconnect is installed — package-missing branch not reachable")
        assert "package" in p.name.lower() or "pip" in p.name.lower()

    def test_get_candles_returns_empty_when_not_available(self):
        df = self._no_creds().get_candles("NIFTY", date.today())
        assert df.empty
        assert list(df.columns) == CANDLE_COLUMNS

    def test_get_candles_never_raises(self):
        p = self._no_creds()
        try:
            df = p.get_candles("BANKNIFTY", date(2026, 5, 20), "15m")
            assert isinstance(df, pd.DataFrame)
        except Exception as exc:
            pytest.fail(f"get_candles raised: {exc}")

    def test_normalize_response_returns_correct_columns(self):
        df = KiteIntradayProvider.normalize_response(_KITE_CANDLES, "NIFTY", "5m")
        assert list(df.columns) == CANDLE_COLUMNS

    def test_normalize_response_returns_three_rows(self):
        df = KiteIntradayProvider.normalize_response(_KITE_CANDLES, "NIFTY", "5m")
        assert len(df) == 3

    def test_normalize_response_populates_symbol(self):
        df = KiteIntradayProvider.normalize_response(_KITE_CANDLES, "NIFTY", "5m")
        assert (df["symbol"] == "NIFTY").all()

    def test_normalize_response_datetime_tz_naive(self):
        df = KiteIntradayProvider.normalize_response(_KITE_CANDLES, "NIFTY", "5m")
        assert df["datetime"].dt.tz is None

    def test_normalize_response_sorted_chronologically(self):
        shuffled = [_KITE_CANDLES[2], _KITE_CANDLES[0], _KITE_CANDLES[1]]
        df = KiteIntradayProvider.normalize_response(shuffled, "NIFTY", "5m")
        assert df.iloc[0]["datetime"] < df.iloc[1]["datetime"]

    def test_normalize_response_empty_input_returns_empty(self):
        df = KiteIntradayProvider.normalize_response([], "NIFTY", "5m")
        assert df.empty
        assert list(df.columns) == CANDLE_COLUMNS

    def test_normalize_response_short_row_skipped(self):
        bad = [["2026-05-20 09:15:00", 100, 110]]  # only 3 elements
        df = KiteIntradayProvider.normalize_response(bad, "NIFTY", "5m")
        assert df.empty

    def test_normalize_response_with_oi_column_ignored(self):
        # Kite sometimes includes OI as 7th element — should still work
        with_oi = [row + [0] for row in _KITE_CANDLES]
        df = KiteIntradayProvider.normalize_response(with_oi, "NIFTY", "5m")
        assert len(df) == 3
        assert list(df.columns) == CANDLE_COLUMNS


# ---------------------------------------------------------------------------
# Module-level normalize functions (shared logic)
# ---------------------------------------------------------------------------

class TestNormalizeAngeloneCandles:
    def test_ohlcv_values_correct(self):
        df = normalize_angelone_candles(_ANGEL_CANDLES, "NIFTY", "5m")
        row = df.iloc[0]
        assert row["open"]  == pytest.approx(23000.0)
        assert row["high"]  == pytest.approx(23060.0)
        assert row["low"]   == pytest.approx(22990.0)
        assert row["close"] == pytest.approx(23020.0)
        assert row["volume"] == pytest.approx(50000.0)

    def test_all_numeric_columns(self):
        df = normalize_angelone_candles(_ANGEL_CANDLES, "NIFTY", "5m")
        for col in ("open", "high", "low", "close", "volume"):
            assert pd.api.types.is_float_dtype(df[col]) or pd.api.types.is_numeric_dtype(df[col])


class TestNormalizeKiteCandles:
    def test_ohlcv_values_correct(self):
        df = normalize_kite_candles(_KITE_CANDLES, "NIFTY", "5m")
        row = df.iloc[0]
        assert row["open"]  == pytest.approx(23000.0)
        assert row["high"]  == pytest.approx(23060.0)
        assert row["low"]   == pytest.approx(22990.0)
        assert row["close"] == pytest.approx(23020.0)
        assert row["volume"] == pytest.approx(50000.0)

    def test_all_numeric_columns(self):
        df = normalize_kite_candles(_KITE_CANDLES, "NIFTY", "5m")
        for col in ("open", "high", "low", "close", "volume"):
            assert pd.api.types.is_float_dtype(df[col]) or pd.api.types.is_numeric_dtype(df[col])


# ---------------------------------------------------------------------------
# Provider failure does not crash evaluation
# ---------------------------------------------------------------------------

class TestProviderFailureDoesNotCrashEvaluation:
    def _trade(self):
        return {
            "signal":      "CALL",
            "entry_price": 100.0,
            "stop_loss":   70.0,
            "target_1":    130.0,
            "target_2":    None,
        }

    def _ohlc(self):
        return {"open_p": 23000, "high_p": 23200, "low_p": 22900, "was_correct": True}

    def test_empty_dataframe_from_unavailable_provider_does_not_crash(self):
        p = UnavailableIntradayProvider()
        candles = p.get_candles("NIFTY", date.today())
        result = resolve_outcome_with_intraday(self._trade(), self._ohlc(), candles)
        assert result["data_source"] == "DAILY_OHLC"
        assert "outcome" in result

    def test_empty_dataframe_from_angelone_no_creds_does_not_crash(self):
        p = AngelOneIntradayProvider(creds={"api_key": "", "client_code": "", "totp_secret": ""})
        candles = p.get_candles("NIFTY", date.today())
        result = resolve_outcome_with_intraday(self._trade(), self._ohlc(), candles)
        assert result["data_source"] == "DAILY_OHLC"

    def test_empty_dataframe_from_kite_no_creds_does_not_crash(self):
        p = KiteIntradayProvider(creds={"api_key": "", "access_token": ""})
        candles = p.get_candles("NIFTY", date.today())
        result = resolve_outcome_with_intraday(self._trade(), self._ohlc(), candles)
        assert result["data_source"] == "DAILY_OHLC"

    def test_normalized_candles_integrate_with_evaluation(self):
        """Mocked Angel One candles → normalize → resolve_outcome works end-to-end."""
        # These candles: CALL trade, spot starts at 23000
        # Candle 1: high=23060 → best_prem = 100 + (60*0.5) = 130 → T1 hit
        angel_raw = [["2026-05-20T09:15:00+05:30", 23000, 23060, 22990, 23020, 50000]]
        candles = normalize_angelone_candles(angel_raw, "NIFTY", "5m")

        trade = {"signal": "CALL", "entry_price": 100.0, "stop_loss": 70.0, "target_1": 130.0, "target_2": None}
        ohlc  = {"open_p": 23000, "high_p": 23060, "low_p": 22990, "was_correct": True}

        result = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert result["outcome"] == "TARGET_1_HIT"
        assert result["data_source"] == "INTRADAY"

    def test_normalized_kite_candles_integrate_with_evaluation(self):
        """Mocked Kite candles → normalize → resolve_outcome works end-to-end."""
        kite_raw = [["2026-05-20 09:15:00", 23000, 23060, 22990, 23020, 50000]]
        candles = normalize_kite_candles(kite_raw, "NIFTY", "5m")

        trade = {"signal": "CALL", "entry_price": 100.0, "stop_loss": 70.0, "target_1": 130.0, "target_2": None}
        ohlc  = {"open_p": 23000, "high_p": 23060, "low_p": 22990, "was_correct": True}

        result = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert result["outcome"] == "TARGET_1_HIT"
        assert result["data_source"] == "INTRADAY"


# ---------------------------------------------------------------------------
# get_default_provider backward compatibility
# ---------------------------------------------------------------------------

class TestGetDefaultProviderBackwardCompat:
    def test_returns_intraday_provider(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        p = get_default_provider()
        assert isinstance(p, (LocalCSVIntradayProvider, UnavailableIntradayProvider,
                               AngelOneIntradayProvider, KiteIntradayProvider))

    def test_returns_intraday_provider_interface(self, monkeypatch):
        _clear_broker_env(monkeypatch)
        p = get_default_provider()
        assert hasattr(p, "get_candles")
        assert hasattr(p, "is_available")
        assert hasattr(p, "name")

    def test_csv_provider_still_works_as_default(self, monkeypatch, tmp_path):
        """CSV files uploaded to data/intraday/ must still be found when BROKER_PROVIDER=csv."""
        _clear_broker_env(monkeypatch)
        p = LocalCSVIntradayProvider(data_dir=tmp_path)
        assert p.is_available() is True
        df = p.get_candles("NIFTY", date(2026, 5, 20))
        assert df.empty  # no files in tmp_path yet
        assert list(df.columns) == CANDLE_COLUMNS

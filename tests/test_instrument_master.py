"""
Tests for instrument master / token mapping, validation, coverage, and normalisers.

Covers (PR6 — existing):
  - Valid CSV loads correctly (is_loaded=True, record_count>0)
  - Missing required columns rejected (is_loaded=False, error set)
  - Equity/index/option contract lookup
  - Kite / Angel One token field resolution
  - InstrumentMaster graceful miss on missing file
  - normalize_symbol_input: .NS, aliases, casing
  - token_readiness, has_token for zero/empty tokens
  - AngelOne/Kite provider stubs with missing master

Covers (PR7 — new):
  - ValidationReport: errors, warnings, missing tokens, expired options
  - CoverageReport: by_type/exchange, index_ready, equity_ready
  - Instrument.is_expired()
  - InstrumentMaster.from_dataframe() factory
  - normalize_kite_native_df: column mapping, INDEX type, FUTIDX/OPTIDX detection
  - normalize_angelone_native_df: column mapping, alias handling
  - load_best_available_master: canonical → native → not-loaded priority
  - source_name property
  - CLI script: importable and runs without crash

Run: python -m pytest tests/test_instrument_master.py -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta

from src.fetchers.instrument_master import (
    Instrument,
    InstrumentMaster,
    ValidationReport,
    CoverageReport,
    normalize_symbol_input,
    normalize_kite_native_df,
    normalize_angelone_native_df,
    load_best_available_master,
    REQUIRED_COLUMNS,
    SUPPORTED_PROVIDERS,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, rows: list[dict], filename: str = "master.csv") -> Path:
    """Write a list-of-dicts to a CSV with all required columns."""
    df = pd.DataFrame(rows)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    p = tmp_path / filename
    df.to_csv(p, index=False)
    return p


def _nifty_index_row(provider: str, token_int: int = 256265, token_str: str = "26000") -> dict:
    return {
        "provider": provider,
        "exchange": "NSE",
        "symbol": "NIFTY",
        "trading_symbol": "NIFTY 50",
        "instrument_token": token_int if provider == "kite" else 0,
        "symbol_token": token_str if provider == "angelone" else "",
        "instrument_type": "INDEX",
        "expiry": "",
        "strike": 0.0,
        "option_type": "",
        "lot_size": 75,
        "tick_size": 0.05,
    }


def _reliance_eq_row(provider: str, token_int: int = 738561, token_str: str = "2885") -> dict:
    return {
        "provider": provider,
        "exchange": "NSE",
        "symbol": "RELIANCE",
        "trading_symbol": "RELIANCE",
        "instrument_token": token_int if provider == "kite" else 0,
        "symbol_token": token_str if provider == "angelone" else "",
        "instrument_type": "EQ",
        "expiry": "",
        "strike": 0.0,
        "option_type": "",
        "lot_size": 1,
        "tick_size": 0.05,
    }


def _nifty_option_row(
    provider: str,
    expiry: str = "2026-05-29",
    strike: float = 25000.0,
    option_type: str = "CE",
    token_int: int = 12345678,
    token_str: str = "99001",
) -> dict:
    return {
        "provider": provider,
        "exchange": "NFO",
        "symbol": "NIFTY",
        "trading_symbol": f"NIFTY{expiry.replace('-', '')[:6]}{int(strike)}{option_type}",
        "instrument_token": token_int if provider == "kite" else 0,
        "symbol_token": token_str if provider == "angelone" else "",
        "instrument_type": "OPTIDX",
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "lot_size": 75,
        "tick_size": 0.05,
    }


_CLI_SCRIPT = str(Path(__file__).parent.parent / "scripts" / "check_instrument_master.py")


def _run_cli(*args):
    import subprocess
    return subprocess.run(
        [sys.executable, _CLI_SCRIPT] + list(args),
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


# ---------------------------------------------------------------------------
# Loading tests
# ---------------------------------------------------------------------------

class TestInstrumentMasterLoading:
    def test_valid_csv_loads(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.is_loaded is True
        assert m.record_count == 1
        assert m.error is None

    def test_missing_file_does_not_crash(self, tmp_path):
        m = InstrumentMaster(path=tmp_path / "nonexistent.csv")
        assert m.is_loaded is False
        assert m.error is not None
        assert "not found" in m.error.lower()

    def test_missing_required_column_rejected(self, tmp_path):
        df = pd.DataFrame([{"provider": "kite", "symbol": "NIFTY"}])
        p = tmp_path / "bad.csv"
        df.to_csv(p, index=False)
        m = InstrumentMaster(path=p)
        assert m.is_loaded is False
        assert "missing required columns" in (m.error or "").lower()

    def test_empty_csv_with_correct_headers_loads(self, tmp_path):
        df = pd.DataFrame(columns=list(REQUIRED_COLUMNS))
        p = tmp_path / "empty.csv"
        df.to_csv(p, index=False)
        m = InstrumentMaster(path=p)
        assert m.is_loaded is True
        assert m.record_count == 0

    def test_multiple_providers_load_all_rows(self, tmp_path):
        rows = [_nifty_index_row("kite"), _nifty_index_row("angelone")]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        assert m.is_loaded is True
        assert m.record_count == 2


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------

class TestLookupIndexSymbol:
    def test_index_lookup_kite(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=256265)])
        m = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("kite", "NIFTY")
        assert inst is not None
        assert inst.symbol == "NIFTY"
        assert inst.instrument_type == "INDEX"

    def test_index_lookup_angelone(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("angelone", token_str="26000")])
        m = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("angelone", "NIFTY")
        assert inst is not None
        assert inst.symbol == "NIFTY"
        assert inst.symbol_token == "26000"

    def test_index_lookup_case_insensitive(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.lookup_index_symbol("kite", "nifty") is not None
        assert m.lookup_index_symbol("kite", "Nifty") is not None

    def test_index_lookup_missing_master_returns_none(self, tmp_path):
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        assert m.lookup_index_symbol("kite", "NIFTY") is None

    def test_index_lookup_wrong_provider_returns_none(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.lookup_index_symbol("angelone", "NIFTY") is None

    def test_index_lookup_unknown_symbol_returns_none(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.lookup_index_symbol("kite", "SENSEX") is None


class TestLookupEquitySymbol:
    def test_equity_lookup_kite(self, tmp_path):
        p = _write_csv(tmp_path, [_reliance_eq_row("kite", token_int=738561)])
        m = InstrumentMaster(path=p)
        inst = m.lookup_equity_symbol("kite", "RELIANCE")
        assert inst is not None
        assert inst.instrument_type == "EQ"
        assert inst.instrument_token == 738561

    def test_equity_lookup_angelone(self, tmp_path):
        p = _write_csv(tmp_path, [_reliance_eq_row("angelone", token_str="2885")])
        m = InstrumentMaster(path=p)
        inst = m.lookup_equity_symbol("angelone", "RELIANCE")
        assert inst is not None
        assert inst.symbol_token == "2885"

    def test_equity_lookup_with_ns_suffix(self, tmp_path):
        p = _write_csv(tmp_path, [_reliance_eq_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.lookup_equity_symbol("kite", "RELIANCE.NS") is not None

    def test_equity_lookup_missing_returns_none(self, tmp_path):
        p = _write_csv(tmp_path, [_reliance_eq_row("kite")])
        m = InstrumentMaster(path=p)
        assert m.lookup_equity_symbol("kite", "INFY") is None


class TestLookupOptionContract:
    def test_option_lookup_exact_match_kite(self, tmp_path):
        row = _nifty_option_row("kite", "2026-05-29", 25000.0, "CE", token_int=12345678)
        p   = _write_csv(tmp_path, [row])
        m   = InstrumentMaster(path=p)
        inst = m.lookup_option_contract("kite", "NIFTY", "2026-05-29", 25000.0, "CE")
        assert inst is not None
        assert inst.strike == 25000.0
        assert inst.option_type == "CE"
        assert inst.instrument_token == 12345678

    def test_option_lookup_exact_match_angelone(self, tmp_path):
        row = _nifty_option_row("angelone", "2026-05-29", 25000.0, "PE", token_str="99002")
        p   = _write_csv(tmp_path, [row])
        m   = InstrumentMaster(path=p)
        inst = m.lookup_option_contract("angelone", "NIFTY", "2026-05-29", 25000.0, "PE")
        assert inst is not None
        assert inst.symbol_token == "99002"

    def test_option_lookup_wrong_strike_returns_none(self, tmp_path):
        row = _nifty_option_row("kite", "2026-05-29", 25000.0, "CE")
        p   = _write_csv(tmp_path, [row])
        m   = InstrumentMaster(path=p)
        assert m.lookup_option_contract("kite", "NIFTY", "2026-05-29", 25100.0, "CE") is None

    def test_option_lookup_wrong_expiry_returns_none(self, tmp_path):
        row = _nifty_option_row("kite", "2026-05-29", 25000.0, "CE")
        p   = _write_csv(tmp_path, [row])
        m   = InstrumentMaster(path=p)
        assert m.lookup_option_contract("kite", "NIFTY", "2026-06-26", 25000.0, "CE") is None

    def test_option_lookup_wrong_type_returns_none(self, tmp_path):
        row = _nifty_option_row("kite", "2026-05-29", 25000.0, "CE")
        p   = _write_csv(tmp_path, [row])
        m   = InstrumentMaster(path=p)
        assert m.lookup_option_contract("kite", "NIFTY", "2026-05-29", 25000.0, "PE") is None


# ---------------------------------------------------------------------------
# Token field tests
# ---------------------------------------------------------------------------

class TestTokenFields:
    def test_kite_instrument_token_via_get_token(self, tmp_path):
        p    = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=256265)])
        m    = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("kite", "NIFTY")
        assert inst is not None
        assert inst.get_token("kite") == 256265
        assert inst.has_token("kite") is True

    def test_angelone_symbol_token_via_get_token(self, tmp_path):
        p    = _write_csv(tmp_path, [_nifty_index_row("angelone", token_str="26000")])
        m    = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("angelone", "NIFTY")
        assert inst is not None
        assert inst.get_token("angelone") == "26000"
        assert inst.has_token("angelone") is True

    def test_kite_zero_token_returns_none(self, tmp_path):
        row  = _nifty_index_row("kite", token_int=0)
        p    = _write_csv(tmp_path, [row])
        m    = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("kite", "NIFTY")
        assert inst is not None
        assert inst.get_token("kite") is None
        assert inst.has_token("kite") is False

    def test_angelone_empty_symbol_token_returns_none(self, tmp_path):
        row  = _nifty_index_row("angelone", token_str="")
        p    = _write_csv(tmp_path, [row])
        m    = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("angelone", "NIFTY")
        assert inst is not None
        assert inst.get_token("angelone") is None
        assert inst.has_token("angelone") is False

    def test_cross_provider_token_isolation(self, tmp_path):
        """Kite token should not bleed into Angel One and vice-versa."""
        p    = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=256265)])
        m    = InstrumentMaster(path=p)
        inst = m.lookup_index_symbol("kite", "NIFTY")
        assert inst is not None
        assert inst.get_token("angelone") is None
        assert inst.get_token("kite") == 256265


# ---------------------------------------------------------------------------
# normalize_symbol_input
# ---------------------------------------------------------------------------

class TestNormalizeSymbolInput:
    def test_strips_ns_suffix(self):
        assert normalize_symbol_input("RELIANCE.NS") == "RELIANCE"

    def test_strips_bo_suffix(self):
        assert normalize_symbol_input("TCS.BO") == "TCS"

    def test_nsei_alias(self):
        assert normalize_symbol_input("^NSEI") == "NIFTY"

    def test_nsebank_alias(self):
        assert normalize_symbol_input("^NSEBANK") == "BANKNIFTY"

    def test_uppercase(self):
        assert normalize_symbol_input("nifty") == "NIFTY"

    def test_nifty50_alias(self):
        assert normalize_symbol_input("NIFTY50") == "NIFTY"

    def test_no_change_for_clean_symbol(self):
        assert normalize_symbol_input("INFY") == "INFY"

    def test_strips_whitespace(self):
        assert normalize_symbol_input("  NIFTY  ") == "NIFTY"


# ---------------------------------------------------------------------------
# token_readiness
# ---------------------------------------------------------------------------

class TestTokenReadiness:
    def test_readiness_true_when_tokens_present(self, tmp_path):
        rows = [
            _nifty_index_row("kite", token_int=256265),
            {**_nifty_index_row("kite", token_int=12345), "symbol": "BANKNIFTY"},
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        r = m.token_readiness("kite", ["NIFTY", "BANKNIFTY"])
        assert r["NIFTY"] is True
        assert r["BANKNIFTY"] is True

    def test_readiness_false_when_zero_token(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=0)])
        m = InstrumentMaster(path=p)
        r = m.token_readiness("kite", ["NIFTY"])
        assert r["NIFTY"] is False

    def test_readiness_false_when_master_not_loaded(self, tmp_path):
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        r = m.token_readiness("kite", ["NIFTY", "BANKNIFTY"])
        assert r["NIFTY"] is False
        assert r["BANKNIFTY"] is False


# ---------------------------------------------------------------------------
# Provider stub behaviour when instrument master is absent/missing tokens
# ---------------------------------------------------------------------------

class TestProviderStubsWithInstrumentMaster:
    """
    Verify that AngelOne and Kite provider stubs return empty DataFrames
    safely when the instrument master is missing or tokens are absent.
    Credentials and packages are NOT required for these tests.
    """

    from datetime import date as _date

    def test_angelone_no_crash_when_master_missing(self, tmp_path):
        from datetime import date
        from src.fetchers.intraday_provider import AngelOneIntradayProvider, CANDLE_COLUMNS
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        p = AngelOneIntradayProvider(
            creds={"api_key": "fake", "client_code": "fake"},
            instrument_master=m,
        )
        df = p._fetch("NIFTY", date(2026, 5, 21), "5m")
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0

    def test_kite_no_crash_when_master_missing(self, tmp_path):
        from datetime import date
        from src.fetchers.intraday_provider import KiteIntradayProvider, CANDLE_COLUMNS
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        p = KiteIntradayProvider(
            creds={"api_key": "fake", "access_token": "fake"},
            instrument_master=m,
        )
        df = p._fetch("NIFTY", date(2026, 5, 21), "5m")
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0

    def test_angelone_no_crash_when_token_missing(self, tmp_path):
        from datetime import date
        from src.fetchers.intraday_provider import AngelOneIntradayProvider, CANDLE_COLUMNS
        row = _nifty_index_row("angelone", token_str="")
        p_csv = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p_csv)
        p = AngelOneIntradayProvider(
            creds={"api_key": "fake", "client_code": "fake"},
            instrument_master=m,
        )
        df = p._fetch("NIFTY", date(2026, 5, 21), "5m")
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0

    def test_kite_no_crash_when_token_missing(self, tmp_path):
        from datetime import date
        from src.fetchers.intraday_provider import KiteIntradayProvider, CANDLE_COLUMNS
        row = _nifty_index_row("kite", token_int=0)
        p_csv = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p_csv)
        p = KiteIntradayProvider(
            creds={"api_key": "fake", "access_token": "fake"},
            instrument_master=m,
        )
        df = p._fetch("NIFTY", date(2026, 5, 21), "5m")
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0

    def test_kite_logs_token_resolved_but_no_live_call(self, tmp_path):
        """When a real (positive) token exists, _fetch returns empty but logs resolved token."""
        from datetime import date
        from src.fetchers.intraday_provider import KiteIntradayProvider, CANDLE_COLUMNS
        row = _nifty_index_row("kite", token_int=256265)
        p_csv = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p_csv)
        provider = KiteIntradayProvider(
            creds={"api_key": "fake", "access_token": "fake"},
            instrument_master=m,
        )
        df = provider._fetch("NIFTY", date(2026, 5, 21), "5m")
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------

class TestValidationReport:
    def test_clean_master_is_clean(self, tmp_path):
        today = date.today()
        future = (today + timedelta(days=30)).isoformat()
        rows = [
            _nifty_index_row("kite", token_int=256265),
            _nifty_option_row("kite", expiry=future, token_int=12345678),
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        report = m.validate(today=today)
        assert report.has_errors is False
        assert report.has_warnings is False
        assert report.is_clean is True
        assert report.total_rows == 2
        assert report.valid_rows == 2

    def test_unloaded_master_returns_error_report(self, tmp_path):
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        report = m.validate()
        assert report.has_errors is True
        assert report.is_clean is False
        assert report.total_rows == 0
        assert report.valid_rows == 0

    def test_missing_kite_token_is_warning_not_error(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=0)])
        m = InstrumentMaster(path=p)
        report = m.validate()
        assert report.has_errors is False
        assert report.has_warnings is True
        assert report.missing_token_rows == 1
        assert len(report.missing_token_symbols) == 1

    def test_missing_angelone_token_is_warning_not_error(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("angelone", token_str="")])
        m = InstrumentMaster(path=p)
        report = m.validate()
        assert report.has_errors is False
        assert report.has_warnings is True
        assert report.missing_token_rows == 1

    def test_expired_option_is_warning_not_error(self, tmp_path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        row = _nifty_option_row("kite", expiry=yesterday, token_int=12345678)
        p = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p)
        report = m.validate()
        assert report.has_errors is False
        assert report.has_warnings is True
        assert report.expired_option_count == 1
        assert len(report.expired_option_symbols) == 1

    def test_option_missing_expiry_is_error(self, tmp_path):
        row = {
            "provider": "kite",
            "exchange": "NFO",
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY25MAY25000CE",
            "instrument_token": 12345678,
            "symbol_token": "",
            "instrument_type": "OPTIDX",
            "expiry": "",
            "strike": 25000.0,
            "option_type": "CE",
            "lot_size": 75,
            "tick_size": 0.05,
        }
        p = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p)
        report = m.validate()
        assert report.has_errors is True

    def test_option_zero_strike_is_error(self, tmp_path):
        today = date.today()
        future = (today + timedelta(days=30)).isoformat()
        row = {
            "provider": "kite",
            "exchange": "NFO",
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY25MAY0CE",
            "instrument_token": 12345678,
            "symbol_token": "",
            "instrument_type": "OPTIDX",
            "expiry": future,
            "strike": 0.0,
            "option_type": "CE",
            "lot_size": 75,
            "tick_size": 0.05,
        }
        p = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p)
        report = m.validate(today=today)
        assert report.has_errors is True

    def test_unsupported_provider_is_error(self, tmp_path):
        row = {**_nifty_index_row("kite"), "provider": "unknown_broker"}
        p = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p)
        report = m.validate()
        assert report.has_errors is True
        assert report.unsupported_provider_count == 1

    def test_future_expiry_not_flagged_as_expired(self, tmp_path):
        today = date.today()
        future = (today + timedelta(days=30)).isoformat()
        row = _nifty_option_row("kite", expiry=future, token_int=12345678)
        p = _write_csv(tmp_path, [row])
        m = InstrumentMaster(path=p)
        report = m.validate(today=today)
        assert report.expired_option_count == 0

    def test_valid_rows_count_excludes_error_rows(self, tmp_path):
        today = date.today()
        rows = [
            _nifty_index_row("kite", token_int=256265),      # valid → counted
            _nifty_index_row("kite", token_int=0),           # warning only → counted
            {                                                  # error (no expiry) → NOT counted
                "provider": "kite",
                "exchange": "NFO",
                "symbol": "NIFTY",
                "trading_symbol": "NIFTY25MAY25000CE",
                "instrument_token": 12345678,
                "symbol_token": "",
                "instrument_type": "OPTIDX",
                "expiry": "",
                "strike": 25000.0,
                "option_type": "CE",
                "lot_size": 75,
                "tick_size": 0.05,
            },
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        report = m.validate(today=today)
        assert report.total_rows == 3
        assert report.valid_rows == 2

    def test_missing_token_row_counts_as_structurally_valid(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=0)])
        m = InstrumentMaster(path=p)
        report = m.validate()
        # Row has a warning (missing token) but no structural error → still valid
        assert report.valid_rows == 1
        assert report.missing_token_rows == 1


# ---------------------------------------------------------------------------
# CoverageReport
# ---------------------------------------------------------------------------

class TestCoverageReport:
    def test_indices_all_ready_when_tokens_present(self, tmp_path):
        rows = [
            _nifty_index_row("kite", token_int=256265),
            {**_nifty_index_row("kite", token_int=12345), "symbol": "BANKNIFTY"},
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("kite")
        assert cov.index_ready["NIFTY"] is True
        assert cov.index_ready["BANKNIFTY"] is True
        assert cov.indices_all_ready is True

    def test_missing_index_symbol_is_false(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite", token_int=256265)])
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("kite")
        assert cov.index_ready["NIFTY"] is True
        assert cov.index_ready["BANKNIFTY"] is False
        assert "BANKNIFTY" in cov.missing_index_symbols

    def test_by_type_counts(self, tmp_path):
        rows = [
            _nifty_index_row("kite"),
            {**_nifty_index_row("kite", token_int=12345), "symbol": "BANKNIFTY"},
            _reliance_eq_row("kite"),
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("kite")
        assert cov.by_type.get("INDEX", 0) == 2
        assert cov.by_type.get("EQ", 0) == 1
        assert cov.total_count == 3

    def test_coverage_for_unloaded_master_is_empty(self, tmp_path):
        m = InstrumentMaster(path=tmp_path / "missing.csv")
        cov = m.coverage_report("kite")
        assert cov.total_count == 0
        assert cov.by_type == {}
        assert cov.index_ready.get("NIFTY", True) is False

    def test_equity_missing_shown_in_coverage(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("kite", fo_symbols=["RELIANCE"])
        assert cov.equity_ready["RELIANCE"] is False
        assert "RELIANCE" in cov.missing_token_symbols

    def test_expired_option_in_coverage(self, tmp_path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        rows = [
            _nifty_index_row("kite"),
            _nifty_option_row("kite", expiry=yesterday, token_int=12345678),
        ]
        p = _write_csv(tmp_path, rows)
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("kite")
        assert len(cov.expired_option_symbols) >= 1

    def test_coverage_wrong_provider_is_empty(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")])
        m = InstrumentMaster(path=p)
        cov = m.coverage_report("angelone")
        assert cov.total_count == 0
        assert cov.index_ready.get("NIFTY", True) is False


# ---------------------------------------------------------------------------
# Instrument.is_expired
# ---------------------------------------------------------------------------

class TestInstrumentIsExpired:
    def _make_instrument(self, expiry: str) -> Instrument:
        return Instrument(
            provider="KITE",
            exchange="NFO",
            symbol="NIFTY",
            trading_symbol="NIFTY25MAY25000CE",
            instrument_token=12345678,
            symbol_token="",
            instrument_type="OPTIDX",
            expiry=expiry,
            strike=25000.0,
            option_type="CE",
            lot_size=75,
            tick_size=0.05,
        )

    def test_past_expiry_is_expired(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        inst = self._make_instrument(yesterday)
        assert inst.is_expired(today=date.today()) is True

    def test_future_expiry_is_not_expired(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        inst = self._make_instrument(tomorrow)
        assert inst.is_expired(today=date.today()) is False

    def test_no_expiry_is_never_expired(self):
        inst = self._make_instrument("")
        assert inst.is_expired(today=date.today()) is False


# ---------------------------------------------------------------------------
# InstrumentMaster.from_dataframe
# ---------------------------------------------------------------------------

class TestFromDataframe:
    def test_loads_correctly_with_source_name(self):
        df = pd.DataFrame([_nifty_index_row("kite", token_int=256265)])
        m = InstrumentMaster.from_dataframe(df, source_name="my_source")
        assert m.is_loaded is True
        assert m.source_name == "my_source"
        assert m.record_count == 1

    def test_missing_columns_not_loaded(self):
        df = pd.DataFrame([{"provider": "kite", "symbol": "NIFTY"}])
        m = InstrumentMaster.from_dataframe(df, source_name="bad")
        assert m.is_loaded is False
        assert "missing required columns" in (m.error or "").lower()

    def test_lookup_works_after_from_dataframe(self):
        df = pd.DataFrame([_nifty_index_row("kite", token_int=256265)])
        m = InstrumentMaster.from_dataframe(df, source_name="test")
        inst = m.lookup_index_symbol("kite", "NIFTY")
        assert inst is not None
        assert inst.instrument_token == 256265


# ---------------------------------------------------------------------------
# normalize_kite_native_df
# ---------------------------------------------------------------------------

class TestNormalizeKiteNativeDf:
    def _kite_row(self, **kwargs) -> dict:
        base = {
            "instrument_token": "256265",
            "tradingsymbol": "NIFTY 50",
            "instrument_type": "EQ",
            "exchange": "NSE",
            "segment": "INDICES",
            "name": "NIFTY 50",
            "last_price": "0",
            "expiry": "",
            "strike": "0",
            "tick_size": "0.05",
            "lot_size": "50",
        }
        base.update(kwargs)
        return base

    def test_missing_required_column_returns_empty(self):
        df = pd.DataFrame([{"tradingsymbol": "NIFTY 50", "instrument_type": "EQ"}])
        result = normalize_kite_native_df(df)
        assert result.empty
        assert list(result.columns) == list(REQUIRED_COLUMNS)

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["instrument_token", "tradingsymbol", "instrument_type", "exchange"])
        result = normalize_kite_native_df(df)
        assert result.empty
        assert list(result.columns) == list(REQUIRED_COLUMNS)

    def test_indices_segment_maps_to_index_type(self):
        df = pd.DataFrame([self._kite_row(segment="INDICES", instrument_type="EQ")])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_type"] == "INDEX"

    def test_nifty_future_maps_to_futidx(self):
        df = pd.DataFrame([self._kite_row(
            instrument_token="13349378",
            tradingsymbol="NIFTY25MAYFUT",
            instrument_type="FUT",
            exchange="NFO",
            segment="NFO-FUT",
            name="NIFTY",
            expiry="2026-05-29",
            strike="0",
        )])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_type"] == "FUTIDX"

    def test_reliance_future_maps_to_futstk(self):
        df = pd.DataFrame([self._kite_row(
            instrument_token="4815109",
            tradingsymbol="RELIANCE25MAYFUT",
            instrument_type="FUT",
            exchange="NFO",
            segment="NFO-FUT",
            name="RELIANCE",
            expiry="2026-05-29",
            strike="0",
        )])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_type"] == "FUTSTK"

    def test_nifty_ce_maps_to_optidx(self):
        df = pd.DataFrame([self._kite_row(
            instrument_token="12345678",
            tradingsymbol="NIFTY2552525000CE",
            instrument_type="CE",
            exchange="NFO",
            segment="NFO-OPT",
            name="NIFTY",
            expiry="2026-05-29",
            strike="25000",
        )])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_type"] == "OPTIDX"
        assert result.iloc[0]["option_type"] == "CE"

    def test_equity_type_preserved(self):
        df = pd.DataFrame([self._kite_row(
            instrument_token="738561",
            tradingsymbol="RELIANCE",
            instrument_type="EQ",
            exchange="NSE",
            segment="NSE",
            name="RELIANCE INDUSTRIES",
        )])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_type"] == "EQ"

    def test_provider_is_kite(self):
        df = pd.DataFrame([self._kite_row()])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["provider"] == "KITE"

    def test_symbol_token_is_empty(self):
        df = pd.DataFrame([self._kite_row()])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["symbol_token"] == ""

    def test_instrument_token_carried_through(self):
        df = pd.DataFrame([self._kite_row(instrument_token="256265")])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["instrument_token"] == "256265"

    def test_result_has_all_required_columns(self):
        df = pd.DataFrame([self._kite_row()])
        result = normalize_kite_native_df(df)
        for col in REQUIRED_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_iso_expiry_preserved(self):
        df = pd.DataFrame([self._kite_row(
            tradingsymbol="NIFTY25MAYFUT",
            instrument_type="FUT",
            exchange="NFO",
            segment="NFO-FUT",
            name="NIFTY",
            expiry="2026-05-29",
        )])
        result = normalize_kite_native_df(df)
        assert result.iloc[0]["expiry"] == "2026-05-29"


# ---------------------------------------------------------------------------
# normalize_angelone_native_df
# ---------------------------------------------------------------------------

class TestNormalizeAngelOneNativeDf:
    def _ao_row(self, **kwargs) -> dict:
        base = {
            "token": "26000",
            "symbol": "Nifty 50",
            "exch_seg": "NSE",
            "name": "NIFTY",
            "expiry": "",
            "strike": "-1.0",
            "lotsize": "50",
            "instrumenttype": "INDEX",
            "tick_size": "0.05",
        }
        base.update(kwargs)
        return base

    def test_missing_required_column_returns_empty(self):
        df = pd.DataFrame([{"symbol": "NIFTY", "exch_seg": "NSE"}])
        result = normalize_angelone_native_df(df)
        assert result.empty
        assert list(result.columns) == list(REQUIRED_COLUMNS)

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["token", "symbol", "exch_seg"])
        result = normalize_angelone_native_df(df)
        assert result.empty
        assert list(result.columns) == list(REQUIRED_COLUMNS)

    def test_provider_is_angelone(self):
        df = pd.DataFrame([self._ao_row()])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["provider"] == "ANGELONE"

    def test_token_maps_to_symbol_token(self):
        df = pd.DataFrame([self._ao_row(token="26000")])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["symbol_token"] == "26000"

    def test_instrument_token_is_zero(self):
        df = pd.DataFrame([self._ao_row()])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["instrument_token"] == "0"

    def test_optidx_type_preserved(self):
        df = pd.DataFrame([self._ao_row(
            token="99001",
            symbol="NIFTY25MAY25000CE",
            exch_seg="NFO",
            name="NIFTY",
            instrumenttype="OPTIDX",
            expiry="29MAY2026",
            strike="25000.0",
        )])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["instrument_type"] == "OPTIDX"

    def test_option_type_ce_from_trading_symbol(self):
        df = pd.DataFrame([self._ao_row(
            symbol="NIFTY25MAY25000CE",
            exch_seg="NFO",
            instrumenttype="OPTIDX",
            expiry="29MAY2026",
            strike="25000.0",
        )])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["option_type"] == "CE"

    def test_option_type_pe_from_trading_symbol(self):
        df = pd.DataFrame([self._ao_row(
            symbol="NIFTY25MAY25000PE",
            exch_seg="NFO",
            instrumenttype="OPTIDX",
            expiry="29MAY2026",
            strike="25000.0",
        )])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["option_type"] == "PE"

    def test_alternate_column_symboltoken_accepted(self):
        df = pd.DataFrame([{
            "symboltoken": "26000",
            "symbol": "Nifty 50",
            "exch_seg": "NSE",
            "instrumenttype": "INDEX",
        }])
        result = normalize_angelone_native_df(df)
        assert not result.empty
        assert result.iloc[0]["symbol_token"] == "26000"

    def test_result_has_all_required_columns(self):
        df = pd.DataFrame([self._ao_row()])
        result = normalize_angelone_native_df(df)
        for col in REQUIRED_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_expiry_ddmmmyyyy_normalised(self):
        df = pd.DataFrame([self._ao_row(
            symbol="NIFTY25MAY25000CE",
            exch_seg="NFO",
            instrumenttype="OPTIDX",
            expiry="29MAY2026",
            strike="25000.0",
        )])
        result = normalize_angelone_native_df(df)
        assert result.iloc[0]["expiry"] == "2026-05-29"


# ---------------------------------------------------------------------------
# load_best_available_master
# ---------------------------------------------------------------------------

class TestLoadBestAvailableMaster:
    def test_canonical_file_takes_priority(self, tmp_path):
        # Both canonical and kite native present — canonical wins
        _write_csv(tmp_path, [_nifty_index_row("kite")], "instrument_master.csv")
        kite_native = tmp_path / "kite_instruments.csv"
        kite_native.write_text(
            "instrument_token,tradingsymbol,instrument_type,exchange\n"
            "256265,NIFTY 50,EQ,NSE\n"
        )
        m = load_best_available_master(instruments_dir=tmp_path)
        assert m.is_loaded is True
        assert m.source_name == "instrument_master.csv"

    def test_returns_not_loaded_when_nothing_exists(self, tmp_path):
        m = load_best_available_master(instruments_dir=tmp_path)
        assert m.is_loaded is False

    def test_kite_native_auto_normalised(self, tmp_path):
        kite_native = tmp_path / "kite_instruments.csv"
        kite_native.write_text(
            "instrument_token,tradingsymbol,instrument_type,exchange,segment,name,expiry,strike,tick_size,lot_size\n"
            "256265,NIFTY 50,EQ,NSE,INDICES,NIFTY 50,,0,0.05,50\n"
        )
        m = load_best_available_master(instruments_dir=tmp_path)
        assert m.is_loaded is True
        assert m.record_count > 0
        assert "kite_instruments.csv" in m.source_name

    def test_angelone_native_auto_normalised(self, tmp_path):
        ao_native = tmp_path / "angelone_instruments.csv"
        ao_native.write_text(
            "token,symbol,exch_seg,instrumenttype,name\n"
            "26000,Nifty 50,NSE,INDEX,NIFTY\n"
        )
        m = load_best_available_master(instruments_dir=tmp_path)
        assert m.is_loaded is True
        assert m.record_count > 0
        assert "angelone_instruments.csv" in m.source_name

    def test_canonical_takes_priority_over_native_files(self, tmp_path):
        # canonical has 1 row; native has 1 row — canonical wins, record_count=1
        _write_csv(tmp_path, [_nifty_index_row("kite")], "instrument_master.csv")
        ao_native = tmp_path / "angelone_instruments.csv"
        ao_native.write_text(
            "token,symbol,exch_seg\n"
            "26000,Nifty 50,NSE\n"
            "26009,BANKNIFTY,NSE\n"
        )
        m = load_best_available_master(instruments_dir=tmp_path)
        assert m.source_name == "instrument_master.csv"
        assert m.record_count == 1  # only canonical row


# ---------------------------------------------------------------------------
# source_name property
# ---------------------------------------------------------------------------

class TestSourceName:
    def test_file_based_source_name_is_filename(self, tmp_path):
        p = _write_csv(tmp_path, [_nifty_index_row("kite")], "my_master.csv")
        m = InstrumentMaster(path=p)
        assert m.source_name == "my_master.csv"

    def test_from_dataframe_source_name_preserved(self):
        df = pd.DataFrame([_nifty_index_row("kite")])
        m = InstrumentMaster.from_dataframe(df, source_name="kite_instruments.csv + angelone_instruments.csv")
        assert m.source_name == "kite_instruments.csv + angelone_instruments.csv"

    def test_missing_file_has_fallback_name(self, tmp_path):
        p = tmp_path / "missing_master.csv"
        m = InstrumentMaster(path=p)
        assert m.is_loaded is False
        assert m.source_name == "missing_master.csv"


# ---------------------------------------------------------------------------
# CLI script
# ---------------------------------------------------------------------------

class TestCheckInstrumentMasterCLI:
    def test_script_is_importable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "check_instrument_master",
            _CLI_SCRIPT,
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")

    def test_validate_missing_file_returns_nonzero(self, tmp_path):
        result = _run_cli("--file", str(tmp_path / "nonexistent.csv"))
        assert result.returncode != 0

    def test_validate_valid_file_returns_zero(self, tmp_path):
        # A master with valid structure (warnings OK, no structural errors → exit 0)
        p = _write_csv(tmp_path, [_nifty_index_row("kite")], "instrument_master.csv")
        result = _run_cli("--file", str(p))
        assert result.returncode == 0

    def test_convert_kite_dry_run_returns_zero(self, tmp_path):
        kite_csv = tmp_path / "kite.csv"
        kite_csv.write_text(
            "instrument_token,tradingsymbol,instrument_type,exchange,segment\n"
            "256265,NIFTY 50,EQ,NSE,INDICES\n"
        )
        result = _run_cli("--kite-file", str(kite_csv))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Data availability — instrument master integration
# ---------------------------------------------------------------------------

class TestDataAvailabilityInstrumentMaster:
    def test_instrument_master_key_present(self):
        from src.fetchers.data_availability import check_data_availability
        report = check_data_availability({})
        assert "instrument_master" in report

    def test_instrument_master_has_required_keys(self):
        from src.fetchers.data_availability import check_data_availability
        report = check_data_availability({})
        im = report["instrument_master"]
        for key in ("loaded", "path", "source_name", "record_count", "error",
                    "token_readiness", "validation", "coverage"):
            assert key in im, f"Missing key in instrument_master dict: {key}"

    def test_not_loaded_returns_false_readiness(self, monkeypatch):
        from src.fetchers.data_availability import check_data_availability
        from src.fetchers import data_availability as _da_mod
        not_loaded = InstrumentMaster(path=Path("/nonexistent/path_for_test.csv"))
        monkeypatch.setattr(_da_mod, "get_instrument_master", lambda: not_loaded)
        report = check_data_availability({})
        im = report["instrument_master"]
        assert im["loaded"] is False
        assert all(not v for v in im["token_readiness"].values())

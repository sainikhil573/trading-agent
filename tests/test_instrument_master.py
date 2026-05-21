"""
Tests for instrument master / token mapping.

Covers:
  - Valid CSV loads correctly (is_loaded=True, record_count>0)
  - Missing required columns rejected (is_loaded=False, error set)
  - Equity lookup returns correct Instrument
  - Index lookup returns correct Instrument
  - Option contract lookup returns correct Instrument with exact match
  - Symbol not in master returns None (no crash)
  - Kite token (instrument_token) resolved correctly via get_token()
  - Angel One token (symbol_token) resolved correctly via get_token()
  - InstrumentMaster does not crash when file is missing
  - normalize_symbol_input handles .NS suffix, aliases, casing
  - token_readiness returns correct bool map
  - has_token returns False for zero/empty tokens
  - AngelOne/Kite provider stubs do not crash when instrument master is missing

Run: python -m pytest tests/test_instrument_master.py -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.instrument_master import (
    Instrument,
    InstrumentMaster,
    normalize_symbol_input,
    REQUIRED_COLUMNS,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, rows: list[dict], filename: str = "master.csv") -> Path:
    """Write a list-of-dicts to a CSV with all required columns."""
    df = pd.DataFrame(rows)
    # Ensure all required columns present (fill missing with empty string)
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
        # Angel One token is empty on a Kite row
        assert inst.get_token("angelone") is None
        # Kite token is present
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
        # is_available() checks credentials AND package; skip package check via direct _fetch
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
        # Row with empty symbol_token
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
        # Still empty — live call not yet implemented
        assert list(df.columns) == CANDLE_COLUMNS
        assert len(df) == 0

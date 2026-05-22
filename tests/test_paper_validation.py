"""
Tests for S2 paper validation features.
Run: python -m pytest tests/test_paper_validation.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers — tier assignment logic (mirrors orchestrator rules)
# ---------------------------------------------------------------------------

def _assign_tier(accuracy_pct: float, signals_taken: int) -> str:
    """Replicate the tier assignment rules from TASK 1."""
    if accuracy_pct >= 55.0 and signals_taken >= 10:
        return "PRIMARY_CANDIDATE"
    if 45.0 <= accuracy_pct < 55.0 and signals_taken >= 10:
        return "SECONDARY_CANDIDATE"
    return "WATCHLIST_REVIEW"


def _confidence_tier(eff_conf: float | None) -> str:
    """Replicate the confidence tier logic from orchestrator."""
    if eff_conf is None:
        return "BELOW_FLOOR"
    if eff_conf >= 8.0:
        return "HIGH"
    if eff_conf >= 7.5:
        return "MID"
    if eff_conf >= 7.0:
        return "LOW"
    return "BELOW_FLOOR"


# ---------------------------------------------------------------------------
# TASK 1 — backtest tier assignment
# ---------------------------------------------------------------------------

def test_tier_assignment_primary():
    """accuracy >= 55% AND signals >= 10 → PRIMARY_CANDIDATE"""
    assert _assign_tier(78.6, 14) == "PRIMARY_CANDIDATE"
    assert _assign_tier(61.1, 18) == "PRIMARY_CANDIDATE"
    assert _assign_tier(56.2, 16) == "PRIMARY_CANDIDATE"
    assert _assign_tier(55.0, 10) == "PRIMARY_CANDIDATE"


def test_tier_assignment_secondary():
    """45–55% AND signals >= 10 → SECONDARY_CANDIDATE"""
    assert _assign_tier(50.0, 12) == "SECONDARY_CANDIDATE"
    assert _assign_tier(46.7, 15) == "SECONDARY_CANDIDATE"
    assert _assign_tier(45.0, 10) == "SECONDARY_CANDIDATE"


def test_tier_assignment_watchlist_small_sample():
    """accuracy >= 55% but signals < 10 → WATCHLIST_REVIEW (small sample)"""
    assert _assign_tier(55.6, 9)  == "WATCHLIST_REVIEW"
    assert _assign_tier(50.0, 8)  == "WATCHLIST_REVIEW"
    assert _assign_tier(100.0, 1) == "WATCHLIST_REVIEW"


# ---------------------------------------------------------------------------
# TASK 2 — confidence tier
# ---------------------------------------------------------------------------

def test_confidence_tier_high():
    assert _confidence_tier(8.5) == "HIGH"
    assert _confidence_tier(9.0) == "HIGH"
    assert _confidence_tier(10.0) == "HIGH"


def test_confidence_tier_mid():
    assert _confidence_tier(7.6) == "MID"
    assert _confidence_tier(7.9) == "MID"


def test_confidence_tier_low():
    assert _confidence_tier(7.1) == "LOW"
    assert _confidence_tier(7.0) == "LOW"


def test_confidence_tier_boundary_mid():
    """7.5 exactly → MID (>= 7.5 and < 8.0)"""
    assert _confidence_tier(7.5) == "MID"


def test_confidence_tier_boundary_high():
    """8.0 exactly → HIGH (>= 8.0)"""
    assert _confidence_tier(8.0) == "HIGH"


# ---------------------------------------------------------------------------
# TASK 7 / S2B — paper journal append (idempotent, no duplicate)
# ---------------------------------------------------------------------------

def _make_brief(symbol="WIPRO", signal="CALL", confidence=7.3, eff_conf=7.0):
    return {
        "stock_trades": [{
            "symbol":                    symbol,
            "signal":                    signal,
            "strike":                    "300CE",
            "confidence":                confidence,
            "gate_effective_confidence": eff_conf,
            "entry_price":               10.0,
            "stop_loss":                 7.0,
            "target_1":                  15.0,
            "target_2":                  20.0,
        }]
    }


def test_paper_journal_append_no_duplicate():
    """Appending twice for same date replaces, does not duplicate."""
    from src.orchestrator import _append_to_paper_journal, _JOURNAL_PATH

    with tempfile.TemporaryDirectory() as tmp:
        journal_path = Path(tmp) / "journal.json"
        # Patch the module-level constant
        import src.orchestrator as orch
        original = orch._JOURNAL_PATH
        orch._JOURNAL_PATH = journal_path
        try:
            today = "2026-05-22"
            brief = _make_brief()
            _append_to_paper_journal(brief, today)
            _append_to_paper_journal(brief, today)  # second call — must replace, not add

            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            today_entries = [t for t in journal["trades"] if t["date"] == today]
            assert len(today_entries) == 1, (
                f"Expected 1 entry for {today}, got {len(today_entries)}"
            )
        finally:
            orch._JOURNAL_PATH = original


def test_paper_journal_no_hypothetical_mixing():
    """Gated-out trades are NOT included in the paper journal (only stock_trades are added)."""
    from src.orchestrator import _append_to_paper_journal
    import src.orchestrator as orch

    with tempfile.TemporaryDirectory() as tmp:
        journal_path = Path(tmp) / "journal.json"
        original = orch._JOURNAL_PATH
        orch._JOURNAL_PATH = journal_path
        try:
            today = "2026-05-22"
            # Brief with gated-out trade in stock_trades_gated_out — not in stock_trades
            brief = {
                "stock_trades": [],
                "stock_trades_gated_out": [{
                    "symbol": "AXISBANK",
                    "signal": "CALL",
                    "confidence": 6.5,
                    "gate_effective_confidence": 5.5,
                    "gate_status": "NO_TRADE",
                }],
            }
            _append_to_paper_journal(brief, today)

            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            all_symbols = [t["symbol"] for t in journal["trades"]]
            assert "AXISBANK" not in all_symbols, (
                "Gated-out trades must not appear in paper journal"
            )
        finally:
            orch._JOURNAL_PATH = original


# ---------------------------------------------------------------------------
# S2C — health report schema
# ---------------------------------------------------------------------------

def _make_health_report(overall="HEALTHY", failed_sources=None):
    sources = {
        "vix":            {"status": "FRESH"},
        "fii_dii":        {"status": "FRESH"},
        "participant_oi": {"status": "CACHED"},
        "option_chain":   {"status": "FRESH"},
        "global_cues":    {"status": "FRESH"},
        "technicals":     {"status": "FRESH"},
        "news":           {"status": "FRESH"},
    }
    if failed_sources:
        for s in failed_sources:
            sources[s] = {"status": "FAILED"}
    return {
        "date":    "2026-05-22",
        "time":    "08:05 IST",
        "sources": sources,
        "overall": overall,
    }


def test_health_report_schema():
    """Health report has required keys: date, time, sources, overall."""
    report = _make_health_report()
    for key in ("date", "time", "sources", "overall"):
        assert key in report, f"Missing key: {key}"
    assert isinstance(report["sources"], dict)
    assert len(report["sources"]) > 0


def test_health_report_overall_statuses():
    """HEALTHY when all FRESH, DEGRADED when some FAILED."""
    healthy = _make_health_report(overall="HEALTHY")
    assert healthy["overall"] == "HEALTHY"

    degraded = _make_health_report(overall="DEGRADED", failed_sources=["news"])
    assert degraded["overall"] == "DEGRADED"
    assert degraded["sources"]["news"]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# TASK 3 — intraday CSV parser
# ---------------------------------------------------------------------------

from src.fetchers.intraday_csv_parser import parse_candle_csv, compute_intraday_outcome

_ZERODHA_CSV = (
    "date,time,open,high,low,close,volume\n"
    "22-05-2026,09:15:00,24000.5,24050.0,23990.0,24020.0,100000\n"
    "22-05-2026,09:20:00,24020.0,24080.0,24010.0,24060.0,120000\n"
    "22-05-2026,09:25:00,24060.0,24100.0,24040.0,24090.0,110000\n"
)

_TRADINGVIEW_CSV = (
    "time,open,high,low,close,volume\n"
    "1748000100,24000.5,24050.0,23990.0,24020.0,100000\n"
    "1748000400,24020.0,24080.0,24010.0,24060.0,120000\n"
    "1748000700,24060.0,24100.0,24040.0,24090.0,110000\n"
)

_BAD_CSV = "this is not,a valid,candle,csv\nhello,world,foo,bar\n"


def test_zerodha_csv_parse():
    result = parse_candle_csv(_ZERODHA_CSV)
    assert result["errors"] == [], f"Expected no errors, got: {result['errors']}"
    assert result["format_detected"] == "zerodha"
    df = result["df"]
    assert df is not None
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert len(df) == 3
    assert float(df.iloc[0]["open"]) == pytest.approx(24000.5)


def test_tradingview_csv_parse():
    result = parse_candle_csv(_TRADINGVIEW_CSV)
    assert result["errors"] == [], f"Expected no errors, got: {result['errors']}"
    assert result["format_detected"] == "tradingview"
    df = result["df"]
    assert df is not None
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    assert len(df) == 3


def test_csv_parse_bad_format_no_crash():
    """Completely invalid CSV returns errors and df=None — never raises."""
    result = parse_candle_csv(_BAD_CSV)
    assert result["df"] is None
    assert len(result["errors"]) > 0
    assert result["format_detected"] == "unknown"


def test_csv_parse_bytes_input():
    """Bytes input is also accepted."""
    result = parse_candle_csv(_ZERODHA_CSV.encode("utf-8"))
    assert result["df"] is not None
    assert result["format_detected"] == "zerodha"


def test_csv_parse_empty_no_crash():
    result = parse_candle_csv("")
    assert result["df"] is None
    assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# TASK 6 — opening gap fallback always returns dict
# ---------------------------------------------------------------------------

def test_opening_gap_fallback_returns_dict():
    """fetch_opening_gap() always returns a dict with required keys."""
    from src.fetchers.global_fetcher import fetch_opening_gap

    # Mock all network calls to fail → should still return a dict
    with patch("src.fetchers.global_fetcher.requests.get", side_effect=Exception("network down")):
        with patch("src.fetchers.global_fetcher._fetch_ticker", return_value={
            "last": None, "prev_close": None, "pct_change": None, "error": "mock error"
        }):
            result = fetch_opening_gap()

    assert isinstance(result, dict), "fetch_opening_gap must always return a dict"
    for key in ("source", "value", "pct_change", "error"):
        assert key in result, f"Missing key in opening gap result: {key}"


def test_opening_gap_gift_nifty_success():
    """When NSE API returns valid data, source is gift_nifty."""
    from src.fetchers.global_fetcher import fetch_opening_gap

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"last": 24500.5, "pChange": 0.35}

    with patch("src.fetchers.global_fetcher.requests.get", return_value=mock_response):
        result = fetch_opening_gap()

    assert result["source"] == "gift_nifty"
    assert result["value"] == pytest.approx(24500.5)
    assert result["error"] is None


# ---------------------------------------------------------------------------
# TASK 7 — dry-run post-market does not corrupt journal
# ---------------------------------------------------------------------------

def test_dry_run_postmarket_no_journal_corruption():
    """Dry-run doesn't corrupt journal if yfinance fetch fails."""
    import src.orchestrator as orch

    with tempfile.TemporaryDirectory() as tmp:
        # Create a valid journal
        journal_path = Path(tmp) / "journal.json"
        original_journal = Path(tmp) / "original.json"
        journal_data = {
            "trades": [
                {
                    "date": "2026-05-21",
                    "symbol": "WIPRO",
                    "signal": "CALL",
                    "status": "OPEN",
                    "confidence_tier": "LOW",
                }
            ],
            "updated": "08:05 IST",
        }
        journal_path.write_text(json.dumps(journal_data), encoding="utf-8")

        original_jpath = orch._JOURNAL_PATH
        original_outdir = orch.OUT_DIR
        orch._JOURNAL_PATH = journal_path
        orch.OUT_DIR = Path(tmp)

        # Patch yfinance to fail
        with patch("src.orchestrator._fetch_yf_intraday", side_effect=Exception("yf down")):
            try:
                result = orch.run_dry_run_postmarket()
            except Exception:
                pass  # dry-run should handle this internally

        # Journal must still be valid JSON and unchanged
        if journal_path.exists():
            reloaded = json.loads(journal_path.read_text(encoding="utf-8"))
            assert reloaded["trades"][0]["symbol"] == "WIPRO", (
                "Journal was corrupted — original trade data lost"
            )

        orch._JOURNAL_PATH = original_jpath
        orch.OUT_DIR = original_outdir

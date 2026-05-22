"""
Tests for S3 analytics: signal_quality, premarket_checklist, telegram_formatter,
and memory loop (mock post-market file).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# signal_quality tests
# ---------------------------------------------------------------------------

class TestSignalQuality:

    def test_pending_trade_returns_pending(self):
        from src.analytics.signal_quality import _compute_quality
        trade = {
            "symbol": "WIPRO", "signal": "CALL", "status": "OPEN",
            "entry_price": 7.0, "stop_loss": 3.5, "target_1": 10.5,
            "outcome": None, "confidence_tier": "MID", "date": "2026-05-22",
        }
        result = _compute_quality(trade, None)
        assert result["outcome_quality"] == "PENDING"

    def test_clean_win_on_t1_hit(self):
        from src.analytics.signal_quality import _compute_quality
        trade = {
            "symbol": "NIFTY", "signal": "CALL", "status": "CLOSED",
            "entry_price": 10.0, "stop_loss": 5.0, "target_1": 15.0,
            "outcome": "TARGET_1_HIT", "confidence_tier": "HIGH", "date": "2026-05-20",
        }
        result = _compute_quality(trade, None)
        assert result["outcome_quality"] == "CLEAN_WIN"

    def test_clean_win_on_t2_hit(self):
        from src.analytics.signal_quality import _compute_quality
        trade = {
            "symbol": "NIFTY", "signal": "CALL", "status": "CLOSED",
            "entry_price": 10.0, "stop_loss": 5.0, "target_1": 15.0, "target_2": 20.0,
            "outcome": "TARGET_2_HIT", "confidence_tier": "HIGH", "date": "2026-05-20",
        }
        result = _compute_quality(trade, None)
        assert result["outcome_quality"] == "CLEAN_WIN"

    def test_stopped_out_on_sl(self):
        from src.analytics.signal_quality import _compute_quality
        trade = {
            "symbol": "BANKNIFTY", "signal": "PUT", "status": "CLOSED",
            "entry_price": 8.0, "stop_loss": 4.0, "target_1": 12.0,
            "outcome": "SL_HIT", "confidence_tier": "LOW", "date": "2026-05-20",
        }
        result = _compute_quality(trade, None)
        assert result["outcome_quality"] == "STOPPED_OUT"

    def test_pending_with_intraday_computes_pnl(self):
        """PENDING trade with intraday candles should compute best/worst PnL."""
        import pandas as pd
        from src.analytics.signal_quality import _compute_quality

        candles = pd.DataFrame([
            {"datetime": datetime(2026, 5, 22, 9, 15), "open": 270.0, "high": 280.0, "low": 265.0, "close": 278.0, "volume": 1000},
            {"datetime": datetime(2026, 5, 22, 9, 20), "open": 278.0, "high": 285.0, "low": 272.0, "close": 283.0, "volume": 1200},
        ])
        trade = {
            "symbol": "WIPRO", "signal": "CALL", "status": "OPEN",
            "entry_price": 7.0, "stop_loss": 3.5, "target_1": 10.5,
            "outcome": None, "confidence_tier": "MID", "date": "2026-05-22",
        }
        result = _compute_quality(trade, candles)
        assert result["outcome_quality"] == "PENDING"
        assert result["best_case_pnl"] is not None
        assert result["best_case_pnl"] > 0  # price went up for CALL
        assert result["intraday_available"] is True

    def test_run_signal_quality_no_journal(self, tmp_path):
        from src.analytics.signal_quality import run_signal_quality
        missing = tmp_path / "missing.json"
        out = tmp_path / "sq.json"
        result = run_signal_quality(journal_path=missing, output_path=out)
        assert result.get("error") is not None

    def test_run_signal_quality_with_journal(self, tmp_path):
        from src.analytics.signal_quality import run_signal_quality

        journal = tmp_path / "journal.json"
        journal.write_text(json.dumps({
            "trades": [
                {"symbol": "WIPRO", "signal": "CALL", "status": "OPEN",
                 "entry_price": 7.0, "stop_loss": 3.5, "target_1": 10.5,
                 "outcome": None, "confidence_tier": "MID", "date": "2026-05-22",
                 "confidence": 7.5}
            ]
        }), encoding="utf-8")
        out = tmp_path / "signal_quality.json"

        with patch("src.analytics.signal_quality._fetch_5m_candles", return_value=None):
            result = run_signal_quality(journal_path=journal, output_path=out)

        assert result["total_trades"] == 1
        assert result["summary"]["PENDING"] == 1
        assert out.exists()


# ---------------------------------------------------------------------------
# premarket_checklist tests
# ---------------------------------------------------------------------------

class TestPremarketChecklist:

    def _good_health(self):
        return {
            "overall": "HEALTHY",
            "sources": {
                "vix":            {"status": "FRESH"},
                "option_chain":   {"status": "FRESH"},
                "global_cues":    {"status": "FRESH"},
                "technicals":     {"status": "FRESH"},
                "fii_dii":        {"status": "FAILED"},
                "participant_oi": {"status": "FAILED"},
            }
        }

    def test_all_pass(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        result = run_premarket_checklist(
            vix=14.5,
            health=self._good_health(),
            check_date=date.today() if date.today().weekday() < 5 else date(2026, 5, 22),
        )
        # On today (trading day), no blocks expected
        assert result["block_count"] == 0

    def test_vix_above_25_blocks(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        result = run_premarket_checklist(
            vix=27.5,
            health=self._good_health(),
            check_date=date(2026, 5, 22),
        )
        assert not result["checklist_passed"]
        failed_names = [it["name"] for it in result["failed_items"]]
        assert "VIX_BELOW_25" in failed_names

    def test_vix_exactly_25_blocks(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        result = run_premarket_checklist(
            vix=25.0,
            health=self._good_health(),
            check_date=date(2026, 5, 22),
        )
        assert not result["checklist_passed"]

    def test_critical_health_blocks(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        health = self._good_health()
        health["overall"] = "CRITICAL"
        result = run_premarket_checklist(
            vix=14.5,
            health=health,
            check_date=date(2026, 5, 22),
        )
        assert not result["checklist_passed"]

    def test_weekend_blocks(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        # Find a weekend date
        weekend = date(2026, 5, 24)  # Sunday
        result = run_premarket_checklist(
            vix=14.5,
            health=self._good_health(),
            check_date=weekend,
        )
        assert not result["checklist_passed"]
        failed_names = [it["name"] for it in result["failed_items"]]
        assert "TRADING_DAY" in failed_names

    def test_degraded_health_warns_not_blocks(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        health = self._good_health()
        health["overall"] = "DEGRADED"
        result = run_premarket_checklist(
            vix=14.5,
            health=health,
            check_date=date(2026, 5, 22),
        )
        assert result["checklist_passed"]  # DEGRADED is WARN not BLOCK
        assert result["has_warnings"]

    def test_fewer_than_3_fresh_layers_warns(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        health = {
            "overall": "DEGRADED",
            "sources": {k: {"status": "FAILED"} for k in
                        ["vix", "option_chain", "global_cues", "technicals", "fii_dii", "participant_oi"]}
        }
        result = run_premarket_checklist(
            vix=14.5,
            health=health,
            check_date=date(2026, 5, 22),
        )
        failed_names = [it["name"] for it in result["failed_items"]]
        assert "FRESH_LAYERS" in failed_names

    def test_signal_consistency_conflict(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        trade = {
            "signal": "CALL",
            "timeframe_1d_signal": "PUT",  # conflict
        }
        result = run_premarket_checklist(
            vix=14.5,
            health=self._good_health(),
            trade=trade,
            check_date=date(2026, 5, 22),
        )
        failed_names = [it["name"] for it in result["failed_items"]]
        assert "SIGNAL_CONSISTENCY" in failed_names

    def test_signal_consistency_pass(self):
        from src.validators.premarket_checklist import run_premarket_checklist
        trade = {
            "signal": "CALL",
            "timeframe_1d_signal": "CALL",
            "timeframe_1h_signal": "NEUTRAL",
        }
        result = run_premarket_checklist(
            vix=14.5,
            health=self._good_health(),
            trade=trade,
            check_date=date(2026, 5, 22),
        )
        failed_names = [it["name"] for it in result["failed_items"]]
        assert "SIGNAL_CONSISTENCY" not in failed_names


# ---------------------------------------------------------------------------
# telegram_formatter tests
# ---------------------------------------------------------------------------

class TestTelegramFormatter:

    def test_go_signal_format(self):
        from src.alerts.telegram_formatter import format_go_signal
        trade = {
            "symbol": "WIPRO", "signal": "CALL", "confidence_tier": "MID",
            "confidence": 7.5, "gate_effective_confidence": 7.0,
            "entry_price": 7.0, "stop_loss": 3.5, "target_1": 10.5, "target_2": 14.0,
        }
        msg = format_go_signal(trade, vix=14.3)
        assert "[GO]" in msg
        assert "WIPRO" in msg
        assert "CALL" in msg
        assert "MID" in msg
        assert "7.0" in msg  # entry
        assert "3.5" in msg  # SL
        assert "10.5" in msg  # T1
        assert "14.3" in msg  # VIX

    def test_no_trade_format(self):
        from src.alerts.telegram_formatter import format_no_trade
        msg = format_no_trade("VIX = 27.3 — above 25 threshold", "2026-05-22")
        assert "[NO TRADE]" in msg
        assert "2026-05-22" in msg
        assert "VIX" in msg

    def test_data_degraded_format(self):
        from src.alerts.telegram_formatter import format_data_degraded
        health = {
            "overall": "DEGRADED",
            "sources": {
                "vix":  {"status": "FRESH"},
                "fii_dii": {"status": "FAILED"},
                "participant_oi": {"status": "FAILED"},
            }
        }
        msg = format_data_degraded(health, "2026-05-22")
        assert "[DATA WARNING]" in msg
        assert "DEGRADED" in msg
        assert "fii_dii" in msg

    def test_save_pending_alerts(self, tmp_path):
        import src.alerts.telegram_formatter as tf_mod
        orig = tf_mod.OUT_DIR
        tf_mod.OUT_DIR = tmp_path
        try:
            from src.alerts.telegram_formatter import save_pending_alerts
            alerts = [
                {"type": "GO_SIGNAL", "formatted": "[GO] WIPRO CALL", "payload": {}},
            ]
            out = save_pending_alerts(alerts, date_str="2026-05-22")
            assert out.exists()
            data = json.loads(out.read_text())
            assert data["date"] == "2026-05-22"
            assert len(data["alerts"]) == 1
            assert data["alerts"][0]["type"] == "GO_SIGNAL"
        finally:
            tf_mod.OUT_DIR = orig

    def test_build_alerts_from_brief_no_trade(self):
        from src.alerts.telegram_formatter import build_alerts_from_brief
        brief = {
            "date": "2026-05-22",
            "trades": [],
            "stock_trades": [],
            "_gate_summary": {"final_recommendation_status": "NO_ACTIONABLE_TRADE"},
            "post_gate_summary": {"message": "All trades blocked"},
        }
        alerts = build_alerts_from_brief(brief, date_str="2026-05-22")
        types = [a["type"] for a in alerts]
        assert "NO_TRADE" in types

    def test_build_alerts_from_brief_go_signal(self):
        from src.alerts.telegram_formatter import build_alerts_from_brief
        brief = {
            "date": "2026-05-22",
            "trades": [
                {"symbol": "NIFTY", "signal": "CALL", "confidence_tier": "HIGH",
                 "confidence": 8.0, "entry_price": 10.0, "stop_loss": 5.0,
                 "target_1": 15.0, "checklist_result": {"checklist_passed": True},
                 "gated_out": False}
            ],
            "stock_trades": [],
        }
        alerts = build_alerts_from_brief(brief, date_str="2026-05-22")
        types = [a["type"] for a in alerts]
        assert "GO_SIGNAL" in types


# ---------------------------------------------------------------------------
# memory loop tests
# ---------------------------------------------------------------------------

class TestMemoryLoop:

    def test_memory_loads_from_file(self, tmp_path):
        """Memory loop should load post_market file when present."""
        pm_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        pm_data = {
            "date": pm_date, "grade": "B",
            "confidence_adjustment": 0.3, "_test": True
        }
        pm_path = tmp_path / f"post_market_{pm_date}.json"
        pm_path.write_text(json.dumps(pm_data), encoding="utf-8")

        # Simulate memory loop
        loaded = None
        for i in range(1, 8):
            past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            p = tmp_path / f"post_market_{past}.json"
            if p.exists():
                loaded = json.loads(p.read_text())
                break

        assert loaded is not None
        assert loaded["grade"] == "B"
        assert loaded["confidence_adjustment"] == 0.3

    def test_memory_returns_none_when_no_file(self, tmp_path):
        """Memory loop should return None when no post_market files exist."""
        loaded = None
        for i in range(1, 8):
            past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            p = tmp_path / f"post_market_{past}.json"
            if p.exists():
                loaded = json.loads(p.read_text())
                break
        assert loaded is None

    def test_memory_skips_future_dates(self, tmp_path):
        """Memory loop does not look forward — only back 7 days."""
        future_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        pm_path = tmp_path / f"post_market_{future_date}.json"
        pm_path.write_text(json.dumps({"grade": "A"}), encoding="utf-8")

        loaded = None
        for i in range(1, 8):
            past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            p = tmp_path / f"post_market_{past}.json"
            if p.exists():
                loaded = json.loads(p.read_text())
                break
        assert loaded is None  # future file should not be found

    def test_memory_loads_most_recent(self, tmp_path):
        """If multiple post_market files exist, loads the most recent one."""
        for delta in [1, 3, 5]:
            d = (date.today() - timedelta(days=delta)).strftime("%Y-%m-%d")
            (tmp_path / f"post_market_{d}.json").write_text(
                json.dumps({"grade": f"delta_{delta}"}), encoding="utf-8"
            )

        loaded = None
        for i in range(1, 8):
            past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            p = tmp_path / f"post_market_{past}.json"
            if p.exists():
                loaded = json.loads(p.read_text())
                break

        assert loaded is not None
        assert loaded["grade"] == "delta_1"  # most recent (1 day back)

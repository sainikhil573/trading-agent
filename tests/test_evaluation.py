"""
Lightweight tests for evaluation logic and outcome tracker fixes.
Run: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzers.evaluation import (
    evaluate_accuracy_log,
    check_entry_trigger_met,
    derive_data_quality,
    explain_outcome,
    audit_predictions_vs_triggers,
)


# ---------------------------------------------------------------------------
# Outcome logic tests (validates the bug fix)
# ---------------------------------------------------------------------------

class TestExplainOutcome:
    def _make(self, was_correct, sl_hit, t1_reached, t2_reached=False, outcome=None, chg=None):
        return {
            "symbol":          "NIFTY",
            "signal":          "PUT",
            "was_correct":     was_correct,
            "sl_hit":          sl_hit,
            "target_1_reached": t1_reached,
            "target_2_reached": t2_reached,
            "outcome":         outcome,
            "day_change_pct":  chg if chg is not None else (-1.0 if was_correct else 1.0),
        }

    def test_correct_direction_t1(self):
        r = self._make(True, False, True, outcome="TARGET_1_HIT")
        out = explain_outcome(r)
        assert "CORRECT" in out
        assert "Target 1 reached" in out

    def test_wrong_direction_sl(self):
        r = self._make(False, True, False, outcome="SL_HIT")
        out = explain_outcome(r)
        assert "WRONG" in out
        assert "Stop-loss was triggered" in out

    def test_path_ambiguous_has_note(self):
        r = self._make(False, True, True, outcome="SL_HIT")
        r["path_note"] = "OHLC-only tracking: SL and target both triggered via day H/L extremes"
        out = explain_outcome(r)
        assert "OHLC-only tracking" in out
        assert "WRONG" in out


# ---------------------------------------------------------------------------
# evaluate_accuracy_log
# ---------------------------------------------------------------------------

class TestEvaluateAccuracyLog:
    def test_empty(self):
        result = evaluate_accuracy_log([])
        assert result["total"] == 0
        assert "error" in result

    def test_all_correct_calls(self):
        log = [
            {"was_correct": True, "signal": "CALL", "sl_hit": False,
             "target_1_reached": True, "target_2_reached": False, "confidence": 7.5}
        ] * 3
        r = evaluate_accuracy_log(log)
        assert r["direction_pct"] == 100.0
        assert r["call_count"] == 3
        assert r["put_count"] == 0

    def test_all_wrong_puts_sl_hit(self):
        log = [
            {"was_correct": False, "signal": "PUT", "sl_hit": True,
             "target_1_reached": False, "target_2_reached": False, "confidence": 7.2}
        ] * 2
        r = evaluate_accuracy_log(log)
        assert r["direction_pct"] == 0.0
        assert r["sl_hit_pct"] == 100.0
        assert r["put_correct"] == 0

    def test_ambiguous_wrong_direction_both_triggers(self):
        """Real pattern from 2026-05-18 NIFTY PUT — directional WRONG + both SL and T1 via OHLC."""
        log = [{
            "was_correct": False, "signal": "PUT", "sl_hit": True,
            "target_1_reached": True, "target_2_reached": False, "confidence": 7.1,
        }]
        r = evaluate_accuracy_log(log)
        assert r["ambiguous_outcomes"] == 1
        assert "OHLC extremes" in (r["ambiguity_note"] or "")

    def test_t1_only_credited_when_correct(self):
        """T1 should only count when direction was correct (matches outcome bug fix)."""
        log = [
            {"was_correct": True,  "signal": "CALL", "sl_hit": False, "target_1_reached": True,  "target_2_reached": False, "confidence": 8.0},
            {"was_correct": False, "signal": "PUT",  "sl_hit": True,  "target_1_reached": True,  "target_2_reached": False, "confidence": 7.1},
        ]
        r = evaluate_accuracy_log(log)
        assert r["t1_correct_pct"] == 50.0  # only 1/2 counted

    def test_skips_errored_entries(self):
        log = [
            {"error": "No price data", "symbol": "NIFTY"},
            {"was_correct": True, "signal": "CALL", "sl_hit": False,
             "target_1_reached": False, "target_2_reached": False, "confidence": 7.5},
        ]
        r = evaluate_accuracy_log(log)
        assert r["total"] == 1


# ---------------------------------------------------------------------------
# check_entry_trigger_met
# ---------------------------------------------------------------------------

class TestEntryTriggerCheck:
    def test_put_trigger_not_met_open_above(self):
        trade = {
            "signal":        "PUT",
            "entry_trigger": "enter only if NIFTY spot trades BELOW 23449 in first 30 min",
        }
        r = check_entry_trigger_met(trade, index_open=23500.0)
        assert r["met"] is False
        assert "TRIGGER NOT MET" in r["reason"]

    def test_put_trigger_met_open_below(self):
        trade = {
            "signal":        "PUT",
            "entry_trigger": "enter only if NIFTY spot trades BELOW 23449 in first 30 min",
        }
        r = check_entry_trigger_met(trade, index_open=23300.0)
        assert r["met"] is True
        assert "TRIGGER MET" in r["reason"]

    def test_call_trigger_met(self):
        trade = {
            "signal":        "CALL",
            "entry_trigger": "enter only if NIFTY spot trades ABOVE 24200 in first 30 min",
        }
        r = check_entry_trigger_met(trade, index_open=24250.0)
        assert r["met"] is True

    def test_call_trigger_not_met(self):
        trade = {
            "signal":        "CALL",
            "entry_trigger": "enter only if NIFTY spot trades ABOVE 24200 in first 30 min",
        }
        r = check_entry_trigger_met(trade, index_open=24100.0)
        assert r["met"] is False

    def test_no_open_price_returns_none(self):
        trade = {"signal": "PUT", "entry_trigger": "enter below 23449"}
        r = check_entry_trigger_met(trade, index_open=None)
        assert r["met"] is None

    def test_empty_trigger_returns_none(self):
        trade = {"signal": "PUT", "entry_trigger": ""}
        r = check_entry_trigger_met(trade, index_open=23400.0)
        assert r["met"] is None

    def test_banknifty_trigger_was_met_on_18may(self):
        """Real case: BankNifty opened at 53282 with trigger below 53337 — trigger was met."""
        trade = {
            "signal":        "PUT",
            "entry_trigger": "enter only if BankNifty spot trades BELOW 53337 in first 30 min",
        }
        r = check_entry_trigger_met(trade, index_open=53282.15)
        assert r["met"] is True  # trade was entered; it was still wrong directionally


# ---------------------------------------------------------------------------
# derive_data_quality
# ---------------------------------------------------------------------------

class TestDeriveDataQuality:
    def _meta_with_fii(self, fii=0.0, dii=0.0, nifty_pcr=1.0, bn_pcr=0.9):
        return {"fii_dii": {"fii_net_buy": fii, "dii_net_buy": dii},
                "nifty_pcr": nifty_pcr, "banknifty_pcr": bn_pcr}

    def _ctx_with_smart(self, smart="Data available"):
        return {"smart_money_direction": smart}

    def test_fii_zero_flagged(self):
        dq = derive_data_quality(self._meta_with_fii(), self._ctx_with_smart())
        assert "FII_DII_DATA_ZERO" in dq["flags"]
        assert not dq["safe_to_trade"]

    def test_participant_oi_missing_flagged(self):
        dq = derive_data_quality(
            self._meta_with_fii(fii=100.0),
            self._ctx_with_smart("Data unavailable for participant OI"),
        )
        assert "PARTICIPANT_OI_MISSING" in dq["flags"]

    def test_pcr_contrarian_bullish(self):
        dq = derive_data_quality(
            self._meta_with_fii(nifty_pcr=1.35),
            self._ctx_with_smart("Available"),
        )
        assert "PCR_CONTRARIAN_BULLISH" in dq["flags"]
        assert any("short-covering" in w.lower() for w in dq["warnings"])

    def test_pcr_normal_no_flag(self):
        dq = derive_data_quality(
            self._meta_with_fii(nifty_pcr=1.0),
            self._ctx_with_smart("FII net long 60%"),
        )
        assert "PCR_CONTRARIAN_BULLISH" not in dq["flags"]

    def test_all_good_safe_to_trade(self):
        dq = derive_data_quality(
            self._meta_with_fii(fii=1200.0, dii=800.0, nifty_pcr=1.1),
            self._ctx_with_smart("FII futures 62% long — institutional bullish"),
        )
        assert dq["safe_to_trade"] is True
        assert dq["flags"] == []

    def test_missing_layers_penalty_note(self):
        dq = derive_data_quality(self._meta_with_fii(), self._ctx_with_smart())
        assert "may be inflated" in dq["penalty_note"]


# ---------------------------------------------------------------------------
# NO_TRADE behavior
# ---------------------------------------------------------------------------

class TestNoTradeBehavior:
    def test_empty_accuracy_log_reports_zero(self):
        r = evaluate_accuracy_log([])
        assert r["total"] == 0

    def test_audit_with_no_trades(self):
        morning = {"trades": []}
        result = audit_predictions_vs_triggers(morning, [])
        assert result == []

    def test_conflicting_signal_both_triggers_ambiguous(self):
        """2026-05-18 real data: PUT wrong dir + sl_hit + t1_reached = ambiguous."""
        log = [
            {"symbol": "NIFTY", "date": "2026-05-18", "signal": "PUT",
             "was_correct": False, "outcome": "SL_HIT",
             "sl_hit": True, "target_1_reached": True, "target_2_reached": False,
             "confidence": 7.1, "day_change_pct": 0.71},
            {"symbol": "BANKNIFTY", "date": "2026-05-18", "signal": "PUT",
             "was_correct": False, "outcome": "SL_HIT",
             "sl_hit": False, "target_1_reached": True, "target_2_reached": False,
             "confidence": 7.0, "day_change_pct": 0.48},
        ]
        r = evaluate_accuracy_log(log)
        assert r["direction_pct"] == 0.0
        # NIFTY entry is ambiguous (sl_hit + t1_reached + wrong direction)
        assert r["ambiguous_outcomes"] == 1
        # T1 should NOT be credited since direction was wrong
        assert r["t1_correct_pct"] == 0.0

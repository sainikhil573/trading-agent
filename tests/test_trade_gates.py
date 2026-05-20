"""
Tests for trade eligibility gating logic.
Gate priority: DATA_INSUFFICIENT → CONFLICTING_SIGNALS (PCR) → CONFLICTING_SIGNALS (max pain)
               → data quality penalty → volume cap → NO_TRADE threshold
Run: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analyzers.trade_gates import (
    gate_trade,
    apply_trade_gates,
    check_pcr_conflict,
    check_max_pain_conflict,
    check_volume_gate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(symbol="NIFTY", signal="PUT", confidence=8.0, vol_score=7):
    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": confidence,
        "confidence_breakdown": {"volume_confirmation": vol_score},
    }


def _nifty_sig(pcr=1.0, mp_gap=None, mp_direction=None):
    return {
        "pcr": {"pcr": pcr},
        "mp_gap": mp_gap,
        "mp_direction": mp_direction,
    }


def _bn_sig(pcr=1.0, mp_gap=None, mp_direction=None):
    return {
        "pcr": {"pcr": pcr},
        "mp_gap": mp_gap,
        "mp_direction": mp_direction,
    }


def _dq(flags=None, missing_layers=None):
    flags          = flags or []
    missing_layers = missing_layers or []
    return {"flags": flags, "missing_layers": missing_layers}


# ---------------------------------------------------------------------------
# check_pcr_conflict
# ---------------------------------------------------------------------------

class TestPCRConflict:
    def test_put_blocked_when_pcr_above_threshold(self):
        """PCR 1.344 (2026-05-18 NIFTY) ≥ 1.3 → PUT blocked as contrarian bullish."""
        trade = _trade(signal="PUT")
        blocked, reason = check_pcr_conflict(trade, _nifty_sig(pcr=1.344), _bn_sig())
        assert blocked is True
        assert "contrarian BULLISH" in reason

    def test_call_blocked_when_pcr_below_threshold(self):
        """PCR 0.55 ≤ 0.6 → CALL blocked as contrarian bearish."""
        trade = _trade(signal="CALL")
        blocked, reason = check_pcr_conflict(trade, _nifty_sig(pcr=0.55), _bn_sig())
        assert blocked is True
        assert "contrarian BEARISH" in reason

    def test_put_allowed_when_pcr_just_below_threshold(self):
        """PCR 1.29 < 1.3 → PUT not blocked."""
        trade = _trade(signal="PUT")
        blocked, _ = check_pcr_conflict(trade, _nifty_sig(pcr=1.29), _bn_sig())
        assert blocked is False

    def test_call_allowed_when_pcr_just_above_threshold(self):
        """PCR 0.65 > 0.6 → CALL not blocked."""
        trade = _trade(signal="CALL")
        blocked, _ = check_pcr_conflict(trade, _nifty_sig(pcr=0.65), _bn_sig())
        assert blocked is False

    def test_banknifty_uses_bn_signal(self):
        trade = _trade(symbol="BANKNIFTY", signal="PUT")
        blocked, _ = check_pcr_conflict(trade, _nifty_sig(pcr=0.9), _bn_sig(pcr=1.35))
        assert blocked is True

    def test_unknown_symbol_returns_no_block(self):
        trade = _trade(symbol="SENSEX", signal="PUT")
        blocked, _ = check_pcr_conflict(trade, _nifty_sig(pcr=1.5), _bn_sig(pcr=1.5))
        assert blocked is False


# ---------------------------------------------------------------------------
# check_max_pain_conflict
# ---------------------------------------------------------------------------

class TestMaxPainConflict:
    def test_put_blocked_when_max_pain_far_above_spot(self):
        """Max pain 1663 pts above spot (2026-05-18 BANKNIFTY) → PUT blocked."""
        trade = _trade(symbol="BANKNIFTY", signal="PUT")
        blocked, reason = check_max_pain_conflict(
            trade, _nifty_sig(), _bn_sig(mp_gap=1663, mp_direction="ABOVE_SPOT")
        )
        assert blocked is True
        assert "gravitational pull" in reason.lower() or "upward" in reason.lower()

    def test_call_blocked_when_max_pain_far_below_spot(self):
        """Max pain 1200 pts below spot → CALL blocked."""
        trade = _trade(signal="CALL")
        blocked, reason = check_max_pain_conflict(
            trade, _nifty_sig(mp_gap=-1200, mp_direction="BELOW_SPOT"), _bn_sig()
        )
        assert blocked is True

    def test_put_allowed_when_gap_below_threshold(self):
        """Max pain 800 pts above spot < 1000 threshold → not blocked."""
        trade = _trade(signal="PUT")
        blocked, _ = check_max_pain_conflict(
            trade, _nifty_sig(mp_gap=800, mp_direction="ABOVE_SPOT"), _bn_sig()
        )
        assert blocked is False

    def test_put_allowed_when_max_pain_below_spot(self):
        """Max pain below spot → downward pull → PUT not opposed."""
        trade = _trade(signal="PUT")
        blocked, _ = check_max_pain_conflict(
            trade, _nifty_sig(mp_gap=-1500, mp_direction="BELOW_SPOT"), _bn_sig()
        )
        assert blocked is False

    def test_no_mp_data_returns_no_block(self):
        trade = _trade(signal="PUT")
        blocked, _ = check_max_pain_conflict(trade, _nifty_sig(), _bn_sig())
        assert blocked is False


# ---------------------------------------------------------------------------
# check_volume_gate
# ---------------------------------------------------------------------------

class TestVolumeGate:
    def test_low_volume_flagged(self):
        trade = _trade(vol_score=3)
        is_low, score = check_volume_gate(trade)
        assert is_low is True
        assert score == 3.0

    def test_volume_at_threshold_not_flagged(self):
        trade = _trade(vol_score=5)
        is_low, _ = check_volume_gate(trade)
        assert is_low is False

    def test_missing_volume_not_penalized(self):
        trade = {"symbol": "NIFTY", "signal": "PUT", "confidence": 8.0,
                 "confidence_breakdown": {}}
        is_low, score = check_volume_gate(trade)
        assert is_low is False
        assert score == 10.0

    def test_no_breakdown_key_not_penalized(self):
        trade = {"symbol": "NIFTY", "signal": "PUT", "confidence": 8.0}
        is_low, score = check_volume_gate(trade)
        assert is_low is False


# ---------------------------------------------------------------------------
# gate_trade — full gate pipeline
# ---------------------------------------------------------------------------

class TestGateTrade:
    def test_trade_allowed_when_all_gates_pass(self):
        """High confidence, normal PCR, no max pain conflict, no data issues."""
        trade  = _trade(signal="CALL", confidence=8.0, vol_score=7)
        gated  = gate_trade(trade, _nifty_sig(pcr=1.0), _bn_sig(), _dq())
        assert gated["gate_status"] == "TRADE_ALLOWED"
        assert gated["gate_effective_confidence"] == 8.0
        assert gated["gate_data_penalty"] == 0.0

    def test_data_insufficient_blocks_when_both_layers_missing(self):
        """Both FII/DII and Participant OI missing → DATA_INSUFFICIENT hard block."""
        dq    = _dq(
            flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
            missing_layers=["Institutional Flow", "Smart Money OI"],
        )
        trade = _trade(confidence=9.0)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), dq)
        assert gated["gate_status"] == "DATA_INSUFFICIENT"

    def test_pcr_conflict_blocks_put(self):
        trade = _trade(signal="PUT", confidence=8.5)
        gated = gate_trade(trade, _nifty_sig(pcr=1.35), _bn_sig(), _dq())
        assert gated["gate_status"] == "CONFLICTING_SIGNALS"

    def test_max_pain_conflict_blocks_put(self):
        trade = _trade(signal="PUT", confidence=8.5)
        gated = gate_trade(
            trade,
            _nifty_sig(mp_gap=1200, mp_direction="ABOVE_SPOT"),
            _bn_sig(),
            _dq(),
        )
        assert gated["gate_status"] == "CONFLICTING_SIGNALS"

    def test_fii_missing_reduces_confidence(self):
        """One missing layer → -0.5 confidence penalty applied."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO"], missing_layers=[])
        trade = _trade(confidence=7.4, vol_score=6)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), dq)
        assert gated["gate_data_penalty"] == 0.5
        assert gated["gate_effective_confidence"] == pytest.approx(6.9)

    def test_fii_missing_drops_below_floor_becomes_no_trade(self):
        """Confidence 7.4 − 0.5 (FII missing) = 6.9 < 7.0 → NO_TRADE."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO"], missing_layers=[])
        trade = _trade(confidence=7.4, vol_score=6)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), dq)
        assert gated["gate_status"] == "NO_TRADE"

    def test_low_volume_caps_confidence(self):
        """Volume score 3 < 5 → cap to 6.8 → if that drops below 7.0 → NO_TRADE."""
        trade = _trade(confidence=9.0, vol_score=3)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), _dq())
        assert gated["gate_confidence_cap"] == 6.8
        assert gated["gate_effective_confidence"] == 6.8
        assert gated["gate_status"] == "NO_TRADE"

    def test_volume_cap_only_applies_if_above_cap(self):
        """Confidence already 6.5 with low volume — cap at 6.8 doesn't lower it."""
        trade = _trade(confidence=7.5, vol_score=2)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), _dq())
        assert gated["gate_effective_confidence"] == 6.8

    def test_original_confidence_preserved(self):
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
                    missing_layers=["a", "b"])
        trade = _trade(confidence=9.0)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), dq)
        assert gated["gate_original_confidence"] == 9.0

    def test_data_insufficient_takes_priority_over_pcr(self):
        """Gate 1 (DATA_INSUFFICIENT) should fire before Gate 2 (PCR)."""
        dq    = _dq(missing_layers=["FII", "OI"])
        trade = _trade(signal="PUT", confidence=8.0)
        gated = gate_trade(trade, _nifty_sig(pcr=1.5), _bn_sig(), dq)
        assert gated["gate_status"] == "DATA_INSUFFICIENT"

    def test_no_trade_when_effective_confidence_below_floor(self):
        """Confidence 6.8 < 7.0 → NO_TRADE even with good data."""
        trade = _trade(confidence=6.8, vol_score=7)
        gated = gate_trade(trade, _nifty_sig(), _bn_sig(), _dq())
        assert gated["gate_status"] == "NO_TRADE"


# ---------------------------------------------------------------------------
# apply_trade_gates — brief-level split
# ---------------------------------------------------------------------------

class TestApplyTradeGates:
    def _make_brief(self, trades):
        return {"trades": trades, "market_context": {}}

    def test_allowed_trades_stay_in_trades(self):
        brief = self._make_brief([_trade(signal="CALL", confidence=8.0, vol_score=7)])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.0), _bn_sig())
        assert len(result["trades"]) == 1
        assert len(result["trades_gated_out"]) == 0
        assert result["_gate_summary"]["allowed"] == 1

    def test_blocked_trades_move_to_gated_out(self):
        trades = [
            _trade(signal="PUT", confidence=8.0, vol_score=7),  # PCR will block
            _trade(signal="CALL", confidence=8.0, vol_score=7), # allowed
        ]
        brief  = self._make_brief(trades)
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.35), _bn_sig())
        assert len(result["trades"]) == 1
        assert len(result["trades_gated_out"]) == 1
        assert result["trades_gated_out"][0]["gate_status"] == "CONFLICTING_SIGNALS"

    def test_gates_applied_flag_set(self):
        brief  = self._make_brief([])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(), _bn_sig())
        assert result["_gates_applied"] is True

    def test_gate_summary_counts_correct(self):
        trades = [
            _trade(signal="PUT", confidence=8.0, vol_score=7),  # blocked by PCR
            _trade(signal="PUT", confidence=8.0, vol_score=7),  # blocked by PCR
            _trade(symbol="BANKNIFTY", signal="CALL", confidence=8.5, vol_score=6),  # allowed
        ]
        brief  = self._make_brief(trades)
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.35), _bn_sig())
        gs = result["_gate_summary"]
        assert gs["allowed"]  == 1
        assert gs["blocked"]  == 2

    def test_empty_brief_returns_cleanly(self):
        brief  = self._make_brief([])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(), _bn_sig())
        assert result["trades"]           == []
        assert result["trades_gated_out"] == []
        assert result["_gate_summary"]["allowed"] == 0

    def test_data_penalty_in_summary(self):
        """FII + OI both missing → 1.0 total penalty reflected in gate_summary."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
                    missing_layers=["FII", "OI"])
        brief  = self._make_brief([_trade(confidence=9.0)])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        assert result["_gate_summary"]["data_penalty"] == 1.0

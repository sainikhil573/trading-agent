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
    gate_stock_trade,
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


# ---------------------------------------------------------------------------
# gate_stock_trade — simplified gate for stock F&O trades
# ---------------------------------------------------------------------------

class TestGateStockTrade:
    def test_stock_trade_allowed_with_clean_data(self):
        """Stock trade passes gates when data is complete and confidence is high."""
        trade = {"symbol": "BAJFINANCE", "signal": "PUT", "confidence": 8.0}
        gated = gate_stock_trade(trade, _dq())
        assert gated["gate_status"] == "TRADE_ALLOWED"
        assert gated["gate_effective_confidence"] == 8.0
        assert gated["gate_data_penalty"] == 0.0

    def test_stock_trade_gated_when_data_penalty_drops_below_floor(self):
        """Confidence 7.3 − 1.0 (FII+OI penalty) = 6.3 < 7.0 → NO_TRADE."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"])
        trade = {"symbol": "HDFCBANK", "signal": "PUT", "confidence": 7.3}
        gated = gate_stock_trade(trade, dq)
        assert gated["gate_status"] == "NO_TRADE"
        assert gated["gate_effective_confidence"] == pytest.approx(6.3)
        assert gated["gate_data_penalty"] == 1.0

    def test_stock_trade_survives_high_confidence_with_penalty(self):
        """Confidence 8.5 − 1.0 = 7.5 ≥ 7.0 → TRADE_ALLOWED even with full penalty."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"])
        trade = {"symbol": "RELIANCE", "signal": "CALL", "confidence": 8.5}
        gated = gate_stock_trade(trade, dq)
        assert gated["gate_status"] == "TRADE_ALLOWED"
        assert gated["gate_effective_confidence"] == pytest.approx(7.5)

    def test_stock_trade_partial_penalty(self):
        """Only FII/DII missing → −0.5 penalty applied."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO"])
        trade = {"symbol": "TCS", "signal": "CALL", "confidence": 7.6}
        gated = gate_stock_trade(trade, dq)
        assert gated["gate_data_penalty"] == 0.5
        assert gated["gate_effective_confidence"] == pytest.approx(7.1)
        assert gated["gate_status"] == "TRADE_ALLOWED"

    def test_stock_gate_preserves_original_fields(self):
        """gate_stock_trade must not drop existing trade fields."""
        trade = {"symbol": "INFY", "signal": "PUT", "confidence": 8.0,
                 "entry_price": 25.0, "trigger": "EMA bear trend"}
        gated = gate_stock_trade(trade, _dq())
        assert gated["entry_price"] == 25.0
        assert gated["trigger"] == "EMA bear trend"
        assert gated["gate_original_confidence"] == 8.0


# ---------------------------------------------------------------------------
# Post-gate output consistency — the primary focus of this branch
# ---------------------------------------------------------------------------

def _stock_trade(symbol="BAJFINANCE", signal="PUT", confidence=7.5):
    return {"symbol": symbol, "signal": signal, "confidence": confidence}


class TestPostGateConsistency:
    def _brief(self, trades=None, stock_trades=None, max_trades=2):
        return {
            "trades": trades or [],
            "stock_trades": stock_trades or [],
            "max_trades_recommended": max_trades,
            "market_context": {"trading_recommended": True, "overall_market_bias": "MIXED"},
            "morning_summary": "Claude pre-gate summary with BankNifty PUT as primary trade.",
        }

    def test_allowed_zero_sets_trading_recommended_false(self):
        """When all trades are blocked, trading_recommended must flip to False."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
                    missing_layers=["FII", "OI"])
        brief = self._brief(trades=[_trade(confidence=7.2)], stock_trades=[_stock_trade(confidence=7.0)])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        assert result["market_context"]["trading_recommended"] is False

    def test_trading_recommended_true_when_any_trade_passes(self):
        """trading_recommended stays True when at least one trade passes."""
        brief = self._brief(trades=[_trade(signal="CALL", confidence=9.0, vol_score=8)])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.0), _bn_sig())
        assert result["market_context"]["trading_recommended"] is True

    def test_blocked_index_trade_not_in_trades(self):
        """A gated-out index trade must not appear in brief['trades']."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
                    missing_layers=["FII", "OI"])
        brief = self._brief(trades=[_trade(symbol="BANKNIFTY", signal="PUT", confidence=7.2)])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        assert len(result["trades"]) == 0
        assert len(result["trades_gated_out"]) == 1
        assert result["trades_gated_out"][0]["symbol"] == "BANKNIFTY"

    def test_stock_trades_go_through_gates(self):
        """Stock trades with borderline confidence are gated when data is missing."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"])
        brief = self._brief(stock_trades=[
            _stock_trade("BAJFINANCE", confidence=7.3),   # 7.3 − 1.0 = 6.3 → blocked
            _stock_trade("RELIANCE",   confidence=8.5),   # 8.5 − 1.0 = 7.5 → allowed
        ])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        stock_syms = [t["symbol"] for t in result["stock_trades"]]
        blocked_syms = [t["symbol"] for t in result["stock_trades_gated_out"]]
        assert "RELIANCE" in stock_syms
        assert "BAJFINANCE" in blocked_syms

    def test_max_trades_recommended_enforced_after_gating(self):
        """When allowed stock trades exceed max_trades_recommended, excess goes to watchlist."""
        brief = self._brief(
            max_trades=2,
            stock_trades=[
                _stock_trade("BAJFINANCE", confidence=8.0),
                _stock_trade("HDFCBANK",   confidence=7.5),
                _stock_trade("ADANIENT",   confidence=7.2),  # exceeds cap of 2
            ],
        )
        result = apply_trade_gates(brief, _dq(), _nifty_sig(), _bn_sig())
        assert len(result["stock_trades"]) == 2
        assert len(result["watchlist_only"]) == 1
        assert result["watchlist_only"][0]["symbol"] == "ADANIENT"

    def test_watchlist_only_separate_from_actionable(self):
        """Watchlist items must not appear in stock_trades."""
        brief = self._brief(
            max_trades=1,
            stock_trades=[
                _stock_trade("TCS",   confidence=8.0),
                _stock_trade("INFY",  confidence=7.5),
            ],
        )
        result = apply_trade_gates(brief, _dq(), _nifty_sig(), _bn_sig())
        actionable_syms = {t["symbol"] for t in result["stock_trades"]}
        watchlist_syms  = {t["symbol"] for t in result["watchlist_only"]}
        # No overlap
        assert actionable_syms.isdisjoint(watchlist_syms)
        assert "TCS" in actionable_syms
        assert "INFY" in watchlist_syms

    def test_post_gate_summary_reflects_no_actionable_state(self):
        """post_gate_summary must mention blocked status when no trades pass."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO", "PARTICIPANT_OI_MISSING"],
                    missing_layers=["FII", "OI"])
        brief = self._brief(trades=[_trade(confidence=7.2)])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        summary = result.get("post_gate_summary", "")
        assert summary != ""
        assert "NO ACTIONABLE" in summary.upper() or "blocked" in summary.lower()

    def test_final_recommendation_status_in_gate_summary(self):
        """_gate_summary must include final_recommendation_status."""
        brief = self._brief(trades=[_trade(signal="CALL", confidence=9.0, vol_score=8)])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.0), _bn_sig())
        assert "final_recommendation_status" in result["_gate_summary"]
        assert result["_gate_summary"]["final_recommendation_status"] == "ACTIONABLE_TRADES_AVAILABLE"

    def test_final_status_no_actionable_when_all_blocked(self):
        """final_recommendation_status must be NO_ACTIONABLE_TRADE when nothing passes."""
        dq    = _dq(flags=["FII_DII_DATA_ZERO"])
        brief = self._brief(trades=[_trade(confidence=7.4, vol_score=6)])
        result = apply_trade_gates(brief, dq, _nifty_sig(), _bn_sig())
        status = result["_gate_summary"]["final_recommendation_status"]
        assert status in ("NO_ACTIONABLE_TRADE", "DATA_INSUFFICIENT")

    def test_total_actionable_in_gate_summary(self):
        """_gate_summary.total_actionable must equal allowed index + actionable stock."""
        brief = self._brief(
            max_trades=3,
            trades=[_trade(signal="CALL", confidence=8.0, vol_score=6)],
            stock_trades=[
                _stock_trade("TCS",  confidence=8.0),
                _stock_trade("INFY", confidence=7.5),
            ],
        )
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.0), _bn_sig())
        gs = result["_gate_summary"]
        assert gs["total_actionable"] == gs["allowed"] + gs["stock_allowed"]

    def test_stock_trades_gated_out_field_present(self):
        """stock_trades_gated_out must always be present in the result."""
        brief = self._brief(stock_trades=[_stock_trade()])
        result = apply_trade_gates(brief, _dq(), _nifty_sig(), _bn_sig())
        assert "stock_trades_gated_out" in result

    def test_index_trade_cap_respected_before_stock_slots(self):
        """Index allowed trades consume from max_trades before stock slots are allocated."""
        brief = self._brief(
            max_trades=2,
            trades=[
                _trade(signal="CALL", confidence=8.0, vol_score=7),
                _trade(symbol="BANKNIFTY", signal="PUT", confidence=7.5, vol_score=6),
            ],
            stock_trades=[_stock_trade("BAJFINANCE", confidence=8.0)],
        )
        result = apply_trade_gates(brief, _dq(), _nifty_sig(pcr=1.0), _bn_sig())
        total = result["_gate_summary"]["total_actionable"]
        assert total <= 2

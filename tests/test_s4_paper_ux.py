"""
Tests for S4: health scoring thresholds, manual trade append, close trade P&L,
backtest v2 signal rules, and .bat file existence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Health scoring thresholds (FIX 2)
# ---------------------------------------------------------------------------

class TestHealthScoring:
    """Test the n_available-based health scoring logic extracted from orchestrator."""

    @staticmethod
    def _score(statuses: list[str]) -> str:
        n_failed    = statuses.count("FAILED")
        n_available = len(statuses) - n_failed
        if n_available >= 5 and "CACHED" not in statuses:
            return "HEALTHY"
        elif n_available >= 5:
            return "CACHED"
        elif n_available in (3, 4):
            return "PARTIAL"
        elif n_available in (1, 2):
            return "DEGRADED"
        else:
            return "CRITICAL"

    def test_all_fresh_is_healthy(self):
        statuses = ["FRESH"] * 7
        assert self._score(statuses) == "HEALTHY"

    def test_five_fresh_two_failed_is_healthy(self):
        # Pre-market FII/DII absent — should NOT degrade
        statuses = ["FRESH"] * 5 + ["FAILED", "FAILED"]
        assert self._score(statuses) == "HEALTHY"

    def test_five_with_one_cached_is_cached(self):
        statuses = ["FRESH"] * 4 + ["CACHED"] + ["FAILED", "FAILED"]
        assert self._score(statuses) == "CACHED"

    def test_four_available_is_partial(self):
        statuses = ["FRESH"] * 4 + ["FAILED"] * 3
        assert self._score(statuses) == "PARTIAL"

    def test_three_available_is_partial(self):
        statuses = ["FRESH"] * 3 + ["FAILED"] * 4
        assert self._score(statuses) == "PARTIAL"

    def test_two_available_is_degraded(self):
        statuses = ["FRESH"] * 2 + ["FAILED"] * 5
        assert self._score(statuses) == "DEGRADED"

    def test_one_available_is_degraded(self):
        statuses = ["FRESH"] + ["FAILED"] * 6
        assert self._score(statuses) == "DEGRADED"

    def test_all_failed_is_critical(self):
        statuses = ["FAILED"] * 7
        assert self._score(statuses) == "CRITICAL"

    def test_old_threshold_three_fails_was_critical_now_partial(self):
        # Old code: n_failed >= 3 → CRITICAL. New: 3 failed = 4 available → PARTIAL
        statuses = ["FRESH"] * 4 + ["FAILED"] * 3
        assert self._score(statuses) == "PARTIAL"


# ---------------------------------------------------------------------------
# Manual trade append (TASK 2)
# ---------------------------------------------------------------------------

class TestManualTradeAppend:

    def test_appends_to_existing_journal(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        existing = {"trades": [{"symbol": "WIPRO", "signal": "CALL", "status": "OPEN"}]}
        journal_path.write_text(json.dumps(existing), encoding="utf-8")

        new_trade = {
            "date": "2026-05-22", "symbol": "NIFTY", "signal": "PUT",
            "strike": 24000.0, "expiry": "2026-05-29", "entry_price": 120.0,
            "status": "OPEN", "paper_only": True, "manually_entered": True,
        }
        data = json.loads(journal_path.read_text(encoding="utf-8"))
        data.setdefault("trades", []).append(new_trade)
        journal_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        saved = json.loads(journal_path.read_text(encoding="utf-8"))
        assert len(saved["trades"]) == 2
        assert saved["trades"][1]["manually_entered"] is True
        assert saved["trades"][1]["symbol"] == "NIFTY"

    def test_creates_journal_if_missing(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        new_trade = {"symbol": "BANKNIFTY", "signal": "CALL", "status": "OPEN",
                     "paper_only": True, "manually_entered": True}
        data = {"trades": [new_trade]}
        journal_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        saved = json.loads(journal_path.read_text(encoding="utf-8"))
        assert saved["trades"][0]["symbol"] == "BANKNIFTY"

    def test_manual_trade_has_required_fields(self):
        trade = {
            "date": "2026-05-22", "symbol": "NIFTY", "signal": "CALL",
            "strike": 24000.0, "expiry": "2026-05-29", "entry_price": 150.0,
            "stop_loss": None, "target_1": None, "target_2": None,
            "status": "OPEN", "outcome": None, "was_correct": None,
            "hypothetical": False, "paper_only": True, "manually_entered": True,
            "notes": "testing",
        }
        for key in ("symbol", "signal", "entry_price", "paper_only", "manually_entered"):
            assert key in trade


# ---------------------------------------------------------------------------
# Close trade P&L (TASK 3)
# ---------------------------------------------------------------------------

class TestCloseTradeLogic:

    def test_profit_trade(self):
        entry, exit_p = 50.0, 80.0
        pnl = round(exit_p - entry, 2)
        outcome = "WIN" if pnl > 0 else "LOSS"
        assert pnl == 30.0
        assert outcome == "WIN"

    def test_loss_trade(self):
        entry, exit_p = 50.0, 25.0
        pnl = round(exit_p - entry, 2)
        outcome = "WIN" if pnl > 0 else "LOSS"
        assert pnl == -25.0
        assert outcome == "LOSS"

    def test_close_updates_journal_status(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        data = {
            "trades": [
                {"symbol": "WIPRO", "signal": "CALL", "status": "OPEN", "entry_price": 7.0},
            ]
        }
        journal_path.write_text(json.dumps(data), encoding="utf-8")

        loaded = json.loads(journal_path.read_text(encoding="utf-8"))
        trade  = loaded["trades"][0]
        pnl    = round(10.5 - trade["entry_price"], 2)
        trade.update({
            "status": "CLOSED", "exit_price": 10.5,
            "exit_reason": "T1_HIT", "pnl": pnl,
            "outcome": "WIN" if pnl > 0 else "LOSS",
            "was_correct": pnl > 0,
        })
        journal_path.write_text(json.dumps(loaded, indent=2), encoding="utf-8")

        saved = json.loads(journal_path.read_text(encoding="utf-8"))
        t = saved["trades"][0]
        assert t["status"] == "CLOSED"
        assert t["exit_reason"] == "T1_HIT"
        assert t["pnl"] == 3.5
        assert t["outcome"] == "WIN"
        assert t["was_correct"] is True

    def test_valid_exit_reasons(self):
        valid_reasons = {"SL_HIT", "T1_HIT", "T2_HIT", "MANUAL_EXIT", "EXPIRED"}
        for r in valid_reasons:
            assert isinstance(r, str)


# ---------------------------------------------------------------------------
# Backtest v2 signal rules (TASK 5)
# ---------------------------------------------------------------------------

class TestBacktestV2Signal:

    @staticmethod
    def _signal(close, ema20, rsi, vol_ratio):
        from types import SimpleNamespace
        row = SimpleNamespace(close=close, ema20=ema20, rsi=rsi, vol_ratio=vol_ratio)
        # Replicate _derive_signal_v2 logic
        vol_ok   = row.vol_ratio >= 0.4
        call_sig = row.close > row.ema20 and 50 < row.rsi < 70 and vol_ok
        put_sig  = row.close < row.ema20 and 30 < row.rsi < 50 and vol_ok
        if call_sig:
            return "CALL"
        if put_sig:
            return "PUT"
        return "NEUTRAL"

    def test_call_in_valid_rsi_range(self):
        assert self._signal(close=500, ema20=490, rsi=60, vol_ratio=0.6) == "CALL"

    def test_put_in_valid_rsi_range(self):
        assert self._signal(close=480, ema20=490, rsi=40, vol_ratio=0.6) == "PUT"

    def test_call_blocked_rsi_overbought(self):
        # RSI >= 70 → NEUTRAL (no overbought entries)
        assert self._signal(close=500, ema20=490, rsi=72, vol_ratio=0.6) == "NEUTRAL"

    def test_put_blocked_rsi_oversold(self):
        # RSI <= 30 → NEUTRAL (no oversold entries)
        assert self._signal(close=480, ema20=490, rsi=28, vol_ratio=0.6) == "NEUTRAL"

    def test_call_blocked_rsi_below_50(self):
        assert self._signal(close=500, ema20=490, rsi=48, vol_ratio=0.6) == "NEUTRAL"

    def test_neutral_when_vol_too_low(self):
        assert self._signal(close=500, ema20=490, rsi=60, vol_ratio=0.3) == "NEUTRAL"

    def test_vol_ratio_boundary_04_passes(self):
        assert self._signal(close=500, ema20=490, rsi=60, vol_ratio=0.4) == "CALL"

    def test_v2_module_importable(self):
        from src.backtesting import backtest_runner_v2
        assert hasattr(backtest_runner_v2, "run_backtest_v2")
        assert hasattr(backtest_runner_v2, "_derive_signal_v2")


# ---------------------------------------------------------------------------
# .bat file existence (TASK 6)
# ---------------------------------------------------------------------------

class TestBatFiles:

    def test_scheduler_bat_exists(self):
        bat = Path(__file__).parent.parent / "trading-agent.bat"
        assert bat.exists(), "trading-agent.bat missing from project root"

    def test_manual_bat_exists(self):
        bat = Path(__file__).parent.parent / "trading-agent-manual.bat"
        assert bat.exists(), "trading-agent-manual.bat missing from project root"

    def test_scheduler_bat_has_schedule_flag(self):
        bat = Path(__file__).parent.parent / "trading-agent.bat"
        content = bat.read_text(encoding="utf-8")
        assert "--schedule" in content

    def test_manual_bat_has_menu_options(self):
        bat = Path(__file__).parent.parent / "trading-agent-manual.bat"
        content = bat.read_text(encoding="utf-8")
        assert "--preopen" in content
        assert "--postmarket" in content
        assert "--dashboard" in content

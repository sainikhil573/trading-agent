"""
Tests for intraday provider and outcome resolution.

Covers:
  - LocalCSVIntradayProvider: load, missing file, bad schema
  - UnavailableIntradayProvider: always empty, is_available=False
  - resolve_outcome_with_intraday: SL-first, target-first, ambiguous OHLC
  - data_source tracking (INTRADAY vs DAILY_OHLC)
  - OUTCOME_UNKNOWN excluded from win rate
  - check_data_availability: missing FII/DII and OI appear in warnings
Run: python -m pytest tests/ -v
"""

import sys
import csv
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.intraday_provider import (
    LocalCSVIntradayProvider,
    UnavailableIntradayProvider,
    get_default_provider,
    CANDLE_COLUMNS,
)
from src.analyzers.evaluation import (
    resolve_outcome_with_intraday,
    evaluate_accuracy_log,
)
from src.fetchers.data_availability import check_data_availability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal candle DataFrame from list of dicts."""
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col])
    return df.sort_values("datetime").reset_index(drop=True)


def _put_trade(entry=110.0, sl=55.0, t1=185.0, t2=260.0):
    return {
        "signal":      "PUT",
        "entry_price": entry,
        "stop_loss":   sl,
        "target_1":    t1,
        "target_2":    t2,
    }


def _call_trade(entry=110.0, sl=55.0, t1=185.0, t2=260.0):
    return {
        "signal":      "CALL",
        "entry_price": entry,
        "stop_loss":   sl,
        "target_1":    t1,
        "target_2":    t2,
    }


def _daily_ohlc(open_p, high_p, low_p, was_correct):
    return {"open_p": open_p, "high_p": high_p, "low_p": low_p, "was_correct": was_correct}


# ---------------------------------------------------------------------------
# UnavailableIntradayProvider
# ---------------------------------------------------------------------------

class TestUnavailableProvider:
    def test_is_not_available(self):
        p = UnavailableIntradayProvider()
        assert p.is_available() is False

    def test_returns_empty_dataframe(self):
        p   = UnavailableIntradayProvider()
        df  = p.get_candles("NIFTY", date.today())
        assert df.empty
        assert list(df.columns) == CANDLE_COLUMNS

    def test_does_not_crash(self):
        """Provider must never raise — graceful no-op."""
        p = UnavailableIntradayProvider()
        df = p.get_candles("ANYTHING", date(2020, 1, 1), timeframe="30m")
        assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# LocalCSVIntradayProvider
# ---------------------------------------------------------------------------

class TestLocalCSVProvider:
    def _write_csv(self, tmp_dir: Path, symbol: str, trading_date: date,
                   timeframe: str, rows: list[dict]) -> Path:
        ds   = trading_date.strftime("%Y%m%d")
        path = tmp_dir / f"{symbol}_{ds}_{timeframe}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CANDLE_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_loads_valid_csv(self, tmp_path):
        rows = [
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:15:00",
             "open": 22500, "high": 22550, "low": 22480, "close": 22520,
             "volume": 1000000, "timeframe": "5m"},
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:20:00",
             "open": 22520, "high": 22530, "low": 22440, "close": 22450,
             "volume": 950000, "timeframe": "5m"},
        ]
        self._write_csv(tmp_path, "NIFTY", date(2026, 5, 20), "5m", rows)
        p  = LocalCSVIntradayProvider(data_dir=tmp_path)
        df = p.get_candles("NIFTY", date(2026, 5, 20), "5m")
        assert len(df) == 2
        assert df.iloc[0]["open"] == 22500.0

    def test_returns_empty_when_file_missing(self, tmp_path):
        p  = LocalCSVIntradayProvider(data_dir=tmp_path)
        df = p.get_candles("BANKNIFTY", date(2026, 5, 20), "15m")
        assert df.empty

    def test_returns_empty_when_directory_missing(self, tmp_path):
        p  = LocalCSVIntradayProvider(data_dir=tmp_path / "nonexistent")
        df = p.get_candles("NIFTY", date(2026, 5, 20))
        assert df.empty

    def test_is_available_when_dir_exists(self, tmp_path):
        p = LocalCSVIntradayProvider(data_dir=tmp_path)
        assert p.is_available() is True

    def test_is_not_available_when_dir_missing(self, tmp_path):
        p = LocalCSVIntradayProvider(data_dir=tmp_path / "no_such_dir")
        assert p.is_available() is False

    def test_bad_schema_missing_column_returns_empty(self, tmp_path):
        path = tmp_path / "NIFTY_20260520_5m.csv"
        path.write_text("symbol,datetime,open,close\nNIFTY,2026-05-20 09:15:00,22500,22520\n")
        p  = LocalCSVIntradayProvider(data_dir=tmp_path)
        df = p.get_candles("NIFTY", date(2026, 5, 20))
        assert df.empty

    def test_list_available_returns_symbols(self, tmp_path):
        for sym in ("NIFTY", "BANKNIFTY"):
            rows = [{"symbol": sym, "datetime": "2026-05-20 09:15:00",
                     "open": 100, "high": 110, "low": 90, "close": 105,
                     "volume": 1000, "timeframe": "5m"}]
            self._write_csv(tmp_path, sym, date(2026, 5, 20), "5m", rows)
        p  = LocalCSVIntradayProvider(data_dir=tmp_path)
        syms = p.list_available(date(2026, 5, 20))
        assert set(syms) == {"NIFTY", "BANKNIFTY"}

    def test_list_available_empty_when_no_files(self, tmp_path):
        p    = LocalCSVIntradayProvider(data_dir=tmp_path)
        syms = p.list_available(date(2026, 5, 20))
        assert syms == []


# ---------------------------------------------------------------------------
# resolve_outcome_with_intraday — no candles (daily OHLC fallback)
# ---------------------------------------------------------------------------

class TestResolveOutcomeDailyFallback:
    def test_ambiguous_ohlc_is_outcome_unknown(self):
        """PUT wrong direction + both SL and T1 via OHLC H/L → OUTCOME_UNKNOWN."""
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # spot: open 23480, high 23700 (+220 bad for PUT → worst_prem 110-110=0 ≤ 55 → SL hit)
        #       low  23250 (-230 good for PUT → best_prem  110+115=225 ≥ 185 → T1 hit)
        ohlc = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, None)
        assert res["outcome"]      == "OUTCOME_UNKNOWN"
        assert res["path_ambiguous"] is True
        assert res["data_source"]  == "DAILY_OHLC"

    def test_sl_only_is_sl_hit(self):
        """Only SL extreme reached → SL_HIT regardless of direction."""
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # high only +140 pts → worst_prem = 110-70 = 40 ≤ 55 → SL hit; low only -30 → best 125 < 185
        ohlc = _daily_ohlc(open_p=23480, high_p=23620, low_p=23450, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, None)
        assert res["outcome"]    == "SL_HIT"
        assert res["sl_hit"]     is True
        assert res["data_source"] == "DAILY_OHLC"

    def test_target1_correct_direction_credited(self):
        """T1 reached + direction correct → TARGET_1_HIT."""
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # low -200 pts → best_prem = 110+100 = 210 ≥ 185 → T1 hit; high only +50 pts → no SL
        ohlc = _daily_ohlc(open_p=23480, high_p=23530, low_p=23280, was_correct=True)
        res  = resolve_outcome_with_intraday(trade, ohlc, None)
        assert res["outcome"]          == "TARGET_1_HIT"
        assert res["target_1_reached"] is True
        assert res["data_source"]      == "DAILY_OHLC"

    def test_direction_wrong_no_sl_is_direction_wrong(self):
        """Market went up, PUT direction wrong, SL not hit → DIRECTION_WRONG."""
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # high only +80 pts → worst_prem = 110-40 = 70 > 55 → no SL
        # low only -10 pts → best_prem = 110+5 = 115 < 185 → no T1
        ohlc = _daily_ohlc(open_p=23480, high_p=23560, low_p=23470, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, None)
        assert res["outcome"] == "DIRECTION_WRONG"

    def test_missing_entry_premium_is_outcome_unknown(self):
        trade = {"signal": "PUT", "entry_price": None, "stop_loss": 55.0, "target_1": 185.0}
        ohlc  = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        res   = resolve_outcome_with_intraday(trade, ohlc, None)
        assert res["outcome"] == "OUTCOME_UNKNOWN"


# ---------------------------------------------------------------------------
# resolve_outcome_with_intraday — WITH intraday candles
# ---------------------------------------------------------------------------

class TestResolveOutcomeIntraday:
    def test_sl_first_then_target_is_sl_hit(self):
        """
        PUT trade. First candle spikes UP (SL hit). Later candle drops (T1 would hit).
        Chronological order: SL hit first → SL_HIT.
        """
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # Entry spot = 23480 (first candle open)
        # Candle 1: high 23700 (+220) → worst_prem = 110 - 110 = 0 ≤ 55 → SL hit at 09:15
        # Candle 2: low  23250 (-230) → best_prem  = 110 + 115 = 225 ≥ 185 → T1 hit at 09:20
        candles = _make_candle_df([
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:15:00",
             "open": 23480, "high": 23700, "low": 23460, "close": 23680, "volume": 1000, "timeframe": "5m"},
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:20:00",
             "open": 23680, "high": 23700, "low": 23250, "close": 23300, "volume": 1000, "timeframe": "5m"},
        ])
        ohlc = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert res["outcome"]      == "SL_HIT"
        assert res["data_source"]  == "INTRADAY"
        assert res["path_ambiguous"] is False

    def test_target_first_then_sl_is_target_hit(self):
        """
        PUT trade. First candle drops (T1 hit). Later candle spikes UP (SL hit).
        Chronological order: T1 hit first → TARGET_1_HIT.
        """
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        # Entry spot = 23480
        # Candle 1: low 23250 (-230) → best_prem = 110+115 = 225 ≥ 185 → T1 hit at 09:15
        # Candle 2: high 23700 → SL hit at 09:20 (but after T1 — irrelevant)
        candles = _make_candle_df([
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:15:00",
             "open": 23480, "high": 23490, "low": 23250, "close": 23260, "volume": 1000, "timeframe": "5m"},
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:20:00",
             "open": 23260, "high": 23700, "low": 23250, "close": 23680, "volume": 1000, "timeframe": "5m"},
        ])
        ohlc = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert res["outcome"]          == "TARGET_1_HIT"
        assert res["target_1_reached"] is True
        assert res["data_source"]      == "INTRADAY"

    def test_call_sl_first(self):
        """CALL trade. First candle drops sharply (SL hit) → SL_HIT."""
        trade = _call_trade(entry=110.0, sl=55.0, t1=185.0)
        # Entry spot = 23480
        # Candle 1: low 23250 (-230) → worst_prem = 110-115 = -5 ≤ 55 → SL hit
        candles = _make_candle_df([
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:15:00",
             "open": 23480, "high": 23485, "low": 23250, "close": 23260, "volume": 1000, "timeframe": "5m"},
        ])
        ohlc = _daily_ohlc(open_p=23480, high_p=23500, low_p=23250, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert res["outcome"]     == "SL_HIT"
        assert res["data_source"] == "INTRADAY"

    def test_empty_candles_falls_back_to_daily(self):
        """Empty DataFrame → daily OHLC fallback."""
        trade   = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        ohlc    = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        candles = pd.DataFrame(columns=CANDLE_COLUMNS)
        res     = resolve_outcome_with_intraday(trade, ohlc, candles)
        # Empty candles → None passed → daily OHLC → ambiguous → OUTCOME_UNKNOWN
        assert res["data_source"] == "DAILY_OHLC"

    def test_intraday_path_never_ambiguous(self):
        """When intraday candles are used, path_ambiguous is always False."""
        trade = _put_trade(entry=110.0, sl=55.0, t1=185.0)
        candles = _make_candle_df([
            {"symbol": "NIFTY", "datetime": "2026-05-20 09:15:00",
             "open": 23480, "high": 23700, "low": 23250, "close": 23300, "volume": 1000, "timeframe": "5m"},
        ])
        ohlc = _daily_ohlc(open_p=23480, high_p=23700, low_p=23250, was_correct=False)
        res  = resolve_outcome_with_intraday(trade, ohlc, candles)
        assert res["path_ambiguous"] is False
        assert res["data_source"]    == "INTRADAY"


# ---------------------------------------------------------------------------
# OUTCOME_UNKNOWN excluded from win rate
# ---------------------------------------------------------------------------

class TestUnknownExcludedFromWinRate:
    def test_unknown_not_counted_in_direction_pct(self):
        """OUTCOME_UNKNOWN entries must not inflate direction accuracy."""
        log = [
            {"was_correct": True,  "signal": "CALL", "outcome": "TARGET_1_HIT",  "confidence": 8.0},
            {"was_correct": False, "signal": "PUT",  "outcome": "OUTCOME_UNKNOWN", "confidence": 7.1},
            {"was_correct": False, "signal": "PUT",  "outcome": "SL_HIT",         "confidence": 7.0},
        ]
        r = evaluate_accuracy_log(log)
        # direction_pct is based on was_correct (1/3 = 33.3%)
        assert r["direction_pct"] == pytest.approx(33.3, abs=0.1)
        # t1_correct_pct: only TARGET_1_HIT entry counts (1/3)
        assert r["t1_correct_pct"] == pytest.approx(33.3, abs=0.1)
        # OUTCOME_UNKNOWN in ambiguous count
        assert r["outcome_unknown_count"] == 1
        assert r["ambiguous_outcomes"]    == 1

    def test_unknown_not_counted_as_sl_hit(self):
        """OUTCOME_UNKNOWN must not appear in sl_hit_pct."""
        log = [
            {"was_correct": False, "signal": "PUT", "outcome": "OUTCOME_UNKNOWN", "confidence": 7.1},
        ]
        r = evaluate_accuracy_log(log)
        assert r["sl_hit_pct"] == 0.0
        assert r["outcome_unknown_count"] == 1


# ---------------------------------------------------------------------------
# check_data_availability — FII/DII and OI missing appear in warnings
# ---------------------------------------------------------------------------

class TestDataAvailability:
    def _meta(self, fii=0.0, dii=0.0, vix=14.5, nifty_spot=23480.0,
              poi_error=True, gc=None):
        poi = {"error": "Could not fetch"} if poi_error else {
            "fii_futures_bias": "BULLISH", "fii_long_pct": 62.0
        }
        return {
            "vix_at_8am":       vix,
            "nifty_spot":       nifty_spot,
            "fii_dii":          {"fii_net_buy": fii, "dii_net_buy": dii},
            "participant_oi":   poi,
            "global_cues":      gc or {"S&P 500 Futures": {"last": 5000, "pct_change": 0.5}},
        }

    def test_fii_zero_appears_as_missing(self):
        da = check_data_availability(self._meta())
        names = [l["name"] for l in da["layers"] if not l["available"]]
        assert "FII / DII Cash Market" in names

    def test_participant_oi_error_appears_as_missing(self):
        da = check_data_availability(self._meta())
        names = [l["name"] for l in da["layers"] if not l["available"]]
        assert "Participant OI (Derivatives)" in names

    def test_both_missing_makes_not_actionable(self):
        da = check_data_availability(self._meta())
        assert da["actionable"] is False
        assert "FII / DII Cash Market" in da["missing_required"]
        assert "Participant OI (Derivatives)" in da["missing_required"]

    def test_all_present_is_actionable(self):
        meta = self._meta(fii=1200.0, dii=800.0, poi_error=False)
        da   = check_data_availability(meta)
        assert da["actionable"] is True
        assert da["missing_required"] == []

    def test_intraday_not_available_when_no_files(self):
        meta = self._meta(fii=1000.0, poi_error=False)
        da   = check_data_availability(meta, trading_date=date(2099, 1, 1))
        assert da["intraday_available"] is False

    def test_report_has_all_layers(self):
        da = check_data_availability(self._meta())
        keys = {l["key"] for l in da["layers"]}
        for expected in ("option_chain", "vix", "fii_dii", "participant_oi",
                         "global_cues", "technicals", "news", "intraday", "gift_nifty"):
            assert expected in keys

    def test_gift_nifty_always_unavailable(self):
        """GIFT Nifty has no source — always reported as missing."""
        da = check_data_availability(self._meta(fii=1000.0, poi_error=False))
        gift = next(l for l in da["layers"] if l["key"] == "gift_nifty")
        assert gift["available"] is False

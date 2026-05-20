"""
Tests for intraday CSV ingestion: validator, loader, evaluation integration.
Run: python -m pytest tests/test_intraday_csv.py -v
"""
import io
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.intraday_validator import validate_intraday_df
from src.fetchers.intraday_loader import (
    load_intraday_file,
    list_intraday_files,
    save_uploaded_csv,
)
from src.analyzers.evaluation import (
    get_first_30min_range,
    resolve_outcome_with_intraday,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_CSV = """\
symbol,datetime,open,high,low,close,volume,timeframe
NIFTY,2026-05-20 09:15:00,23000,23060,22990,23020,50000,5m
NIFTY,2026-05-20 09:20:00,23020,23080,23010,23050,48000,5m
NIFTY,2026-05-20 09:25:00,23050,23070,22990,23000,45000,5m
NIFTY,2026-05-20 09:30:00,23000,23010,22950,22960,52000,5m
NIFTY,2026-05-20 09:35:00,22960,22980,22900,22920,55000,5m
"""


def _df_from_csv(csv_text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv_text))


def _valid_df() -> pd.DataFrame:
    return _df_from_csv(_VALID_CSV)


# ---------------------------------------------------------------------------
# validate_intraday_df — schema checks
# ---------------------------------------------------------------------------

class TestValidateIntradayDf:
    def test_valid_df_passes(self):
        result = validate_intraday_df(_valid_df())
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["stats"]["rows"] == 5
        assert result["stats"]["symbols"] == ["NIFTY"]

    def test_missing_required_column_rejected(self):
        df = _valid_df().drop(columns=["volume"])
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("volume" in e.lower() for e in result["errors"])

    def test_multiple_missing_columns_reported(self):
        df = _valid_df().drop(columns=["open", "timeframe"])
        result = validate_intraday_df(df)
        assert result["valid"] is False
        # Both missing cols should appear in the single error message
        assert "open" in result["errors"][0] or "timeframe" in result["errors"][0]

    def test_non_numeric_ohlcv_rejected(self):
        # Inject a bad value directly in the CSV text so pandas reads the column as object dtype
        bad_csv = _VALID_CSV.replace(
            "23050,23070,22990,23000,45000,5m",
            "23050,23070,bad_value,23000,45000,5m",
        )
        df = _df_from_csv(bad_csv)
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("low" in e for e in result["errors"])

    def test_unparseable_datetime_rejected(self):
        df = _valid_df()
        df.loc[0, "datetime"] = "not-a-date"
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("datetime" in e.lower() for e in result["errors"])

    def test_high_less_than_low_rejected(self):
        df = _valid_df()
        df.loc[1, "high"] = 22800  # below the low of 23010
        df.loc[1, "low"]  = 23050
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("high < low" in e or "impossible" in e for e in result["errors"])

    def test_negative_volume_rejected(self):
        df = _valid_df()
        df.loc[0, "volume"] = -1
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("negative volume" in e for e in result["errors"])

    def test_multiple_symbols_rejected(self):
        df = _valid_df()
        df.loc[3, "symbol"] = "BANKNIFTY"
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("multiple symbols" in e.lower() for e in result["errors"])

    def test_unknown_timeframe_is_warning_not_error(self):
        df = _valid_df()
        df["timeframe"] = "99m"  # not in ALLOWED_TIMEFRAMES
        result = validate_intraday_df(df)
        # Unrecognised timeframe is a warning, not a blocking error
        assert result["valid"] is True
        assert any("timeframe" in w.lower() for w in result["warnings"])

    def test_open_outside_range_is_warning_not_error(self):
        df = _valid_df()
        df.loc[0, "open"] = 24000  # above high of 23060
        result = validate_intraday_df(df)
        assert result["valid"] is True
        assert any("open" in w for w in result["warnings"])

    def test_stats_date_range_correct(self):
        result = validate_intraday_df(_valid_df())
        assert result["stats"]["date_range"] == "2026-05-20 to 2026-05-20"

    def test_null_in_critical_column_rejected(self):
        df = _valid_df()
        df.loc[0, "open"] = None
        result = validate_intraday_df(df)
        assert result["valid"] is False
        assert any("open" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# load_intraday_file
# ---------------------------------------------------------------------------

class TestLoadIntradayFile:
    def test_valid_csv_file_loads(self, tmp_path):
        p = tmp_path / "NIFTY_20260520_5m.csv"
        p.write_text(_VALID_CSV)
        result = load_intraday_file(p)
        assert result["valid"] is True
        assert result["df"] is not None
        assert len(result["df"]) == 5

    def test_invalid_csv_file_returns_errors(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("symbol,datetime,open\nNIFTY,2026-05-20 09:15:00,23000\n")
        result = load_intraday_file(p)
        assert result["valid"] is False
        assert result["df"] is None
        assert result["errors"]

    def test_unreadable_file_returns_error(self, tmp_path):
        p = tmp_path / "ghost.csv"  # does not exist
        result = load_intraday_file(p)
        assert result["valid"] is False
        assert result["df"] is None

    def test_df_datetime_parsed_on_load(self, tmp_path):
        p = tmp_path / "NIFTY_20260520_5m.csv"
        p.write_text(_VALID_CSV)
        result = load_intraday_file(p)
        assert pd.api.types.is_datetime64_any_dtype(result["df"]["datetime"])

    def test_df_sorted_chronologically(self, tmp_path):
        # Write rows in reverse order
        reversed_csv = _VALID_CSV.splitlines()
        header = reversed_csv[0]
        rows   = list(reversed(reversed_csv[1:]))
        p = tmp_path / "NIFTY_20260520_5m.csv"
        p.write_text("\n".join([header] + rows) + "\n")
        result = load_intraday_file(p)
        assert result["valid"] is True
        dts = result["df"]["datetime"].tolist()
        assert dts == sorted(dts)


# ---------------------------------------------------------------------------
# list_intraday_files
# ---------------------------------------------------------------------------

class TestListIntradayFiles:
    def test_missing_directory_returns_empty_list(self, tmp_path):
        result = list_intraday_files(tmp_path / "nonexistent")
        assert result == []

    def test_lists_valid_files(self, tmp_path):
        (tmp_path / "NIFTY_20260520_5m.csv").write_text(_VALID_CSV)
        result = list_intraday_files(tmp_path)
        assert len(result) == 1
        assert result[0]["filename"] == "NIFTY_20260520_5m.csv"
        assert result[0]["valid"] is True

    def test_skips_schema_example(self, tmp_path):
        (tmp_path / "schema_example.csv").write_text(_VALID_CSV)
        (tmp_path / "NIFTY_20260520_5m.csv").write_text(_VALID_CSV)
        result = list_intraday_files(tmp_path)
        filenames = [r["filename"] for r in result]
        assert "schema_example.csv" not in filenames
        assert "NIFTY_20260520_5m.csv" in filenames

    def test_invalid_file_appears_with_errors(self, tmp_path):
        (tmp_path / "bad.csv").write_text("col1,col2\n1,2\n")
        result = list_intraday_files(tmp_path)
        assert len(result) == 1
        assert result[0]["valid"] is False
        assert result[0]["errors"]

    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = list_intraday_files(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# save_uploaded_csv
# ---------------------------------------------------------------------------

class TestSaveUploadedCsv:
    def test_valid_upload_saved_successfully(self, tmp_path):
        result = save_uploaded_csv(_VALID_CSV.encode(), data_dir=tmp_path)
        assert result["saved"] is True
        assert result["filename"] == "NIFTY_20260520_5m.csv"
        assert (tmp_path / "NIFTY_20260520_5m.csv").exists()

    def test_filename_inferred_from_content(self, tmp_path):
        result = save_uploaded_csv(_VALID_CSV, data_dir=tmp_path)
        assert result["saved"] is True
        assert "NIFTY" in result["filename"]
        assert "20260520" in result["filename"]
        assert "5m" in result["filename"]

    def test_invalid_content_not_saved(self, tmp_path):
        bad_csv = "col_a,col_b\n1,2\n3,4\n"
        result = save_uploaded_csv(bad_csv, data_dir=tmp_path)
        assert result["saved"] is False
        assert result["errors"]

    def test_overwrite_false_blocks_duplicate(self, tmp_path):
        save_uploaded_csv(_VALID_CSV, data_dir=tmp_path, overwrite=True)
        result = save_uploaded_csv(_VALID_CSV, data_dir=tmp_path, overwrite=False)
        assert result["saved"] is False
        assert any("already exists" in e or "overwrite" in e.lower() for e in result["errors"])

    def test_overwrite_true_replaces_existing(self, tmp_path):
        save_uploaded_csv(_VALID_CSV, data_dir=tmp_path, overwrite=True)
        result = save_uploaded_csv(_VALID_CSV, data_dir=tmp_path, overwrite=True)
        assert result["saved"] is True

    def test_stats_populated_on_success(self, tmp_path):
        result = save_uploaded_csv(_VALID_CSV, data_dir=tmp_path)
        assert result["stats"]["rows"] == 5
        assert result["stats"]["symbols"] == ["NIFTY"]


# ---------------------------------------------------------------------------
# get_first_30min_range
# ---------------------------------------------------------------------------

class TestGetFirst30MinRange:
    def _candles(self) -> pd.DataFrame:
        rows = [
            ("2026-05-20 09:15:00", 23000, 23060, 22990, 23020),
            ("2026-05-20 09:20:00", 23020, 23080, 23010, 23050),
            ("2026-05-20 09:25:00", 23050, 23070, 22980, 23000),
            ("2026-05-20 09:30:00", 23000, 23010, 22950, 22960),
            ("2026-05-20 09:35:00", 22960, 22980, 22900, 22920),  # outside 30-min window
            ("2026-05-20 09:45:00", 22920, 23100, 22850, 23050),  # outside 30-min window
        ]
        df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df

    def test_empty_candles_returns_unavailable(self):
        result = get_first_30min_range(pd.DataFrame())
        assert result["available"] is False
        assert result["high"] is None
        assert result["low"] is None
        assert result["candles_used"] == 0

    def test_none_candles_returns_unavailable(self):
        result = get_first_30min_range(None)
        assert result["available"] is False

    def test_returns_high_and_low_of_first_30_min(self):
        result = get_first_30min_range(self._candles())
        assert result["available"] is True
        # Candles within 09:15–09:45 window (<=09:45): rows 0-4 + row 5 is 09:45 which IS included
        # Actually the cutoff is open_time + 30min = 09:45, so <=09:45 includes row 5
        assert result["high"] is not None
        assert result["low"] is not None
        assert result["high"] >= result["low"]

    def test_candles_used_count_correct(self):
        # 09:15 + 30min = 09:45 → rows at 09:15, 09:20, 09:25, 09:30, 09:35, 09:45 all included
        result = get_first_30min_range(self._candles())
        assert result["candles_used"] == 6

    def test_high_is_max_of_window(self):
        result = get_first_30min_range(self._candles())
        # 09:45 candle has high=23100, which is the highest
        assert result["high"] == pytest.approx(23100.0)

    def test_low_is_min_of_window(self):
        result = get_first_30min_range(self._candles())
        # 09:45 candle has low=22850, which is the lowest
        assert result["low"] == pytest.approx(22850.0)


# ---------------------------------------------------------------------------
# resolve_outcome_with_intraday — target-first scenario
# ---------------------------------------------------------------------------

class TestResolveOutcomeIntraday:
    def _call_trade(self):
        return {
            "signal":      "CALL",
            "entry_price": 100.0,
            "stop_loss":   70.0,   # lose 30 pts → spot moves 60 against
            "target_1":    130.0,  # gain 30 pts → spot moves 60 in favour
            "target_2":    160.0,
        }

    def _daily_ohlc_correct(self):
        return {"open_p": 23000, "high_p": 23200, "low_p": 22940, "was_correct": True}

    def _daily_ohlc_wrong(self):
        return {"open_p": 23000, "high_p": 23060, "low_p": 22800, "was_correct": False}

    def _candles_target_first(self) -> pd.DataFrame:
        """CALL: spot rises first (T1 hit at candle 1), then drops to SL at candle 2."""
        rows = [
            # open=23000 → entry_spot=23000
            # candle 1: high=23060 → cum_high=23060 → best=100+(60*0.5)=130 → T1!
            #           low=23000  → cum_low=23000  → worst=100-(0*0.5)=100
            ("2026-05-20 09:15:00", 23000, 23060, 23000, 23020),
            # candle 2: high=23060, low=22860 → cum_low=22860 → worst=100-(140*0.5)=30 ≤ 70 → SL
            ("2026-05-20 09:20:00", 23020, 23060, 22860, 22880),
        ]
        df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df

    def _candles_sl_first(self) -> pd.DataFrame:
        """CALL: spot drops first (SL hit at candle 1), then recovers above T1 at candle 2."""
        rows = [
            # candle 1: high=23000, low=22880 → cum_low=22880 → worst=100-(120*0.5)=40 ≤ 70 → SL!
            ("2026-05-20 09:15:00", 23000, 23000, 22880, 22900),
            # candle 2: high=23100 → cum_high=23100 → best=100+(100*0.5)=150 ≥ 130 → T1 would fire
            ("2026-05-20 09:20:00", 22900, 23100, 22900, 23050),
        ]
        df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df

    def _candles_no_trigger(self) -> pd.DataFrame:
        """CALL: neither SL nor T1 touched — flat market."""
        rows = [
            ("2026-05-20 09:15:00", 23000, 23010, 22990, 23000),
            ("2026-05-20 09:20:00", 23000, 23015, 22985, 22995),
        ]
        df = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df

    def test_target_first_gives_target1_hit(self):
        result = resolve_outcome_with_intraday(
            self._call_trade(),
            self._daily_ohlc_correct(),
            self._candles_target_first(),
        )
        assert result["outcome"] == "TARGET_1_HIT"
        assert result["data_source"] == "INTRADAY"
        assert result["path_ambiguous"] is False

    def test_sl_first_gives_sl_hit(self):
        result = resolve_outcome_with_intraday(
            self._call_trade(),
            self._daily_ohlc_correct(),
            self._candles_sl_first(),
        )
        assert result["outcome"] == "SL_HIT"
        assert result["data_source"] == "INTRADAY"
        assert result["sl_hit"] is True
        assert result["target_1_reached"] is False

    def test_no_trigger_gives_direction_right(self):
        result = resolve_outcome_with_intraday(
            self._call_trade(),
            self._daily_ohlc_correct(),
            self._candles_no_trigger(),
        )
        assert result["outcome"] == "DIRECTION_RIGHT"
        assert result["data_source"] == "INTRADAY"

    def test_no_candles_falls_back_to_daily_ohlc(self):
        result = resolve_outcome_with_intraday(
            self._call_trade(),
            self._daily_ohlc_correct(),
            None,
        )
        assert result["data_source"] == "DAILY_OHLC"

    def test_empty_candles_falls_back_to_daily_ohlc(self):
        result = resolve_outcome_with_intraday(
            self._call_trade(),
            self._daily_ohlc_correct(),
            pd.DataFrame(),
        )
        assert result["data_source"] == "DAILY_OHLC"

    def test_no_candles_ambiguous_path_is_outcome_unknown(self):
        """No candles + daily OHLC shows both SL and T1 triggered (wrong direction) → UNKNOWN."""
        # PUT trade wrong direction: market went UP, but OHLC shows low enough to hit T1 too
        put_trade = {
            "signal":      "PUT",
            "entry_price": 100.0,
            "stop_loss":   70.0,
            "target_1":    130.0,
            "target_2":    None,
        }
        # open_p=23000, high_p=23100 (bad for PUT), low_p=22500 (good for PUT)
        # PUT: worst_move = 23100-23000=100 → worst_prem=100-50=50 ≤ 70 → sl_hit
        # PUT: best_move  = 23000-22500=500 → best_prem=100+250=350 ≥ 130 → t1_reached
        # was_correct=False → path_ambiguous → OUTCOME_UNKNOWN
        ohlc = {"open_p": 23000, "high_p": 23100, "low_p": 22500, "was_correct": False}
        result = resolve_outcome_with_intraday(put_trade, ohlc, None)
        assert result["outcome"] == "OUTCOME_UNKNOWN"
        assert result["path_ambiguous"] is True
        assert result["data_source"] == "DAILY_OHLC"

    def test_intraday_resolves_what_daily_cannot(self):
        """Same trade that yields OUTCOME_UNKNOWN via daily OHLC resolves via intraday."""
        put_trade = {
            "signal":      "PUT",
            "entry_price": 100.0,
            "stop_loss":   70.0,
            "target_1":    130.0,
            "target_2":    None,
        }
        ohlc = {"open_p": 23000, "high_p": 23100, "low_p": 22500, "was_correct": False}

        # Candles: entry_spot=23000. T1 is hit at candle 1 (downward move), then SL at candle 2.
        # PUT best_prem = entry_prem + (entry_spot - cum_low) * 0.5
        # candle 1: low=22400 → cum_low=22400 → best=100+(600*0.5)=400 ≥ 130 → T1! t1_time=c1
        #           high=23000 → cum_high=23000 → worst=100-(0*0.5)=100 → no SL
        # candle 2: high=23120 → cum_high=23120 → worst=100-(120*0.5)=40 ≤ 70 → SL. sl_time=c2
        # t1_time < sl_time → TARGET_1_HIT (not OUTCOME_UNKNOWN)
        rows = [
            ("2026-05-20 09:15:00", 23000, 23000, 22400, 22420),
            ("2026-05-20 09:20:00", 22420, 23120, 22400, 23100),
        ]
        candles = pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close"])
        candles["datetime"] = pd.to_datetime(candles["datetime"])

        result_daily    = resolve_outcome_with_intraday(put_trade, ohlc, None)
        result_intraday = resolve_outcome_with_intraday(put_trade, ohlc, candles)

        assert result_daily["outcome"]    == "OUTCOME_UNKNOWN"
        assert result_intraday["outcome"] == "TARGET_1_HIT"
        assert result_intraday["data_source"] == "INTRADAY"

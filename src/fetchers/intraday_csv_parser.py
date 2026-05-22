"""
Intraday candle CSV parser.
Supports Zerodha Kite and TradingView export formats.
Returns a normalised DataFrame with columns:
  [datetime, open, high, low, close, volume]
Never raises — all errors returned in result dict.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

_REQUIRED_COLS = {"datetime", "open", "high", "low", "close", "volume"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_candle_csv(content: bytes | str) -> dict:
    """
    Parse a Zerodha Kite or TradingView candle export CSV.

    Returns
    -------
    {
      "df":              pd.DataFrame | None,
      "format_detected": str,          # "zerodha" | "tradingview" | "unknown"
      "errors":          list[str],
      "warnings":        list[str],
    }

    Zerodha format variants:
      - date, time, open, high, low, close, volume  (two separate date+time columns)
      - Date, Open, High, Low, Close, Volume         (combined datetime column)

    TradingView format:
      - time, open, high, low, close, volume         (time is ISO8601 or Unix epoch)

    Output DataFrame always has columns: [datetime, open, high, low, close, volume]
    where datetime contains Python datetime objects (timezone-naive, local time).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ---- decode bytes ----
    if isinstance(content, bytes):
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                content = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            errors.append("Could not decode CSV bytes with utf-8 / latin-1 / cp1252")
            return {"df": None, "format_detected": "unknown", "errors": errors, "warnings": warnings}

    if not content or not content.strip():
        errors.append("Empty CSV content")
        return {"df": None, "format_detected": "unknown", "errors": errors, "warnings": warnings}

    # ---- parse raw CSV ----
    try:
        df_raw = pd.read_csv(io.StringIO(content))
    except Exception as exc:
        errors.append(f"pandas read_csv failed: {exc}")
        return {"df": None, "format_detected": "unknown", "errors": errors, "warnings": warnings}

    if df_raw.empty:
        errors.append("CSV parsed but contains no rows")
        return {"df": None, "format_detected": "unknown", "errors": errors, "warnings": warnings}

    cols_lower = [str(c).strip().lower() for c in df_raw.columns]
    df_raw.columns = cols_lower

    # ---- detect format ----
    format_detected, df = _try_zerodha(df_raw, cols_lower, errors, warnings)
    if df is None:
        format_detected, df = _try_tradingview(df_raw, cols_lower, errors, warnings)

    if df is None:
        errors.append(
            f"Unrecognised CSV format. Columns found: {list(df_raw.columns)}. "
            "Expected Zerodha (date+time or Date/Open/...) or TradingView (time/open/...)."
        )
        return {"df": None, "format_detected": "unknown", "errors": errors, "warnings": warnings}

    # ---- validate output ----
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        errors.append(f"Normalised DataFrame missing columns: {missing}")
        return {"df": None, "format_detected": format_detected, "errors": errors, "warnings": warnings}

    if df.empty:
        warnings.append("CSV parsed but produced zero rows after normalisation")

    return {"df": df, "format_detected": format_detected, "errors": errors, "warnings": warnings}


def compute_intraday_outcome(
    candles_df: pd.DataFrame,
    trade_dict: dict,
    atm_delta: float = 0.5,
) -> dict:
    """
    Compute intraday trade outcome from normalised candle data.

    Parameters
    ----------
    candles_df : DataFrame with columns [datetime, open, high, low, close, volume]
    trade_dict : Paper journal entry (needs signal, entry_price, stop_loss, target_1, target_2)
    atm_delta  : ATM delta used for premium estimation from spot moves (default 0.5)

    Returns
    -------
    {
      "entry_triggered": bool,
      "sl_hit":          bool,
      "t1_hit":          bool,
      "t2_hit":          bool,
      "final_close":     float | None,
      "outcome":         str,   # "T2_HIT" | "T1_HIT" | "SL_HIT" | "NO_ENTRY" | "OPEN" | "UNKNOWN"
    }
    Never raises.
    """
    result: dict = {
        "entry_triggered": False,
        "sl_hit":          False,
        "t1_hit":          False,
        "t2_hit":          False,
        "final_close":     None,
        "outcome":         "UNKNOWN",
    }

    try:
        if candles_df is None or candles_df.empty:
            result["outcome"] = "UNKNOWN"
            return result

        signal      = (trade_dict.get("signal") or "").upper()
        entry_price = trade_dict.get("entry_price")
        sl          = trade_dict.get("stop_loss")
        t1          = trade_dict.get("target_1")
        t2          = trade_dict.get("target_2")

        if not all(isinstance(v, (int, float)) for v in [entry_price, sl, t1]):
            result["outcome"] = "UNKNOWN"
            return result

        df = candles_df.copy().reset_index(drop=True)
        final_close = float(df.iloc[-1]["close"]) if not df.empty else None
        result["final_close"] = final_close

        is_call = signal == "CALL"

        # Walk candles chronologically to determine sequence
        entry_triggered = False
        sl_hit = False
        t1_hit = False
        t2_hit = False

        for _, row in df.iterrows():
            high = float(row["high"])
            low  = float(row["low"])

            if not entry_triggered:
                # Check if entry premium crossed by approximating spot → premium
                if is_call:
                    if high >= entry_price:
                        entry_triggered = True
                else:
                    if low <= entry_price:
                        entry_triggered = True
                continue

            # After entry: check SL and targets in chronological order
            # For CALL: SL means price dropped to sl; T1/T2 means price rose to target
            # For PUT: SL means price rose to sl; T1/T2 means price dropped to target
            if is_call:
                if low <= sl:
                    sl_hit = True
                    break
                if t2 is not None and isinstance(t2, (int, float)) and high >= t2:
                    t1_hit = True
                    t2_hit = True
                    break
                if high >= t1:
                    t1_hit = True
                    # Continue to check T2
            else:
                if high >= sl:
                    sl_hit = True
                    break
                if t2 is not None and isinstance(t2, (int, float)) and low <= t2:
                    t1_hit = True
                    t2_hit = True
                    break
                if low <= t1:
                    t1_hit = True

        result["entry_triggered"] = entry_triggered
        result["sl_hit"]          = sl_hit
        result["t1_hit"]          = t1_hit
        result["t2_hit"]          = t2_hit

        if not entry_triggered:
            result["outcome"] = "NO_ENTRY"
        elif sl_hit:
            result["outcome"] = "SL_HIT"
        elif t2_hit:
            result["outcome"] = "T2_HIT"
        elif t1_hit:
            result["outcome"] = "T1_HIT"
        else:
            result["outcome"] = "OPEN"

    except Exception as exc:
        logger.warning("compute_intraday_outcome failed: %s", exc)
        result["outcome"] = "UNKNOWN"

    return result


# ---------------------------------------------------------------------------
# Format parsers (internal)
# ---------------------------------------------------------------------------

def _try_zerodha(
    df: pd.DataFrame, cols: list[str],
    errors: list, warnings: list,
) -> tuple[str, pd.DataFrame | None]:
    """
    Attempt to parse Zerodha Kite CSV.
    Variant A: columns = [date, time, open, high, low, close, volume]
    Variant B: columns = [date, open, high, low, close, volume] (combined date/datetime)
    """
    has_date_time = "date" in cols and "time" in cols
    has_ohlcv     = all(c in cols for c in ("open", "high", "low", "close", "volume"))

    # Variant A — separate date + time columns
    if has_date_time and has_ohlcv:
        try:
            combined = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
            dt_series = pd.to_datetime(combined, errors="coerce")
            null_count = dt_series.isna().sum()
            if null_count > len(dt_series) * 0.5:
                warnings.append(f"Zerodha variant A: {null_count}/{len(dt_series)} datetime parse failures")
            result_df = _make_output_df(dt_series, df, cols)
            if result_df is not None:
                return "zerodha", result_df
        except Exception as exc:
            warnings.append(f"Zerodha variant A parse error: {exc}")

    # Variant B — single Date column containing combined datetime
    if "date" in cols and has_ohlcv and not has_date_time:
        try:
            dt_series = pd.to_datetime(df["date"].astype(str).str.strip(),
                                       errors="coerce")
            null_count = dt_series.isna().sum()
            if null_count > len(dt_series) * 0.5:
                warnings.append(f"Zerodha variant B: {null_count}/{len(dt_series)} datetime parse failures")
            result_df = _make_output_df(dt_series, df, cols)
            if result_df is not None:
                return "zerodha", result_df
        except Exception as exc:
            warnings.append(f"Zerodha variant B parse error: {exc}")

    return "unknown", None


def _try_tradingview(
    df: pd.DataFrame, cols: list[str],
    errors: list, warnings: list,
) -> tuple[str, pd.DataFrame | None]:
    """
    Attempt to parse TradingView export CSV.
    Columns: time, open, high, low, close, volume
    time may be ISO8601 string or Unix epoch (int/float seconds).
    """
    has_time  = "time" in cols
    has_ohlcv = all(c in cols for c in ("open", "high", "low", "close", "volume"))

    if not (has_time and has_ohlcv):
        return "unknown", None

    try:
        raw_time = df["time"]
        # Try numeric epoch first
        try:
            numeric = pd.to_numeric(raw_time, errors="raise")
            dt_series = pd.to_datetime(numeric, unit="s", errors="coerce")
        except (ValueError, TypeError):
            dt_series = pd.to_datetime(raw_time.astype(str).str.strip(),
                                       errors="coerce")

        null_count = dt_series.isna().sum()
        if null_count > len(dt_series) * 0.5:
            warnings.append(f"TradingView: {null_count}/{len(dt_series)} time parse failures")

        result_df = _make_output_df(dt_series, df, cols, time_col="time")
        if result_df is not None:
            return "tradingview", result_df
    except Exception as exc:
        warnings.append(f"TradingView parse error: {exc}")

    return "unknown", None


def _make_output_df(
    dt_series: "pd.Series",
    df: pd.DataFrame,
    cols: list[str],
    time_col: str | None = None,
) -> pd.DataFrame | None:
    """Build the normalised output DataFrame."""
    try:
        out = pd.DataFrame()
        # Strip timezone for uniformity
        if hasattr(dt_series, "dt") and hasattr(dt_series.dt, "tz") and dt_series.dt.tz is not None:
            dt_series = dt_series.dt.tz_localize(None)
        out["datetime"] = dt_series
        out["open"]     = pd.to_numeric(df["open"],   errors="coerce")
        out["high"]     = pd.to_numeric(df["high"],   errors="coerce")
        out["low"]      = pd.to_numeric(df["low"],    errors="coerce")
        out["close"]    = pd.to_numeric(df["close"],  errors="coerce")
        out["volume"]   = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        # Drop rows where OHLC is entirely null
        out = out.dropna(subset=["open", "high", "low", "close"])
        out = out.sort_values("datetime").reset_index(drop=True)
        return out
    except Exception:
        return None

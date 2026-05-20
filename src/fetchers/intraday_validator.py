"""
Schema validation for intraday candle DataFrames and CSV files.

Validates before any data is used in evaluation or saved to disk.
Does NOT modify the DataFrame — returns a validation report.

Required columns:
  symbol, datetime, open, high, low, close, volume, timeframe

Checks applied in order:
  1. Required columns present
  2. datetime column parseable
  3. OHLCV columns numeric
  4. No nulls in critical columns
  5. high >= low for every row (OHLC integrity)
  6. open and close within [low, high] range (OHLC integrity)
  7. volume >= 0
  8. timeframe is a recognised value
  9. Single symbol per file (required for correct file naming)
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "timeframe"]

ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "10m", "15m", "30m", "60m", "1h"}


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_intraday_df(df: pd.DataFrame) -> dict:
    """
    Validate an intraday candle DataFrame.

    Returns
    -------
    {
      "valid":    bool,
      "errors":   [str],    # blocking — data must not be used if any error
      "warnings": [str],    # non-blocking — data may be used but with caution
      "stats": {
        "rows": int,
        "symbols": [str],
        "timeframes": [str],
        "dates": [str],
        "date_range": str,
      }
    }
    """
    errors: list[str]   = []
    warnings: list[str] = []

    # --- 1. Required columns ---
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        return {
            "valid":    False,
            "errors":   [f"Missing required columns: {missing_cols}. "
                         f"Expected: {REQUIRED_COLUMNS}"],
            "warnings": [],
            "stats":    {},
        }

    # Work on a copy to avoid mutating the caller's DataFrame
    df = df.copy()

    # --- 2. Datetime parseable ---
    try:
        df["datetime"] = pd.to_datetime(df["datetime"])
    except Exception as exc:
        return {
            "valid":    False,
            "errors":   [f"Cannot parse 'datetime' column: {exc}. "
                         f"Expected format: YYYY-MM-DD HH:MM:SS"],
            "warnings": [],
            "stats":    {},
        }

    # --- 3. OHLCV numeric ---
    for col in ("open", "high", "low", "close", "volume"):
        coerced = pd.to_numeric(df[col], errors="coerce")
        bad_count = coerced.isna().sum() - df[col].isna().sum()  # newly-turned-NaN
        if bad_count > 0:
            errors.append(
                f"Column '{col}' has {bad_count} non-numeric value(s) — "
                f"all OHLCV columns must be numbers"
            )
        else:
            df[col] = coerced

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": {}}

    # Convert fully now that we know they're numeric
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- 4. No nulls in critical columns ---
    for col in ("symbol", "datetime", "open", "high", "low", "close"):
        null_count = df[col].isna().sum()
        if null_count > 0:
            errors.append(f"Column '{col}' has {null_count} null/NaN value(s)")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": {}}

    # --- 5. high >= low ---
    bad_hl_mask = df["high"] < df["low"]
    bad_hl_count = bad_hl_mask.sum()
    if bad_hl_count > 0:
        bad_rows = df.index[bad_hl_mask].tolist()[:5]
        errors.append(
            f"{bad_hl_count} row(s) where high < low (rows: {bad_rows}{'...' if bad_hl_count > 5 else ''}) "
            f"— this is an impossible OHLC state"
        )

    # --- 6. open and close within [low, high] ---
    bad_open  = ((df["open"]  > df["high"]) | (df["open"]  < df["low"])).sum()
    bad_close = ((df["close"] > df["high"]) | (df["close"] < df["low"])).sum()
    if bad_open > 0:
        warnings.append(
            f"{bad_open} row(s) where open is outside the high/low range — "
            f"data may be from mismatched timeframes"
        )
    if bad_close > 0:
        warnings.append(
            f"{bad_close} row(s) where close is outside the high/low range — "
            f"check data source"
        )

    # --- 7. volume >= 0 ---
    neg_vol = (df["volume"] < 0).sum()
    if neg_vol > 0:
        errors.append(f"{neg_vol} row(s) with negative volume")

    # --- 8. timeframe values ---
    bad_tf = df[~df["timeframe"].isin(ALLOWED_TIMEFRAMES)]["timeframe"].unique().tolist()
    if bad_tf:
        warnings.append(
            f"Unrecognised timeframe value(s): {bad_tf}. "
            f"Expected one of: {sorted(ALLOWED_TIMEFRAMES)}"
        )

    # --- 9. Single symbol per file ---
    symbols = df["symbol"].dropna().unique().tolist()
    if len(symbols) > 1:
        errors.append(
            f"File contains multiple symbols: {symbols}. "
            f"Each file must contain data for exactly one symbol "
            f"(e.g. NIFTY_20260520_5m.csv)."
        )

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": {}}

    # --- Build stats ---
    timeframes = df["timeframe"].unique().tolist()
    dates_raw  = df["datetime"].dt.date.unique()
    dates      = sorted(str(d) for d in dates_raw)

    stats = {
        "rows":       len(df),
        "symbols":    symbols,
        "timeframes": timeframes,
        "dates":      dates,
        "date_range": f"{dates[0]} to {dates[-1]}" if dates else "—",
    }

    return {
        "valid":    True,
        "errors":   errors,
        "warnings": warnings,
        "stats":    stats,
    }

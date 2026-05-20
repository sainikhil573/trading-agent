"""
Intraday CSV file loader — list, load, validate, and save intraday candle files.

File naming convention:
  data/intraday/{SYMBOL}_{YYYYMMDD}_{timeframe}.csv
  e.g.  NIFTY_20260520_5m.csv
        BANKNIFTY_20260521_15m.csv

All files are validated on load using intraday_validator.py.
Invalid files are never silently used — errors are always surfaced.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from src.fetchers.intraday_validator import validate_intraday_df

logger = logging.getLogger(__name__)

_INTRADAY_DIR = Path(__file__).parent.parent.parent / "data" / "intraday"


# ---------------------------------------------------------------------------
# Load a single file
# ---------------------------------------------------------------------------

def load_intraday_file(path: Path) -> dict:
    """
    Load and validate a single intraday CSV file.

    Returns
    -------
    {
      "path":     Path,
      "valid":    bool,
      "errors":   [str],
      "warnings": [str],
      "stats":    dict,   # rows, symbols, timeframes, dates, date_range
      "df":       pd.DataFrame | None,
    }
    """
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return {
            "path":     path,
            "valid":    False,
            "errors":   [f"Cannot read file: {exc}"],
            "warnings": [],
            "stats":    {},
            "df":       None,
        }

    result = validate_intraday_df(df)
    if result["valid"]:
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("datetime").reset_index(drop=True)
        return {**result, "path": path, "df": df}

    return {**result, "path": path, "df": None}


# ---------------------------------------------------------------------------
# List available files
# ---------------------------------------------------------------------------

def list_intraday_files(data_dir: Path | None = None) -> list[dict]:
    """
    Scan data_dir for intraday CSV files and return metadata for each.

    Each entry in the returned list:
    {
      "path":      Path,
      "filename":  str,
      "valid":     bool,
      "errors":    [str],
      "warnings":  [str],
      "stats":     dict,   # rows, symbols, timeframes, dates, date_range
    }
    Returns an empty list if the directory does not exist.
    """
    if data_dir is None:
        data_dir = _INTRADAY_DIR

    if not data_dir.exists():
        return []

    files = sorted(
        p for p in data_dir.glob("*.csv")
        if p.name != "schema_example.csv"   # skip the bundled example
    )

    results = []
    for p in files:
        result = load_intraday_file(p)
        results.append({
            "path":     p,
            "filename": p.name,
            "valid":    result["valid"],
            "errors":   result["errors"],
            "warnings": result["warnings"],
            "stats":    result["stats"],
        })
    return results


# ---------------------------------------------------------------------------
# Save an uploaded/provided CSV
# ---------------------------------------------------------------------------

def save_uploaded_csv(
    file_content: bytes | str,
    data_dir: Path | None = None,
    overwrite: bool = True,
) -> dict:
    """
    Parse, validate, and save intraday CSV content to data_dir.

    File is saved as:  {SYMBOL}_{YYYYMMDD}_{timeframe}.csv
    Symbol and timeframe are inferred from the CSV content.
    If the file contains multiple dates, the earliest date is used in the filename.

    Parameters
    ----------
    file_content : raw bytes or string content of the CSV
    data_dir     : target directory (defaults to data/intraday/)
    overwrite    : if True, replace existing file; if False, return error on conflict

    Returns
    -------
    {
      "saved":      bool,
      "path":       Path | None,
      "filename":   str | None,
      "errors":     [str],
      "warnings":   [str],
      "stats":      dict,
    }
    """
    if data_dir is None:
        data_dir = _INTRADAY_DIR

    # Parse CSV
    try:
        if isinstance(file_content, bytes):
            text = file_content.decode("utf-8", errors="replace")
        else:
            text = str(file_content)
        df = pd.read_csv(io.StringIO(text))
    except Exception as exc:
        return {
            "saved":    False,
            "path":     None,
            "filename": None,
            "errors":   [f"Cannot parse CSV content: {exc}"],
            "warnings": [],
            "stats":    {},
        }

    # Validate
    result = validate_intraday_df(df)
    if not result["valid"]:
        return {
            "saved":    False,
            "path":     None,
            "filename": None,
            "errors":   result["errors"],
            "warnings": result["warnings"],
            "stats":    result["stats"],
        }

    stats = result["stats"]

    # Infer filename components from content
    symbol    = stats["symbols"][0].upper().strip()
    timeframe = stats["timeframes"][0] if stats["timeframes"] else "5m"
    date_str  = stats["dates"][0].replace("-", "")  # earliest date, YYYYMMDD

    filename = f"{symbol}_{date_str}_{timeframe}.csv"
    out_path = data_dir / filename

    # Conflict check
    if out_path.exists() and not overwrite:
        return {
            "saved":    False,
            "path":     out_path,
            "filename": filename,
            "errors":   [f"File already exists: {filename}. Set overwrite=True to replace."],
            "warnings": result["warnings"],
            "stats":    stats,
        }

    # Create directory if needed
    data_dir.mkdir(parents=True, exist_ok=True)

    # Normalise and save
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("datetime").reset_index(drop=True)

    try:
        df.to_csv(out_path, index=False, date_format="%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        return {
            "saved":    False,
            "path":     out_path,
            "filename": filename,
            "errors":   [f"Failed to write file: {exc}"],
            "warnings": result["warnings"],
            "stats":    stats,
        }

    logger.info("Saved intraday CSV: %s (%d rows)", filename, len(df))
    return {
        "saved":    True,
        "path":     out_path,
        "filename": filename,
        "errors":   [],
        "warnings": result["warnings"],
        "stats":    stats,
    }

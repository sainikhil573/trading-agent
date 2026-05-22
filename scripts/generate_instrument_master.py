"""
Generate instrument_master.csv from NSE's public F&O lot-sizes CSV.

Downloads: https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv
Parses lot sizes, generates canonical instrument_master.csv with
placeholder tokens (0) that allow InstrumentMaster to load.

Usage:
    python scripts/generate_instrument_master.py
    python scripts/generate_instrument_master.py --provider kite
    python scripts/generate_instrument_master.py --out data/instruments/instrument_master.csv
"""

from __future__ import annotations
import argparse
import csv
import io
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT        = Path(__file__).parent.parent
OUT_DEFAULT = ROOT / "data" / "instruments" / "instrument_master.csv"

NSE_MKTLOTS_URL = (
    "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
    "Accept":  "text/csv,*/*",
}

# Canonical column order expected by InstrumentMaster
COLUMNS = [
    "provider", "exchange", "symbol", "trading_symbol",
    "instrument_token", "symbol_token", "instrument_type",
    "expiry", "strike", "option_type", "lot_size", "tick_size",
]

# Fixed lot sizes as fallback (from NSE circulars — updated May 2026)
FALLBACK_LOT_SIZES: dict[str, int] = {
    "NIFTY":     75,   "BANKNIFTY": 35,   "FINNIFTY":  40,
    "RELIANCE":  250,  "TCS":       150,  "INFY":      300,
    "HDFCBANK":  550,  "ICICIBANK": 700,  "AXISBANK":  625,
    "KOTAKBANK": 400,  "SBIN":      1500, "TATASTEEL": 5500,
    "HINDALCO":  2150, "ONGC":      3850, "BPCL":      4500,
    "MARUTI":    50,   "BAJFINANCE":125,  "TATAMOTORS":2850,
    "WIPRO":     1500, "SUNPHARMA": 700,  "DRREDDY":   125,
    "ADANIENT":  625,  "LT":        375,
}


def _next_last_thursday(from_date: date | None = None) -> date:
    """Return the last Thursday of the current month (nearest monthly expiry)."""
    d = from_date or date.today()
    # Find last day of month
    if d.month == 12:
        last = date(d.year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(d.year, d.month + 1, 1) - timedelta(days=1)
    # Walk back to Thursday (weekday 3)
    while last.weekday() != 3:
        last -= timedelta(days=1)
    return last


def fetch_lot_sizes() -> dict[str, int]:
    """Download and parse fo_mktlots.csv from NSE archives."""
    logger.info("Downloading %s ...", NSE_MKTLOTS_URL)
    try:
        resp = requests.get(NSE_MKTLOTS_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not download fo_mktlots.csv: %s", exc)
        logger.warning("Using built-in fallback lot sizes")
        return dict(FALLBACK_LOT_SIZES)

    # fo_mktlots.csv is semicolon-delimited, first few rows may be header junk
    text = resp.content.decode("utf-8", errors="replace")
    lot_map: dict[str, int] = {}

    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            # Common column names: 'Symbol' or 'SYMBOL', lot size column varies
            sym = (
                row.get("Symbol") or row.get("SYMBOL") or
                row.get("symbol") or ""
            ).strip().upper()
            if not sym:
                continue
            lot_col = next(
                (k for k in row if "lot" in k.lower() or "size" in k.lower()), None
            )
            if lot_col:
                try:
                    lot_map[sym] = int(str(row[lot_col]).replace(",", "").strip())
                except (ValueError, TypeError):
                    pass
    except Exception as exc:
        logger.warning("CSV parse error: %s — using fallback sizes", exc)

    if not lot_map:
        logger.warning("No data parsed from CSV — using fallback sizes")
        return dict(FALLBACK_LOT_SIZES)

    # Merge with fallback to fill any gaps
    merged = dict(FALLBACK_LOT_SIZES)
    merged.update(lot_map)
    logger.info("Parsed %d lot sizes from NSE CSV", len(lot_map))
    return merged


def generate_rows(lot_sizes: dict[str, int], provider: str, expiry: date) -> list[dict]:
    """Build canonical instrument_master rows for all F&O symbols."""
    expiry_str = expiry.strftime("%Y-%m-%d")
    rows: list[dict] = []

    def _row(exchange, symbol, trading_symbol, itype, expiry_val="",
             strike="", option_type="", lot=1, tick=0.05) -> dict:
        return {
            "provider":         provider,
            "exchange":         exchange,
            "symbol":           symbol,
            "trading_symbol":   trading_symbol,
            "instrument_token": 0,
            "symbol_token":     "",
            "instrument_type":  itype,
            "expiry":           expiry_val,
            "strike":           strike,
            "option_type":      option_type,
            "lot_size":         lot,
            "tick_size":        tick,
        }

    # --- NIFTY index ---
    nifty_lot = lot_sizes.get("NIFTY", 75)
    rows.append(_row("NSE", "NIFTY",     "Nifty 50",     "INDEX", lot=nifty_lot))
    rows.append(_row("NFO", "NIFTY",     f"NIFTY-I",     "FUTIDX", expiry_str, lot=nifty_lot))

    # --- BANKNIFTY index ---
    bn_lot = lot_sizes.get("BANKNIFTY", 35)
    rows.append(_row("NSE", "BANKNIFTY", "Nifty Bank",   "INDEX", lot=bn_lot))
    rows.append(_row("NFO", "BANKNIFTY", f"BANKNIFTY-I", "FUTIDX", expiry_str, lot=bn_lot))

    # --- F&O equity stocks ---
    fno_stocks = [
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
        "AXISBANK", "KOTAKBANK", "SBIN", "TATASTEEL", "HINDALCO",
        "ONGC", "BPCL", "MARUTI", "BAJFINANCE", "TATAMOTORS",
        "WIPRO", "SUNPHARMA", "DRREDDY", "ADANIENT", "LT",
    ]
    for sym in fno_stocks:
        lot = lot_sizes.get(sym, 500)
        rows.append(_row("NSE", sym, sym,          "EQ",     lot=lot))
        rows.append(_row("NFO", sym, f"{sym}-I",   "FUTSTK", expiry_str, lot=lot))

    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows -> %s", len(rows), out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NSE F&O instrument master CSV")
    parser.add_argument("--provider", default="kite",
                        choices=["kite", "angelone"],
                        help="Broker provider label (default: kite)")
    parser.add_argument("--out", default=str(OUT_DEFAULT),
                        help=f"Output path (default: {OUT_DEFAULT})")
    args = parser.parse_args()

    expiry     = _next_last_thursday()
    lot_sizes  = fetch_lot_sizes()
    provider   = args.provider.upper()
    if provider == "ANGELONE":
        provider = "ANGELONE"
    else:
        provider = "KITE"

    rows = generate_rows(lot_sizes, provider, expiry)

    # Generate rows for both providers so the master covers either
    if provider == "KITE":
        rows_both = rows + generate_rows(lot_sizes, "ANGELONE", expiry)
    else:
        rows_both = rows + generate_rows(lot_sizes, "KITE", expiry)

    out_path = Path(args.out)
    write_csv(rows_both, out_path)

    # Verify it loads
    sys.path.insert(0, str(ROOT))
    from src.fetchers.instrument_master import InstrumentMaster
    master = InstrumentMaster(path=out_path)
    if master.is_loaded:
        logger.info(
            "InstrumentMaster verified: %d records loaded from %s",
            master.record_count, out_path.name,
        )
        val = master.validate()
        if val.errors:
            logger.warning("Validation errors: %s", val.errors[:3])
        logger.info(
            "Validation: %d errors, %d warnings",
            len(val.errors), len(val.warnings),
        )
    else:
        logger.error("InstrumentMaster failed to load: %s", master.error)
        sys.exit(1)


if __name__ == "__main__":
    main()

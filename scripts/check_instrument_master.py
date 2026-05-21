"""
Instrument master validation and coverage checker.

Usage
-----
    # Validate the default instrument_master.csv
    python scripts/check_instrument_master.py

    # Validate a specific file
    python scripts/check_instrument_master.py --file data/instruments/instrument_master.csv

    # Convert Kite native instruments CSV to canonical format
    python scripts/check_instrument_master.py --kite-file kite_instruments.csv --output data/instruments/instrument_master.csv

    # Convert Angel One scrip master CSV to canonical format
    python scripts/check_instrument_master.py --angelone-file angelone_instruments.csv --output data/instruments/instrument_master.csv

    # Convert and merge both providers
    python scripts/check_instrument_master.py \
        --kite-file kite_instruments.csv \
        --angelone-file angelone_instruments.csv \
        --output data/instruments/instrument_master.csv

Options
-------
    --file FILE           Path to canonical instrument master CSV to validate (default: auto-detect)
    --kite-file FILE      Kite native instruments CSV to normalise
    --angelone-file FILE  Angel One scrip master CSV to normalise
    --output FILE         Write normalised output to this path (default: dry run, no write)
    --provider PROVIDER   Restrict coverage report to kite or angelone
    --verbose             Show all warnings (not just summary)

No live API calls are made. No Streamlit required.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Add project root to path so imports work from any working directory
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd

from src.fetchers.instrument_master import (
    InstrumentMaster,
    ValidationReport,
    CoverageReport,
    load_best_available_master,
    normalize_kite_native_df,
    normalize_angelone_native_df,
    REQUIRED_COLUMNS,
    _DEFAULT_FO_SYMBOLS,
)

# ANSI colour codes (disabled on Windows by default; use --no-color to suppress)
_RESET  = "\033[0m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"


def _col(text: str, code: str, use_color: bool) -> str:
    return f"{code}{text}{_RESET}" if use_color else text


def print_validation(report: ValidationReport, verbose: bool, use_color: bool) -> None:
    ok   = _col("OK",   _GREEN,  use_color)
    warn = _col("WARN", _YELLOW, use_color)
    err  = _col("ERR",  _RED,    use_color)

    print(f"\n{_col('=== VALIDATION ===', _BOLD, use_color)}")
    print(f"  Total rows      : {report.total_rows}")
    print(f"  Valid rows      : {report.valid_rows}")
    if report.unsupported_provider_count:
        print(f"  [{err}] Unsupported provider rows : {report.unsupported_provider_count}")
    if report.missing_token_rows:
        print(f"  [{warn}] Missing token rows        : {report.missing_token_rows}")
        if report.missing_token_symbols:
            print(f"         Affected symbols       : {', '.join(report.missing_token_symbols)}")
    if report.expired_option_count:
        print(f"  [{warn}] Expired option contracts  : {report.expired_option_count}")
        if report.expired_option_symbols:
            print(f"         Examples               : {', '.join(report.expired_option_symbols[:5])}")
    if report.errors:
        print(f"\n  {_col('Errors:', _RED, use_color)}")
        for e in report.errors:
            print(f"    [{err}] {e}")
    if report.warnings and verbose:
        print(f"\n  {_col('Warnings:', _YELLOW, use_color)}")
        for w in report.warnings:
            print(f"    [{warn}] {w}")
    elif report.warnings and not verbose:
        print(f"\n  [{warn}] {len(report.warnings)} warning(s). Use --verbose to see all.")

    if report.is_clean:
        print(f"\n  [{ok}] No errors or warnings.")
    print()


def print_coverage(report: CoverageReport, use_color: bool) -> None:
    ok   = _col("OK",   _GREEN,  use_color)
    warn = _col("WARN", _YELLOW, use_color)
    miss = _col("MISS", _RED,    use_color)

    print(f"{_col('=== COVERAGE: ' + report.provider.upper() + ' ===', _BOLD, use_color)}")
    print(f"  Total rows loaded : {report.total_count}")

    if report.by_type:
        print("  By instrument type:")
        for itype, cnt in sorted(report.by_type.items()):
            print(f"    {itype:<12} : {cnt}")

    if report.by_exchange:
        print("  By exchange:")
        for exch, cnt in sorted(report.by_exchange.items()):
            print(f"    {exch:<8} : {cnt}")

    print("\n  Index token readiness:")
    for sym, ready in report.index_ready.items():
        status = f"[{ok}] ready" if ready else f"[{miss}] MISSING"
        print(f"    {sym:<14} : {status}")

    if any(not v for v in report.index_ready.values()):
        print(f"  [{warn}] Some index tokens are missing — broker candle fetch will return empty.")

    if report.equity_ready:
        print("\n  F&O stock token readiness:")
        for sym, ready in report.equity_ready.items():
            status = f"[{ok}]" if ready else f"[{miss}] missing"
            print(f"    {sym:<14} : {status}")
        n_missing = sum(1 for v in report.equity_ready.values() if not v)
        if n_missing:
            print(f"\n  [{warn}] {n_missing} F&O stock(s) have no usable token.")

    if report.expired_option_symbols:
        print(f"\n  [{warn}] Expired option contracts ({len(report.expired_option_symbols)}):")
        for sym in report.expired_option_symbols[:5]:
            print(f"    {sym}")
        if len(report.expired_option_symbols) > 5:
            print(f"    ... and {len(report.expired_option_symbols) - 5} more.")
    print()


def cmd_validate(args: argparse.Namespace, use_color: bool) -> int:
    """Load and validate the instrument master. Returns exit code."""
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(_col(f"Error: file not found: {p}", _RED, use_color), file=sys.stderr)
            return 2
        master = InstrumentMaster(path=p)
    else:
        master = load_best_available_master()

    print(f"\n{_col('Instrument Master', _BOLD, use_color)}")
    print(f"  Source  : {master.source_name}")
    print(f"  Loaded  : {master.is_loaded}")
    print(f"  Records : {master.record_count}")

    if not master.is_loaded:
        print(_col(f"\nError: {master.error}", _RED, use_color))
        print("\nTo create the instrument master:")
        print("  1. Download Kite instruments CSV from https://api.kite.trade/instruments")
        print("     Filter to NSE/NFO rows for your symbols, then run:")
        print("     python scripts/check_instrument_master.py --kite-file <file> --output data/instruments/instrument_master.csv")
        print("  2. Or build manually using data/instruments/schema_example.csv as a template.")
        return 1

    report = master.validate(today=date.today())
    print_validation(report, verbose=getattr(args, "verbose", False), use_color=use_color)

    providers = getattr(args, "provider", None)
    if providers:
        provs = [providers]
    else:
        # Auto-detect providers present in the file
        pnames = (
            master._df["provider"].str.lower().unique().tolist()
            if master.is_loaded else []
        )
        provs = list({p for p in pnames if p in ("kite", "angelone")})
        if not provs:
            provs = ["kite"]

    for prov in provs:
        cov = master.coverage_report(prov, fo_symbols=_DEFAULT_FO_SYMBOLS)
        print_coverage(cov, use_color=use_color)

    return 1 if report.has_errors else 0


def cmd_convert(args: argparse.Namespace, use_color: bool) -> int:
    """Normalise native broker file(s) and optionally write canonical output."""
    frames: list[pd.DataFrame] = []
    sources: list[str] = []

    if getattr(args, "kite_file", None):
        kf = Path(args.kite_file)
        if not kf.exists():
            print(_col(f"Error: Kite file not found: {kf}", _RED, use_color), file=sys.stderr)
            return 2
        print(f"Loading Kite instruments from {kf.name}…")
        raw = pd.read_csv(kf, dtype=str)
        print(f"  Raw rows: {len(raw)}")
        norm = normalize_kite_native_df(raw)
        print(f"  Normalised rows: {len(norm)}")
        if not norm.empty:
            frames.append(norm)
            sources.append(kf.name)

    if getattr(args, "angelone_file", None):
        af = Path(args.angelone_file)
        if not af.exists():
            print(_col(f"Error: Angel One file not found: {af}", _RED, use_color), file=sys.stderr)
            return 2
        print(f"Loading Angel One instruments from {af.name}…")
        raw = pd.read_csv(af, dtype=str)
        print(f"  Raw rows: {len(raw)}")
        norm = normalize_angelone_native_df(raw)
        print(f"  Normalised rows: {len(norm)}")
        if not norm.empty:
            frames.append(norm)
            sources.append(af.name)

    if not frames:
        print(_col("No usable data found after normalisation.", _RED, use_color))
        return 1

    merged = pd.concat(frames, ignore_index=True)
    print(f"\nMerged result: {len(merged)} rows from [{', '.join(sources)}]")

    master = InstrumentMaster.from_dataframe(merged, source_name=" + ".join(sources))
    report = master.validate(today=date.today())
    print_validation(report, verbose=getattr(args, "verbose", False), use_color=use_color)

    for prov in ("kite", "angelone"):
        if master._df["provider"].str.lower().isin([prov]).any():
            cov = master.coverage_report(prov, fo_symbols=_DEFAULT_FO_SYMBOLS)
            print_coverage(cov, use_color=use_color)

    if getattr(args, "output", None):
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(out, index=False)
        print(_col(f"\nWrote {len(merged)} rows to {out}", _GREEN, use_color))
    else:
        print("\n(Dry run — no output written. Use --output FILE to save.)")

    return 1 if report.has_errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and convert instrument master files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--file",          help="Canonical instrument master CSV to validate")
    parser.add_argument("--kite-file",     help="Kite native instruments CSV to normalise")
    parser.add_argument("--angelone-file", help="Angel One scrip master CSV to normalise")
    parser.add_argument("--output",        help="Write normalised canonical CSV to this path")
    parser.add_argument("--provider",      help="Restrict coverage report (kite|angelone)")
    parser.add_argument("--verbose",       action="store_true", help="Show all warnings")
    parser.add_argument("--no-color",      action="store_true", help="Disable ANSI colours")

    args = parser.parse_args()
    use_color = not args.no_color and sys.stdout.isatty()

    # Decide mode: convert if native files given, else validate
    if args.kite_file or args.angelone_file:
        return cmd_convert(args, use_color)
    else:
        return cmd_validate(args, use_color)


if __name__ == "__main__":
    sys.exit(main())

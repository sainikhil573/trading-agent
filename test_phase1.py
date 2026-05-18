"""
Phase 1 smoke test -- run manually to verify NSE connectivity.
Usage:  python test_phase1.py
"""

import sys
import json
import io
from pathlib import Path

# Force UTF-8 stdout on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import setup_logger
from src.fetchers.nse_fetcher import NSEFetcher

logger = setup_logger()


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run():
    fetcher = NSEFetcher()

    # --- India VIX ---------------------------------------------------
    section("India VIX")
    vix = fetcher.fetch_india_vix()
    print(f"  India VIX : {vix}")

    # --- FII / DII ---------------------------------------------------
    section("FII / DII")
    fii_dii = fetcher.fetch_fii_dii()
    for k, v in fii_dii.items():
        print(f"  {k:20s}: {v}")

    # --- NIFTY Option Chain ------------------------------------------
    section("NIFTY Option Chain")
    raw = fetcher.fetch_option_chain("NIFTY")

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    with open("data/raw/nifty_oc.json", "w") as f:
        json.dump(raw, f, indent=2)
    logger.info("Raw NIFTY option chain saved -> data/raw/nifty_oc.json")

    df, meta = fetcher.parse_option_chain(raw, "NIFTY")
    max_pain = fetcher.calculate_max_pain(df)

    print("\n  Metadata:")
    for k, v in meta.items():
        print(f"    {k:20s}: {v}")
    print(f"    {'max_pain':20s}: {max_pain}")

    if not df.empty and meta["atm_strike"]:
        atm = meta["atm_strike"]
        nearby = df[abs(df["strike"] - atm) <= 300].sort_values(["strike", "type"])
        print(f"\n  Strikes near ATM ({atm}):")
        print(nearby.to_string(index=False))

    # --- BANKNIFTY Option Chain --------------------------------------
    section("BANKNIFTY Option Chain")
    raw_bn = fetcher.fetch_option_chain("BANKNIFTY")
    df_bn, meta_bn = fetcher.parse_option_chain(raw_bn, "BANKNIFTY")
    max_pain_bn = fetcher.calculate_max_pain(df_bn)

    print("\n  Metadata:")
    for k, v in meta_bn.items():
        print(f"    {k:20s}: {v}")
    print(f"    {'max_pain':20s}: {max_pain_bn}")

    # --- Top F&O Stocks ----------------------------------------------
    section("Top 10 F&O Stocks (by OI change)")
    df_fno = fetcher.fetch_top_fno_stocks(top_n=10)
    if not df_fno.empty:
        print(df_fno[["symbol", "LTP", "pct_change", "volume"]].to_string(index=False))
    else:
        print("  No data returned.")

    section("Phase 1 Complete")
    logger.info("Phase 1 smoke test finished successfully.")


if __name__ == "__main__":
    run()

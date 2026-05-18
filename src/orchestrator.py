"""
Orchestrator — runs one full analysis cycle and returns the trade brief.
Called by the scheduler (main.py) every morning at 8 AM IST.
"""

from __future__ import annotations
import json
import logging
from datetime import date
from pathlib import Path

from src.fetchers.nse_fetcher import NSEFetcher
from src.fetchers.global_fetcher import fetch_global_cues
from src.analyzers.signal_analyzer import build_market_signal, build_stock_signals
from src.analyzers.claude_analyzer import ClaudeAnalyzer

logger = logging.getLogger(__name__)


def run_analysis(api_key: str) -> list[dict]:
    """
    Full pipeline:
      1. Fetch NSE data (option chains, VIX, FII/DII, top F&O stocks)
      2. Fetch global cues
      3. Build structured signals
      4. Call Claude API for trade brief
      5. Save output to data/processed/
    Returns the list of trade recommendation dicts.
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("ANALYSIS STARTED  —  %s", today)
    logger.info("=" * 70)

    nse = NSEFetcher()

    # -- Step 1: NSE data ------------------------------------------------
    logger.info("[1/4] Fetching NSE data...")

    vix = nse.fetch_india_vix()
    logger.info("  India VIX        : %s", vix)

    fii_dii = nse.fetch_fii_dii()
    logger.info("  FII net (cr)     : %s | DII net (cr): %s",
                fii_dii["fii_net_buy"], fii_dii["dii_net_buy"])

    raw_nifty = nse.fetch_option_chain("NIFTY")
    df_nifty, meta_nifty = nse.parse_option_chain(raw_nifty, "NIFTY")
    max_pain_nifty = nse.calculate_max_pain(df_nifty)

    raw_bn = nse.fetch_option_chain("BANKNIFTY")
    df_bn, meta_bn = nse.parse_option_chain(raw_bn, "BANKNIFTY")
    max_pain_bn = nse.calculate_max_pain(df_bn)

    df_fno = nse.fetch_top_fno_stocks(top_n=10)

    # -- Step 2: Global cues ---------------------------------------------
    logger.info("[2/4] Fetching global cues...")
    global_cues = fetch_global_cues()
    logger.info("  Global bias: %s  (+%d / -%d)",
                global_cues["overall_bias"],
                global_cues["positive_count"],
                global_cues["negative_count"])

    # -- Step 3: Build signals -------------------------------------------
    logger.info("[3/4] Building market signals...")

    nifty_signal = build_market_signal(
        "NIFTY", meta_nifty, df_nifty, max_pain_nifty, vix,
        global_cues["overall_bias"]
    )
    banknifty_signal = build_market_signal(
        "BANKNIFTY", meta_bn, df_bn, max_pain_bn, vix,
        global_cues["overall_bias"]
    )
    stock_signals = build_stock_signals(df_fno)

    logger.info("  NIFTY pre-bias   : %s", nifty_signal["pre_bias"])
    logger.info("  BANKNIFTY pre-bias: %s", banknifty_signal["pre_bias"])

    # -- Step 4: Claude API ----------------------------------------------
    logger.info("[4/4] Calling Claude API...")

    analyzer = ClaudeAnalyzer(api_key=api_key)
    trades = analyzer.generate_trade_brief(
        nifty_signal=nifty_signal,
        banknifty_signal=banknifty_signal,
        global_cues=global_cues,
        top_stocks=stock_signals,
        analysis_date=date.today().strftime("%d-%b-%Y (%A)"),
    )

    # -- Save outputs ----------------------------------------------------
    out_dir = Path("data/processed")
    out_dir.mkdir(parents=True, exist_ok=True)

    brief_path = out_dir / f"trade_brief_{today}.json"
    with open(brief_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date":            today,
                "vix":             vix,
                "fii_dii":         fii_dii,
                "nifty_meta":      meta_nifty,
                "banknifty_meta":  meta_bn,
                "global_cues":     {k: v for k, v in global_cues.items()
                                    if isinstance(v, dict)},
                "global_bias":     global_cues["overall_bias"],
                "trades":          trades,
            },
            f, indent=2,
        )
    logger.info("Trade brief saved -> %s", brief_path)

    # -- Print summary to stdout -----------------------------------------
    _print_brief(trades, today, vix, global_cues["overall_bias"])

    logger.info("ANALYSIS COMPLETE")
    return trades


def _print_brief(trades: list[dict], today: str, vix: float | None, global_bias: str):
    """Pretty-print the trade brief to console."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  TRADE BRIEF  —  {today}   |  VIX: {vix}  |  Global: {global_bias}")
    print(sep)

    if not trades:
        print("  No trades recommended today.")
        print(sep)
        return

    for t in trades:
        sig = t.get("signal", "?")
        conf = t.get("confidence", "?")
        sym  = t.get("symbol", "?")
        strike = t.get("strike", "?")
        expiry = t.get("expiry", "?")

        print(f"\n  {sym} — {sig}  (confidence: {conf}/10)")
        print(f"  Strike/Expiry : {strike}  |  {expiry}")
        print(f"  Entry         : {t.get('entry_price', '?')}")
        print(f"  Stop-Loss     : {t.get('stop_loss', '?')}")
        print(f"  Target        : {t.get('target', '?')}")
        print(f"  Risk:Reward   : 1:{t.get('risk_reward', '?')}")
        print(f"  Reasoning     : {t.get('reasoning', '')}")

    print(f"\n{sep}\n")

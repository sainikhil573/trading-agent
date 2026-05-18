"""
Global market cues fetcher via yfinance.
GIFT Nifty (formerly SGX Nifty) is not on Yahoo Finance; we use S&P 500 futures
as the primary US overnight proxy and note the gap separately.
"""

import logging
import yfinance as yf

logger = logging.getLogger(__name__)

# symbol -> human label
TICKERS = {
    "ES=F":      "S&P 500 Futures",
    "YM=F":      "Dow Jones Futures",
    "NQ=F":      "Nasdaq Futures",
    "CL=F":      "Crude Oil WTI",
    "USDINR=X":  "USD/INR",
    "GC=F":      "Gold Futures",
}


def _fetch_ticker(symbol: str) -> dict:
    """Return last price + day change % for a single ticker."""
    try:
        info = yf.Ticker(symbol).fast_info
        last  = float(info.last_price or 0)
        prev  = float(info.previous_close or last)
        pct   = round((last - prev) / prev * 100, 2) if prev else 0.0
        return {"last": last, "prev_close": prev, "pct_change": pct, "error": None}
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", symbol, exc)
        return {"last": None, "prev_close": None, "pct_change": None, "error": str(exc)}


def fetch_global_cues() -> dict:
    """
    Fetch all global cue tickers and return a structured dict.

    Returns
    -------
    {
      "S&P 500 Futures":  {"symbol": "ES=F", "last": ..., "pct_change": ...},
      ...
      "overall_bias":  "BULLISH" | "BEARISH" | "MIXED",
      "positive_count": int,
      "negative_count": int,
    }
    """
    results = {}
    positive, negative = 0, 0

    for symbol, label in TICKERS.items():
        data = _fetch_ticker(symbol)
        results[label] = {"symbol": symbol, **data}
        logger.info("  %-22s %s  %+.2f%%",
                    label,
                    f"{data['last']:.2f}" if data["last"] else "N/A",
                    data["pct_change"] or 0)

        if data["pct_change"] is not None:
            # Invert crude-oil and USD/INR: rising crude/rupee weakness = bearish for India
            if label in ("Crude Oil WTI", "USD/INR"):
                if data["pct_change"] > 0.3:
                    negative += 1
                elif data["pct_change"] < -0.3:
                    positive += 1
            else:
                if data["pct_change"] > 0.3:
                    positive += 1
                elif data["pct_change"] < -0.3:
                    negative += 1

    if positive > negative + 1:
        bias = "BULLISH"
    elif negative > positive + 1:
        bias = "BEARISH"
    else:
        bias = "MIXED"

    results["overall_bias"]    = bias
    results["positive_count"]  = positive
    results["negative_count"]  = negative
    return results

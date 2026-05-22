"""
Global market cues fetcher via yfinance.
GIFT Nifty (formerly SGX Nifty) is not on Yahoo Finance; we use a priority
fallback chain for the opening gap estimate (see fetch_opening_gap()).
"""

import logging
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

_NSE_GIFT_URL = "https://www.nseindia.com/api/giftNifty"
_NSE_HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# symbol -> human label
TICKERS = {
    "ES=F":      "S&P 500 Futures",
    "YM=F":      "Dow Jones Futures",
    "NQ=F":      "Nasdaq Futures",
    "^N225":     "Nikkei 225",
    "^HSI":      "Hang Seng",
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


def fetch_opening_gap() -> dict:
    """
    Opening gap estimate with priority chain:
      1. GIFT Nifty via NSE API (https://www.nseindia.com/api/giftNifty)
      2. ^NSEI from yfinance — most recent bar as proxy
      3. S&P 500 futures ES=F from yfinance — current fallback

    Returns
    -------
    {
      "source":     "gift_nifty" | "nsei_proxy" | "sp500_proxy",
      "value":      float | None,
      "pct_change": float | None,
      "error":      str | None,
    }
    Never raises — always returns dict.
    """
    # --- 1. GIFT Nifty via NSE API ---
    try:
        resp = requests.get(_NSE_GIFT_URL, headers=_NSE_HEADERS, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # NSE API returns different shapes; try common key paths
            last  = None
            change_pct = None
            for key in ("last", "lastPrice", "lastTradedPrice", "ltp"):
                if key in data:
                    last = float(data[key])
                    break
            for key in ("pChange", "percentChange", "change_pct"):
                if key in data:
                    change_pct = float(data[key])
                    break
            if last is not None:
                logger.info("Opening gap: GIFT Nifty = %.2f (%.2f%%)",
                            last, change_pct or 0)
                return {
                    "source": "gift_nifty",
                    "value": last,
                    "pct_change": change_pct,
                    "error": None,
                }
    except Exception as exc:
        logger.info("GIFT Nifty fetch failed (expected — no free source): %s", exc)

    # --- 2. ^NSEI proxy via yfinance ---
    try:
        data = _fetch_ticker("^NSEI")
        if data["last"]:
            pct = data["pct_change"]
            logger.info("Opening gap proxy: ^NSEI = %.2f (%.2f%%)",
                        data["last"], pct or 0)
            return {
                "source": "nsei_proxy",
                "value": data["last"],
                "pct_change": pct,
                "error": None,
            }
    except Exception as exc:
        logger.info("^NSEI proxy fetch failed: %s", exc)

    # --- 3. S&P 500 futures ES=F fallback ---
    try:
        data = _fetch_ticker("ES=F")
        if data["last"]:
            pct = data["pct_change"]
            logger.info("Opening gap proxy: ES=F = %.2f (%.2f%%)",
                        data["last"], pct or 0)
            return {
                "source": "sp500_proxy",
                "value": data["last"],
                "pct_change": pct,
                "error": None,
            }
    except Exception as exc:
        logger.warning("ES=F proxy fetch failed: %s", exc)
        return {
            "source": "sp500_proxy",
            "value": None,
            "pct_change": None,
            "error": str(exc),
        }

    return {
        "source": "sp500_proxy",
        "value": None,
        "pct_change": None,
        "error": "All opening gap sources failed",
    }


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
            # Invert indicators that are bearish for India when rising
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

    # Opening gap estimate (GIFT Nifty → ^NSEI → ES=F fallback)
    results["opening_gap"] = fetch_opening_gap()

    return results

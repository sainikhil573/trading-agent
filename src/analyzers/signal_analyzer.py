"""
Trend signal analyzer.
Interprets NSE option-chain metrics (PCR, VIX, OI, max pain) and global cues
to produce a structured signal summary for each instrument.
"""

from __future__ import annotations
import logging
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VIX interpretation
# ---------------------------------------------------------------------------

def interpret_vix(vix: float | None) -> dict:
    if vix is None:
        return {"value": None, "zone": "UNKNOWN", "implication": "Data unavailable"}
    if vix >= 25:
        zone, impl = "EXTREME_FEAR", "High premium; options are expensive — prefer selling or tight spreads"
    elif vix >= 18:
        zone, impl = "ELEVATED",     "Moderate fear; directional trades viable with wider SL"
    elif vix >= 12:
        zone, impl = "NORMAL",       "Calm market; standard ATM options reasonable"
    else:
        zone, impl = "COMPLACENCY",  "Very low VIX; watch for sudden reversal — markets tend to mean-revert"
    return {"value": vix, "zone": zone, "implication": impl}


# ---------------------------------------------------------------------------
# PCR interpretation (both sentiment AND contrarian lens)
# ---------------------------------------------------------------------------

def interpret_pcr(pcr: float) -> dict:
    """
    PCR > 1 → more puts than calls (fear/bearish sentiment).
    Contrarian: extreme PCR often marks reversals.
    """
    if pcr >= 1.3:
        sentiment  = "EXTREME_BEARISH"
        contrarian = "CONTRARIAN_BULLISH"
        note = "Heavy put buying; market may be oversold — watch for short-covering bounce"
    elif pcr >= 1.0:
        sentiment  = "BEARISH"
        contrarian = "SLIGHTLY_BULLISH"
        note = "More puts than calls; mild bearish bias"
    elif pcr >= 0.8:
        sentiment  = "NEUTRAL"
        contrarian = "NEUTRAL"
        note = "Balanced put-call activity; no clear directional bias"
    elif pcr >= 0.6:
        sentiment  = "BULLISH"
        contrarian = "SLIGHTLY_BEARISH"
        note = "More calls than puts; mild bullish bias"
    else:
        sentiment  = "EXTREME_BULLISH"
        contrarian = "CONTRARIAN_BEARISH"
        note = "Heavy call buying; market may be overbought — watch for profit-booking"
    return {"pcr": pcr, "sentiment": sentiment, "contrarian": contrarian, "note": note}


# ---------------------------------------------------------------------------
# OI analysis — support / resistance levels
# ---------------------------------------------------------------------------

def find_oi_levels(df: pd.DataFrame, top_n: int = 3) -> dict:
    """
    Identify key OI-based support and resistance strikes.

    CE OI concentration  → resistance (call writers defending these strikes)
    PE OI concentration  → support    (put writers defending these strikes)
    """
    if df.empty:
        return {"resistance": [], "support": []}

    ce = df[df["type"] == "CE"].groupby("strike")["OI"].sum().nlargest(top_n)
    pe = df[df["type"] == "PE"].groupby("strike")["OI"].sum().nlargest(top_n)

    return {
        "resistance": [
            {"strike": int(s), "ce_oi": int(oi)} for s, oi in ce.items()
        ],
        "support": [
            {"strike": int(s), "pe_oi": int(oi)} for s, oi in pe.items()
        ],
    }


def find_oi_buildup(df: pd.DataFrame, top_n: int = 3) -> dict:
    """
    Fresh OI buildup = strikes with largest positive chng_OI.
    Signals where smart money is adding new positions.
    """
    if df.empty:
        return {"fresh_ce_buildup": [], "fresh_pe_buildup": []}

    ce_build = (df[df["type"] == "CE"]
                .groupby("strike")["chng_OI"].sum()
                .nlargest(top_n))
    pe_build = (df[df["type"] == "PE"]
                .groupby("strike")["chng_OI"].sum()
                .nlargest(top_n))

    return {
        "fresh_ce_buildup": [
            {"strike": int(s), "chng_oi": int(oi)} for s, oi in ce_build.items()
        ],
        "fresh_pe_buildup": [
            {"strike": int(s), "chng_oi": int(oi)} for s, oi in pe_build.items()
        ],
    }


# ---------------------------------------------------------------------------
# Main signal builder
# ---------------------------------------------------------------------------

def build_market_signal(
    symbol: str,
    meta: dict,
    df: pd.DataFrame,
    max_pain: float | None,
    vix: float | None,
    global_bias: str,
) -> dict:
    """
    Combine all indicators into a structured signal dict for one instrument.

    Returns a dict Claude will receive as part of its context.
    """
    pcr_info    = interpret_pcr(meta["pcr"])
    vix_info    = interpret_vix(vix)
    oi_levels   = find_oi_levels(df)
    oi_buildup  = find_oi_buildup(df)

    spot        = meta["spot"]
    atm         = meta["atm_strike"]

    # Spot vs Max Pain gap
    mp_gap = None
    mp_direction = None
    if max_pain and spot:
        mp_gap = round(max_pain - spot, 2)
        mp_direction = "ABOVE_SPOT" if mp_gap > 0 else "BELOW_SPOT"

    # Simple rule-based pre-signal (Claude will refine this with full context)
    votes = []
    if pcr_info["sentiment"] in ("BEARISH", "EXTREME_BEARISH"):
        votes.append("BEARISH")
    elif pcr_info["sentiment"] in ("BULLISH", "EXTREME_BULLISH"):
        votes.append("BULLISH")

    if global_bias == "BULLISH":
        votes.append("BULLISH")
    elif global_bias == "BEARISH":
        votes.append("BEARISH")

    if mp_direction == "ABOVE_SPOT" and abs(mp_gap) < spot * 0.01:
        votes.append("BULLISH")   # max pain pull slightly above
    elif mp_direction == "BELOW_SPOT" and abs(mp_gap) < spot * 0.01:
        votes.append("BEARISH")

    bull_votes = votes.count("BULLISH")
    bear_votes = votes.count("BEARISH")
    pre_bias = "BULLISH" if bull_votes > bear_votes else ("BEARISH" if bear_votes > bull_votes else "NEUTRAL")

    return {
        "symbol":       symbol,
        "spot":         spot,
        "atm_strike":   atm,
        "max_pain":     max_pain,
        "mp_gap":       mp_gap,
        "mp_direction": mp_direction,
        "pcr":          pcr_info,
        "vix":          vix_info,
        "oi_levels":    oi_levels,
        "oi_buildup":   oi_buildup,
        "global_bias":  global_bias,
        "pre_bias":     pre_bias,
        "nearest_expiry": meta.get("nearest_expiry"),
    }


def build_stock_signals(df_fno: pd.DataFrame) -> list[dict]:
    """
    Generate simple directional signals for top F&O stocks based on price change.
    """
    signals = []
    for _, row in df_fno.iterrows():
        pct = row.get("pct_change", 0) or 0
        if pct > 1.5:
            bias = "BULLISH"
        elif pct < -1.5:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"
        signals.append({
            "symbol":     row["symbol"],
            "LTP":        row["LTP"],
            "pct_change": pct,
            "bias":       bias,
            "volume":     row.get("volume", 0),
        })
    return signals

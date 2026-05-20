"""
Evaluation helpers for auditing prediction accuracy and entry trigger feasibility.
Reads from prediction_accuracy.json; does NOT call external APIs or Claude.
"""

from __future__ import annotations
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

_ATM_DELTA = 0.5  # crude ATM delta approximation for premium estimation


def evaluate_accuracy_log(log: list[dict]) -> dict:
    """
    Summarize prediction_accuracy.json into audit stats.
    Uses outcome strings (not raw booleans) for T1/T2/SL/UNKNOWN counts,
    so OUTCOME_UNKNOWN entries are not inflating win rates.
    """
    valid = [r for r in log if not r.get("error")]
    if not valid:
        return {"total": 0, "error": "No valid entries"}

    total   = len(valid)
    correct = sum(1 for r in valid if r.get("was_correct"))

    # Outcome-string based counts (honest — no ambiguous inflation)
    t1_hits = sum(1 for r in valid if r.get("outcome") == "TARGET_1_HIT")
    t2_hits = sum(1 for r in valid if r.get("outcome") == "TARGET_2_HIT")
    sl_hits = sum(1 for r in valid if r.get("outcome") == "SL_HIT")
    unknown = sum(1 for r in valid if r.get("outcome") == "OUTCOME_UNKNOWN")

    # Backward compat: also count path_ambiguous entries not yet re-tracked
    legacy_ambiguous = sum(
        1 for r in valid
        if r.get("path_ambiguous") and r.get("outcome") not in ("OUTCOME_UNKNOWN",)
    )
    ambiguous = unknown + legacy_ambiguous

    calls = [r for r in valid if r.get("signal") == "CALL"]
    puts  = [r for r in valid if r.get("signal") == "PUT"]

    high_conf = [r for r in valid if (r.get("confidence") or 0) >= 8.0]
    low_conf  = [r for r in valid if (r.get("confidence") or 0) < 8.0]

    return {
        "total":                  total,
        "direction_pct":          round(correct / total * 100, 1),
        "sl_hit_pct":             round(sl_hits / total * 100, 1),
        "t1_correct_pct":         round(t1_hits / total * 100, 1),
        "t2_correct_pct":         round(t2_hits / total * 100, 1),
        "outcome_unknown_count":  unknown,
        "call_count":             len(calls),
        "put_count":              len(puts),
        "call_correct":           sum(1 for r in calls if r.get("was_correct")),
        "put_correct":            sum(1 for r in puts  if r.get("was_correct")),
        "high_conf_correct_pct":  (
            round(sum(1 for r in high_conf if r.get("was_correct")) / len(high_conf) * 100, 1)
            if high_conf else None
        ),
        "low_conf_correct_pct":   (
            round(sum(1 for r in low_conf if r.get("was_correct")) / len(low_conf) * 100, 1)
            if low_conf else None
        ),
        "ambiguous_outcomes":     ambiguous,
        "ambiguity_note": (
            f"{ambiguous}/{total} outcome(s) marked OUTCOME_UNKNOWN "
            f"(daily OHLC cannot confirm SL vs target sequence)"
            if ambiguous else None
        ),
    }


def check_entry_trigger_met(
    trade: dict,
    index_open: float | None,
    index_low_30m: float | None = None,
    index_high_30m: float | None = None,
) -> dict:
    """
    Check if the entry trigger price condition was met, using open price as proxy
    when 30-min intraday data is unavailable.
    Returns {"met": bool|None, "trigger_level": float|None, "reason": str}
    """
    trigger = trade.get("entry_trigger", "")
    signal  = trade.get("signal", "")

    if not trigger or index_open is None:
        return {"met": None, "trigger_level": None, "reason": "Trigger text or open price unavailable"}

    numbers = re.findall(r"\b(\d{4,6}(?:\.\d+)?)\b", trigger)
    if not numbers:
        return {"met": None, "trigger_level": None, "reason": "Could not parse trigger price from trigger text"}

    trigger_level = float(numbers[0])

    if signal == "PUT":
        check_price = index_low_30m if index_low_30m else index_open
        met = check_price < trigger_level
        return {
            "met":           met,
            "trigger_level": trigger_level,
            "check_price":   check_price,
            "reason": (
                f"PUT trigger: needed spot < {trigger_level:.0f}. "
                f"Open was {check_price:.0f} → {'TRIGGER MET' if met else 'TRIGGER NOT MET (trade should not have been entered)'}"
            ),
        }
    elif signal == "CALL":
        check_price = index_high_30m if index_high_30m else index_open
        met = check_price > trigger_level
        return {
            "met":           met,
            "trigger_level": trigger_level,
            "check_price":   check_price,
            "reason": (
                f"CALL trigger: needed spot > {trigger_level:.0f}. "
                f"Open was {check_price:.0f} → {'TRIGGER MET' if met else 'TRIGGER NOT MET (trade should not have been entered)'}"
            ),
        }
    return {"met": None, "trigger_level": None, "reason": "Unknown signal type"}


def derive_data_quality(meta: dict, ctx: dict) -> dict:
    """
    Derive data quality flags from stored brief meta and market_context.
    Works on existing briefs without needing re-fetch.
    """
    flags    = []
    warnings = []

    fii = meta.get("fii_dii", {})
    if fii.get("fii_net_buy") == 0.0 and fii.get("dii_net_buy") == 0.0:
        flags.append("FII_DII_DATA_ZERO")
        warnings.append("FII/DII cash flow = 0 — Institutional Flow layer (20% weight) likely missing")

    smart_dir = str(ctx.get("smart_money_direction", ""))
    if "unavailable" in smart_dir.lower() or "cannot" in smart_dir.lower():
        flags.append("PARTICIPANT_OI_MISSING")
        warnings.append("Participant-wise Derivative OI unavailable — smart money direction unconfirmed (~10% weight)")

    nifty_pcr = meta.get("nifty_pcr", 1.0) or 1.0
    if nifty_pcr >= 1.3:
        flags.append("PCR_CONTRARIAN_BULLISH")
        warnings.append(
            f"Nifty PCR {nifty_pcr:.3f} ≥ 1.3: extreme put crowding — contrarian BULLISH signal. "
            f"Short-covering bounce risk is HIGH. PUT trades carry elevated reversal risk."
        )
    elif nifty_pcr <= 0.6:
        flags.append("PCR_CONTRARIAN_BEARISH")
        warnings.append(
            f"Nifty PCR {nifty_pcr:.3f} ≤ 0.6: extreme call crowding — contrarian BEARISH signal. "
            f"CALL trades carry elevated reversal risk."
        )

    bn_pcr = meta.get("banknifty_pcr", 1.0) or 1.0
    if bn_pcr <= 0.7:
        flags.append("BANKNIFTY_PCR_BEARISH_CONTRARIAN")
        warnings.append(
            f"BankNifty PCR {bn_pcr:.3f} ≤ 0.7: more calls written than puts — "
            f"option writers expect limited upside but contrarian squeeze is possible."
        )

    missing_layers = []
    if "FII_DII_DATA_ZERO" in flags:
        missing_layers.append("Institutional Flow / FII-DII Cash (20% weight)")
    if "PARTICIPANT_OI_MISSING" in flags:
        missing_layers.append("Smart Money Derivatives OI (~10% weight)")

    safe_to_trade = len(missing_layers) == 0

    return {
        "flags":          flags,
        "warnings":       warnings,
        "missing_layers": missing_layers,
        "safe_to_trade":  safe_to_trade,
        "penalty_note": (
            f"⚠ {len(missing_layers)} key data layer(s) unavailable. "
            f"Displayed confidence scores may be inflated by ~0.5–1.0 points."
            if missing_layers else "All key data layers available."
        ),
    }


def audit_predictions_vs_triggers(
    morning_brief: dict,
    accuracy_log: list[dict],
) -> list[dict]:
    """
    For each trade in morning_brief, check if entry trigger was met using
    index_open from accuracy_log as proxy. Returns per-trade audit dicts.
    """
    trades  = morning_brief.get("trades", [])
    log_map = {r["symbol"]: r for r in accuracy_log if not r.get("error")}
    results = []

    for trade in trades:
        sym = trade.get("symbol", "")
        rec = log_map.get(sym)

        trigger_check = {"met": None, "reason": "No outcome data available for this date"}
        if rec:
            trigger_check = check_entry_trigger_met(trade, rec.get("index_open"))

        results.append({
            "symbol":        sym,
            "signal":        trade.get("signal", ""),
            "confidence":    trade.get("confidence"),
            "was_correct":   rec.get("was_correct") if rec else None,
            "outcome":       rec.get("outcome") if rec else None,
            "path_note":     rec.get("path_note") if rec else None,
            "trigger_check": trigger_check,
        })
    return results


def explain_outcome(result: dict) -> str:
    """Return a human-readable audit string for a single tracked outcome."""
    sym     = result.get("symbol", "?")
    signal  = result.get("signal", "?")
    outcome = result.get("outcome", "?")
    correct = result.get("was_correct")
    chg_pct = result.get("day_change_pct", 0) or 0

    direction_word = "rose" if chg_pct > 0 else "fell"
    corr_word      = "CORRECT" if correct else "WRONG"

    lines = [
        f"{sym} {signal}: direction {corr_word} (market {direction_word} {abs(chg_pct):.2f}%)",
        f"Outcome: {outcome}",
    ]
    if result.get("path_note"):
        lines.append(f"⚠ {result['path_note']}")
    if result.get("sl_hit") and not correct:
        lines.append("Stop-loss was triggered (adverse move exceeded SL threshold)")
    if result.get("target_1_reached") and correct:
        lines.append("Target 1 reached")
    if result.get("target_2_reached") and correct:
        lines.append("Target 2 reached")
    if result.get("data_source") == "INTRADAY":
        lines.append("Data source: intraday candles (fill sequence confirmed)")
    elif result.get("data_source") == "DAILY_OHLC":
        lines.append("Data source: daily OHLC (intraday sequence not confirmed)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intraday-aware outcome resolution
# ---------------------------------------------------------------------------

def resolve_outcome_with_intraday(
    trade: dict,
    daily_ohlc: dict,
    intraday_candles: "pd.DataFrame | None" = None,
) -> dict:
    """
    Resolve trade outcome using intraday candles when available; fall back to
    daily OHLC otherwise.

    Parameters
    ----------
    trade            : trade dict with signal, entry_price, stop_loss, target_1, target_2
    daily_ohlc       : {"open_p", "high_p", "low_p", "was_correct"} from yfinance
    intraday_candles : DataFrame with columns [datetime, open, high, low, close, volume]
                       sorted chronologically; None or empty → daily OHLC fallback

    Returns
    -------
    {
      "outcome":          str,   # TARGET_2_HIT | TARGET_1_HIT | SL_HIT |
                                 # DIRECTION_RIGHT | DIRECTION_WRONG | OUTCOME_UNKNOWN
      "sl_hit":           bool,
      "target_1_reached": bool,
      "target_2_reached": bool,
      "path_ambiguous":   bool,
      "path_note":        str | None,
      "data_source":      "INTRADAY" | "DAILY_OHLC",
    }
    """
    import pandas as pd

    signal    = trade.get("signal", "")
    entry_prem = trade.get("entry_price")
    sl_prem    = trade.get("stop_loss")
    t1_prem    = trade.get("target_1")
    t2_prem    = trade.get("target_2")

    has_intraday = (
        intraday_candles is not None
        and isinstance(intraday_candles, pd.DataFrame)
        and not intraday_candles.empty
        and entry_prem is not None
        and sl_prem is not None
        and t1_prem is not None
    )

    if has_intraday:
        return _resolve_intraday(signal, entry_prem, sl_prem, t1_prem, t2_prem,
                                 intraday_candles, daily_ohlc)
    return _resolve_daily_ohlc(signal, entry_prem, sl_prem, t1_prem, t2_prem, daily_ohlc)


def _resolve_intraday(signal, entry_prem, sl_prem, t1_prem, t2_prem, df, daily_ohlc):
    """Determine SL vs target hit order from intraday candle sequence."""
    was_correct = daily_ohlc.get("was_correct", False)

    # Use first candle's open as proxy for spot at trade entry
    entry_spot = float(df.iloc[0]["open"])
    cum_high   = entry_spot
    cum_low    = entry_spot

    sl_time = t1_time = t2_time = None

    for _, candle in df.iterrows():
        cum_high = max(cum_high, float(candle["high"]))
        cum_low  = min(cum_low,  float(candle["low"]))

        if signal == "PUT":
            worst_prem = entry_prem - (cum_high - entry_spot) * _ATM_DELTA
            best_prem  = entry_prem + (entry_spot - cum_low)  * _ATM_DELTA
        else:
            worst_prem = entry_prem - (entry_spot - cum_low)  * _ATM_DELTA
            best_prem  = entry_prem + (cum_high - entry_spot) * _ATM_DELTA

        ts = candle["datetime"]
        if sl_time is None and sl_prem and worst_prem <= sl_prem:
            sl_time = ts
        if t1_time is None and t1_prem and best_prem >= t1_prem:
            t1_time = ts
        if t2_prem and t2_time is None and best_prem >= t2_prem:
            t2_time = ts

    sl_hit     = sl_time is not None
    t1_reached = t1_time is not None
    t2_reached = t2_time is not None

    # Chronological order determines outcome
    if sl_hit and t1_reached:
        if sl_time <= t1_time:
            # SL was hit first — trade stopped out regardless of later recovery
            outcome      = "SL_HIT"
            t1_reached   = False
            t2_reached   = False
        else:
            # Target hit first
            if t2_reached and t2_time and t2_time <= sl_time:
                outcome = "TARGET_2_HIT"
            else:
                outcome    = "TARGET_1_HIT"
                t2_reached = False
    elif sl_hit:
        outcome = "SL_HIT"
    elif t2_reached and was_correct:
        outcome = "TARGET_2_HIT"
    elif t1_reached and was_correct:
        outcome = "TARGET_1_HIT"
    elif was_correct:
        outcome = "DIRECTION_RIGHT"
    else:
        outcome = "DIRECTION_WRONG"

    return {
        "outcome":          outcome,
        "sl_hit":           outcome == "SL_HIT",
        "target_1_reached": outcome in ("TARGET_1_HIT", "TARGET_2_HIT"),
        "target_2_reached": outcome == "TARGET_2_HIT",
        "path_ambiguous":   False,
        "path_note":        None,
        "data_source":      "INTRADAY",
    }


def _resolve_daily_ohlc(signal, entry_prem, sl_prem, t1_prem, t2_prem, daily_ohlc):
    """Daily OHLC fallback — cannot determine SL vs target hit chronological order."""
    open_p      = daily_ohlc.get("open_p", 0)
    high_p      = daily_ohlc.get("high_p", 0)
    low_p       = daily_ohlc.get("low_p", 0)
    was_correct = daily_ohlc.get("was_correct", False)

    if entry_prem is None:
        return {
            "outcome":          "OUTCOME_UNKNOWN",
            "sl_hit":           False,
            "target_1_reached": False,
            "target_2_reached": False,
            "path_ambiguous":   True,
            "path_note":        "No entry premium — cannot estimate outcome from OHLC",
            "data_source":      "DAILY_OHLC",
        }

    if signal == "PUT":
        worst_move = high_p - open_p
        best_move  = open_p - low_p
    else:
        worst_move = open_p - low_p
        best_move  = high_p - open_p

    worst_prem = entry_prem - worst_move * _ATM_DELTA
    best_prem  = entry_prem + best_move  * _ATM_DELTA

    sl_hit     = bool(sl_prem  and worst_prem <= sl_prem)
    t1_reached = bool(t1_prem  and best_prem  >= t1_prem)
    t2_reached = bool(t2_prem  and best_prem  >= t2_prem)

    path_ambiguous = bool(sl_hit and (t1_reached or t2_reached) and not was_correct)

    outcome = (
        "OUTCOME_UNKNOWN" if path_ambiguous else
        "TARGET_2_HIT"    if t2_reached and was_correct else
        "TARGET_1_HIT"    if t1_reached and was_correct else
        "SL_HIT"          if sl_hit else
        "DIRECTION_RIGHT" if was_correct else
        "DIRECTION_WRONG"
    )

    return {
        "outcome":          outcome,
        "sl_hit":           sl_hit,
        "target_1_reached": t1_reached,
        "target_2_reached": t2_reached,
        "path_ambiguous":   path_ambiguous,
        "path_note": (
            "OHLC-only tracking: both SL and target triggered via day H/L extremes "
            "— intraday sequence unknown. Intraday tick data needed to confirm outcome."
            if path_ambiguous else None
        ),
        "data_source": "DAILY_OHLC",
    }

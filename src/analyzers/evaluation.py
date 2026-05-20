"""
Evaluation helpers for auditing prediction accuracy and entry trigger feasibility.
Reads from prediction_accuracy.json; does NOT call external APIs or Claude.
"""

from __future__ import annotations
import re


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
    return "\n".join(lines)

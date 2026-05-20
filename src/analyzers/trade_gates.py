"""
Trade eligibility gating — deterministic post-processing of Claude's trade brief.
Applied after Claude's API call to enforce hard validation rules before saving.

Gate priority (checked in order):
  1. DATA_INSUFFICIENT : both critical layers (FII/DII + Participant OI) missing → hard block
  2. CONFLICTING_SIGNALS: PCR contradicts trade direction → hard block
  3. CONFLICTING_SIGNALS: Max pain gap strongly opposes trade direction → hard block
  4. Data quality penalty: remaining missing layers reduce effective confidence
  5. Volume gate: low volume confirmation caps effective confidence at 6.8
  6. NO_TRADE: effective confidence < 7.0 after all adjustments

Output fields added to each trade:
  gate_status               : TRADE_ALLOWED | NO_TRADE | DATA_INSUFFICIENT | CONFLICTING_SIGNALS
  gate_reason               : human-readable explanation of why trade was gated
  gate_original_confidence  : Claude's raw confidence score
  gate_effective_confidence : confidence after penalties / caps
  gate_confidence_cap       : cap value applied (or None)
  gate_data_penalty         : total confidence deducted for missing data
"""

from __future__ import annotations

CONFIDENCE_FLOOR    = 7.0   # minimum confidence to recommend a trade
VOLUME_SCORE_MIN    = 5     # below this → cap confidence
VOLUME_CAP          = 6.8   # cap applied when volume_confirmation < VOLUME_SCORE_MIN
PCR_BULL_THRESHOLD  = 1.3   # PCR >= this → contrarian bullish; blocks PUT signals
PCR_BEAR_THRESHOLD  = 0.6   # PCR <= this → contrarian bearish; blocks CALL signals
MAX_PAIN_BLOCK_GAP  = 1000  # pts; gap > this AND opposing direction → hard block


# ---------------------------------------------------------------------------
# Individual gate checks
# ---------------------------------------------------------------------------

def _get_index_pcr(sym: str, nifty_sig: dict, bn_sig: dict) -> float | None:
    if sym == "NIFTY":
        return nifty_sig.get("pcr", {}).get("pcr")
    if sym == "BANKNIFTY":
        return bn_sig.get("pcr", {}).get("pcr")
    return None


def check_pcr_conflict(trade: dict, nifty_sig: dict, bn_sig: dict) -> tuple[bool, str]:
    """
    Returns (is_blocked, reason).
    PUT with PCR >= 1.3 = contrarian bullish → short-covering risk → block PUT.
    CALL with PCR <= 0.6 = contrarian bearish → reversal risk → block CALL.
    """
    sym    = trade.get("symbol", "")
    signal = trade.get("signal", "")
    pcr    = _get_index_pcr(sym, nifty_sig, bn_sig)

    if pcr is None:
        return False, ""

    if signal == "PUT" and pcr >= PCR_BULL_THRESHOLD:
        return True, (
            f"PCR {pcr:.3f} >= {PCR_BULL_THRESHOLD}: extreme put crowding (contrarian BULLISH). "
            f"Short-covering bounce risk HIGH — PUT signal blocked."
        )
    if signal == "CALL" and pcr <= PCR_BEAR_THRESHOLD:
        return True, (
            f"PCR {pcr:.3f} <= {PCR_BEAR_THRESHOLD}: extreme call crowding (contrarian BEARISH). "
            f"Reversal squeeze risk HIGH — CALL signal blocked."
        )
    return False, ""


def check_max_pain_conflict(trade: dict, nifty_sig: dict, bn_sig: dict) -> tuple[bool, str]:
    """
    Returns (is_blocked, reason).
    PUT with large max-pain gap ABOVE spot → strong upward pull → block.
    CALL with large max-pain gap BELOW spot → strong downward pull → block.
    """
    sym    = trade.get("symbol", "")
    signal = trade.get("signal", "")

    sig = nifty_sig if sym == "NIFTY" else (bn_sig if sym == "BANKNIFTY" else None)
    if sig is None:
        return False, ""

    mp_gap = sig.get("mp_gap")
    mp_dir = sig.get("mp_direction")

    if mp_gap is None or mp_dir is None:
        return False, ""

    gap_abs = abs(mp_gap)
    if gap_abs < MAX_PAIN_BLOCK_GAP:
        return False, ""

    if signal == "PUT" and mp_dir == "ABOVE_SPOT":
        return True, (
            f"Max pain {gap_abs:.0f} pts ABOVE spot creates strong upward gravitational pull. "
            f"PUT direction directly opposes max pain — signal blocked."
        )
    if signal == "CALL" and mp_dir == "BELOW_SPOT":
        return True, (
            f"Max pain {gap_abs:.0f} pts BELOW spot creates strong downward gravitational pull. "
            f"CALL direction directly opposes max pain — signal blocked."
        )
    return False, ""


def _data_quality_penalty(data_quality: dict) -> tuple[float, list[str]]:
    """Returns (penalty_amount, [reason_strings]) for soft confidence reduction."""
    penalty = 0.0
    reasons = []
    flags   = data_quality.get("flags", [])

    if "FII_DII_DATA_ZERO" in flags:
        penalty += 0.5
        reasons.append("FII/DII cash data zero (institutional layer 20% weight) → -0.5 confidence")
    if "PARTICIPANT_OI_MISSING" in flags:
        penalty += 0.5
        reasons.append("Participant OI unavailable (smart money unconfirmed) → -0.5 confidence")

    return penalty, reasons


def check_volume_gate(trade: dict) -> tuple[bool, float]:
    """Returns (is_low, vol_score). Low volume caps effective confidence."""
    bd        = trade.get("confidence_breakdown", {})
    vol_score = bd.get("volume_confirmation")
    if vol_score is None:
        return False, 10.0  # unknown = do not penalize
    return float(vol_score) < VOLUME_SCORE_MIN, float(vol_score)


# ---------------------------------------------------------------------------
# Gate one trade
# ---------------------------------------------------------------------------

def gate_trade(
    trade:        dict,
    nifty_sig:    dict,
    bn_sig:       dict,
    data_quality: dict,
) -> dict:
    """
    Apply all gates to a single trade and return it enriched with gate_* fields.
    gate_status: TRADE_ALLOWED | NO_TRADE | DATA_INSUFFICIENT | CONFLICTING_SIGNALS
    """
    original_conf  = float(trade.get("confidence") or 0.0)
    effective_conf = original_conf
    gate_status    = "TRADE_ALLOWED"
    gate_reasons: list[str] = []

    # --- Gate 1: DATA_INSUFFICIENT (hard block, both critical layers missing) ---
    missing = data_quality.get("missing_layers", [])
    if len(missing) >= 2:
        gate_status = "DATA_INSUFFICIENT"
        gate_reasons.append(
            f"Both critical data layers unavailable: {' | '.join(missing)}. "
            f"Institutional weight (≥40%) unverifiable — trade blocked."
        )

    # --- Gate 2: PCR conflict (hard block) ---
    if gate_status == "TRADE_ALLOWED":
        pcr_blocked, pcr_reason = check_pcr_conflict(trade, nifty_sig, bn_sig)
        if pcr_blocked:
            gate_status = "CONFLICTING_SIGNALS"
            gate_reasons.append(pcr_reason)

    # --- Gate 3: Max pain conflict (hard block) ---
    if gate_status == "TRADE_ALLOWED":
        mp_blocked, mp_reason = check_max_pain_conflict(trade, nifty_sig, bn_sig)
        if mp_blocked:
            gate_status = "CONFLICTING_SIGNALS"
            gate_reasons.append(mp_reason)

    # --- Gate 4: Data quality soft penalty (runs even for hard blocks, for reporting) ---
    dq_penalty, dq_reasons = _data_quality_penalty(data_quality)
    effective_conf -= dq_penalty
    gate_reasons.extend(dq_reasons)

    # --- Gate 5: Volume cap ---
    conf_cap = None
    vol_low, vol_score = check_volume_gate(trade)
    if vol_low:
        conf_cap       = VOLUME_CAP
        effective_conf = min(effective_conf, VOLUME_CAP)
        gate_reasons.append(
            f"Volume confirmation {vol_score:.0f}/10 (min {VOLUME_SCORE_MIN}) "
            f"→ confidence capped at {VOLUME_CAP}"
        )

    # --- Gate 6: Effective confidence threshold ---
    if gate_status == "TRADE_ALLOWED" and effective_conf < CONFIDENCE_FLOOR:
        gate_status = "NO_TRADE"
        gate_reasons.append(
            f"Effective confidence {effective_conf:.2f} < {CONFIDENCE_FLOOR} threshold "
            f"after data quality adjustments"
        )

    return {
        **trade,
        "gate_status":               gate_status,
        "gate_reason":               " | ".join(gate_reasons) if gate_reasons else "All gates passed",
        "gate_original_confidence":  original_conf,
        "gate_effective_confidence": round(effective_conf, 2),
        "gate_confidence_cap":       conf_cap,
        "gate_data_penalty":         round(dq_penalty, 2),
    }


# ---------------------------------------------------------------------------
# Gate the full morning brief
# ---------------------------------------------------------------------------

def apply_trade_gates(
    brief:        dict,
    data_quality: dict,
    nifty_sig:    dict,
    bn_sig:       dict,
) -> dict:
    """
    Post-process Claude's trade brief by gating every index trade.
    TRADE_ALLOWED trades stay in brief["trades"].
    Blocked trades move to brief["trades_gated_out"].
    brief["_gate_summary"] is added for dashboard and logging.
    """
    dq_penalty, dq_reasons = _data_quality_penalty(data_quality)

    trades_allowed: list[dict] = []
    trades_blocked: list[dict] = []

    for trade in brief.get("trades", []):
        gated = gate_trade(trade, nifty_sig, bn_sig, data_quality)
        if gated["gate_status"] == "TRADE_ALLOWED":
            trades_allowed.append(gated)
        else:
            trades_blocked.append(gated)

    result = dict(brief)
    result["trades"]           = trades_allowed
    result["trades_gated_out"] = trades_blocked
    result["_gates_applied"]   = True
    result["_gate_summary"]    = {
        "allowed":         len(trades_allowed),
        "blocked":         len(trades_blocked),
        "data_penalty":    round(dq_penalty, 2),
        "penalty_reasons": dq_reasons,
        "missing_layers":  data_quality.get("missing_layers", []),
    }

    return result

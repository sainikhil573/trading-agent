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

Stock trades (stock_trades[]) go through a simplified gate:
  Steps 4 and 6 only — no PCR/max-pain check (requires per-stock option chains).

Output fields added to each trade:
  gate_status               : TRADE_ALLOWED | NO_TRADE | DATA_INSUFFICIENT | CONFLICTING_SIGNALS
  gate_reason               : human-readable explanation of why trade was gated
  gate_original_confidence  : Claude's raw confidence score
  gate_effective_confidence : confidence after penalties / caps
  gate_confidence_cap       : cap value applied (or None)
  gate_data_penalty         : total confidence deducted for missing data

Top-level fields added to brief:
  post_gate_summary         : truthful one-line status after gates are applied
  watchlist_only            : valid stock trades moved out by max_trades_recommended cap
  stock_trades_gated_out    : stock trades blocked by gate logic
  _gate_summary.final_recommendation_status : ACTIONABLE_TRADES_AVAILABLE |
                                              NO_ACTIONABLE_TRADE | DATA_INSUFFICIENT
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
# Gate one index trade
# ---------------------------------------------------------------------------

def gate_trade(
    trade:        dict,
    nifty_sig:    dict,
    bn_sig:       dict,
    data_quality: dict,
) -> dict:
    """
    Apply all gates to a single index trade and return it enriched with gate_* fields.
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
# Gate one stock trade (simplified — no PCR/max-pain, data penalty + floor only)
# ---------------------------------------------------------------------------

def gate_stock_trade(trade: dict, data_quality: dict) -> dict:
    """
    Apply simplified gates to a stock F&O trade.
    Applies: data quality penalty (steps 4 & 6 of the index gate pipeline).
    Skips PCR and max-pain checks (requires per-stock option chain data).
    gate_status: TRADE_ALLOWED | NO_TRADE
    """
    original_conf  = float(trade.get("confidence") or 0.0)
    gate_reasons:  list[str] = []

    # Data quality penalty
    dq_penalty, dq_reasons = _data_quality_penalty(data_quality)
    effective_conf = original_conf - dq_penalty
    gate_reasons.extend(dq_reasons)

    # Confidence floor
    if effective_conf < CONFIDENCE_FLOOR:
        gate_status = "NO_TRADE"
        gate_reasons.append(
            f"Effective confidence {effective_conf:.2f} < {CONFIDENCE_FLOOR} threshold "
            f"after data quality adjustments"
        )
    else:
        gate_status = "TRADE_ALLOWED"

    return {
        **trade,
        "gate_status":               gate_status,
        "gate_reason":               " | ".join(gate_reasons) if gate_reasons else "All gates passed",
        "gate_original_confidence":  original_conf,
        "gate_effective_confidence": round(effective_conf, 2),
        "gate_confidence_cap":       None,
        "gate_data_penalty":         round(dq_penalty, 2),
    }


# ---------------------------------------------------------------------------
# Post-gate summary builder
# ---------------------------------------------------------------------------

def _build_post_gate_summary(
    idx_allowed:    list[dict],
    idx_blocked:    list[dict],
    stk_actionable: list[dict],
    stk_watchlist:  list[dict],
    stk_blocked:    list[dict],
    final_status:   str,
    penalty_reasons: list[str],
) -> str:
    """
    Build a truthful one-line summary that accurately reflects post-gate state.
    Replaces the Claude-generated morning_summary for display when trades are blocked.
    """
    if final_status == "ACTIONABLE_TRADES_AVAILABLE":
        parts = []
        if idx_allowed:
            syms = ", ".join(f"{t.get('symbol','?')} {t.get('signal','?')}" for t in idx_allowed)
            parts.append(f"{len(idx_allowed)} index trade(s) actionable: {syms}")
        if stk_actionable:
            syms = ", ".join(t.get("symbol","?") for t in stk_actionable)
            parts.append(f"{len(stk_actionable)} stock trade(s) actionable: {syms}")
        if idx_blocked:
            syms = ", ".join(t.get("symbol","?") for t in idx_blocked)
            parts.append(f"{len(idx_blocked)} index trade(s) blocked: {syms}")
        if stk_watchlist:
            syms = ", ".join(t.get("symbol","?") for t in stk_watchlist)
            parts.append(
                f"{len(stk_watchlist)} stock trade(s) moved to watchlist "
                f"(daily trade limit reached): {syms}"
            )
        return " | ".join(parts)

    if final_status == "DATA_INSUFFICIENT":
        penalty_str = (
            "Data penalties: " + "; ".join(penalty_reasons[:2]) + ". "
            if penalty_reasons else ""
        )
        return (
            "NO ACTIONABLE TRADES — critical institutional data layers unavailable. "
            + penalty_str
            + "Check DATA AVAILABILITY section. Consider running post-market after FII/DII publishes."
        )

    # NO_ACTIONABLE_TRADE
    blocked_desc = []
    for t in idx_blocked[:3]:
        blocked_desc.append(
            f"{t.get('symbol','?')} {t.get('signal','?')} blocked ({t.get('gate_status','?')})"
        )
    if penalty_reasons:
        blocked_desc.extend(penalty_reasons[:2])
    reason_str = "; ".join(blocked_desc) if blocked_desc else "confidence below 7.0 threshold"
    return f"NO ACTIONABLE TRADES today. {reason_str}."


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
    Post-process Claude's trade brief by gating every index trade AND every stock trade.

    Index trades  → gate_trade()       (all 6 gates including PCR/max-pain)
    Stock trades  → gate_stock_trade() (data penalty + confidence floor only)

    After gating:
    - TRADE_ALLOWED index trades stay in brief["trades"]
    - Blocked index trades move to brief["trades_gated_out"]
    - TRADE_ALLOWED stock trades up to max_trades_recommended cap → brief["stock_trades"]
    - Excess valid stock trades (over cap) → brief["watchlist_only"]
    - Blocked stock trades → brief["stock_trades_gated_out"]
    - market_context.trading_recommended updated to False when total_actionable == 0
    - brief["post_gate_summary"] added with truthful post-gate description
    - brief["_gate_summary"] enriched with final_recommendation_status
    """
    dq_penalty, dq_reasons = _data_quality_penalty(data_quality)

    # --- Gate index trades ---
    trades_allowed: list[dict] = []
    trades_blocked: list[dict] = []
    for trade in brief.get("trades", []):
        gated = gate_trade(trade, nifty_sig, bn_sig, data_quality)
        if gated["gate_status"] == "TRADE_ALLOWED":
            trades_allowed.append(gated)
        else:
            trades_blocked.append(gated)

    # --- Gate stock trades ---
    stock_allowed: list[dict] = []
    stock_blocked: list[dict] = []
    for trade in brief.get("stock_trades", []):
        gated = gate_stock_trade(trade, data_quality)
        if gated["gate_status"] == "TRADE_ALLOWED":
            stock_allowed.append(gated)
        else:
            stock_blocked.append(gated)

    # --- Enforce max_trades_recommended ---
    # Index trades have priority; remaining slots filled by highest-confidence stock trades.
    # Excess valid stock trades move to watchlist_only (not discarded — still worth watching).
    max_trades     = int(brief.get("max_trades_recommended") or 5)
    remaining_slots = max(0, max_trades - len(trades_allowed))
    stock_sorted    = sorted(stock_allowed, key=lambda t: float(t.get("confidence") or 0), reverse=True)
    stock_actionable = stock_sorted[:remaining_slots]
    stock_watchlist  = stock_sorted[remaining_slots:]

    # --- Final recommendation status ---
    total_actionable = len(trades_allowed) + len(stock_actionable)
    all_blocked      = trades_blocked + stock_blocked

    if total_actionable > 0:
        final_status = "ACTIONABLE_TRADES_AVAILABLE"
    elif all_blocked and all(t.get("gate_status") == "DATA_INSUFFICIENT" for t in all_blocked):
        final_status = "DATA_INSUFFICIENT"
    else:
        final_status = "NO_ACTIONABLE_TRADE"

    # --- Update trading_recommended ---
    ctx = dict(brief.get("market_context", {}))
    if total_actionable == 0:
        ctx["trading_recommended"] = False

    # --- Post-gate summary ---
    post_gate_summary = _build_post_gate_summary(
        trades_allowed, trades_blocked,
        stock_actionable, stock_watchlist, stock_blocked,
        final_status, dq_reasons,
    )

    result = dict(brief)
    result["market_context"]         = ctx
    result["trades"]                 = trades_allowed
    result["trades_gated_out"]       = trades_blocked
    result["stock_trades"]           = stock_actionable
    result["stock_trades_gated_out"] = stock_blocked
    result["watchlist_only"]         = stock_watchlist
    result["post_gate_summary"]      = post_gate_summary
    result["_gates_applied"]         = True
    result["_gate_summary"]          = {
        "allowed":                      len(trades_allowed),
        "blocked":                      len(trades_blocked),
        "stock_allowed":                len(stock_actionable),
        "stock_blocked":                len(stock_blocked),
        "stock_watchlisted":            len(stock_watchlist),
        "total_actionable":             total_actionable,
        "final_recommendation_status":  final_status,
        "data_penalty":                 round(dq_penalty, 2),
        "penalty_reasons":              dq_reasons,
        "missing_layers":               data_quality.get("missing_layers", []),
    }

    return result

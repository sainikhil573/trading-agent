"""
Pre-market checklist validator.

Before any trade is shown as GO, this runs a set of blocking and warning checks:
  1. Is today a trading day? (holiday check) — block if not
  2. Is VIX below 25? — block if above
  3. Is data_health GOOD or PARTIAL? — warn if POOR, block if CRITICAL
  4. Are at least 3 of 6 data layers FRESH or CACHED?
  5. Is signal direction consistent across timeframes (1D + 1H if available)?

Output: checklist_passed: bool + list of failed items
The checklist result is added to the trade brief JSON.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

VIX_BLOCK_THRESHOLD  = 25.0
MIN_FRESH_LAYERS     = 3
REQUIRED_LAYERS      = ["vix", "option_chain", "global_cues", "technicals", "fii_dii", "participant_oi"]


def _check_trading_day(check_date: Optional[date] = None) -> dict:
    try:
        from src.utils.trading_calendar import is_trading_day
        d = check_date or date.today()
        is_trading = is_trading_day(d)
        return {
            "name":   "TRADING_DAY",
            "passed": is_trading,
            "level":  "BLOCK",
            "detail": f"{d} is a trading day" if is_trading else f"{d} is a holiday/weekend — no trading",
        }
    except Exception as exc:
        return {"name": "TRADING_DAY", "passed": False, "level": "BLOCK",
                "detail": f"Calendar check failed: {exc}"}


def _check_vix(vix: Optional[float]) -> dict:
    if vix is None:
        return {"name": "VIX_BELOW_25", "passed": False, "level": "BLOCK",
                "detail": "VIX unavailable — cannot confirm safe trading environment"}
    passed = vix < VIX_BLOCK_THRESHOLD
    return {
        "name":   "VIX_BELOW_25",
        "passed": passed,
        "level":  "BLOCK",
        "detail": (
            f"VIX = {vix:.1f} — below {VIX_BLOCK_THRESHOLD} (safe)" if passed
            else f"VIX = {vix:.1f} — above {VIX_BLOCK_THRESHOLD} threshold: no trading today"
        ),
    }


def _check_data_health(health: Optional[dict]) -> dict:
    if health is None:
        return {"name": "DATA_HEALTH", "passed": True, "level": "WARN",
                "detail": "No health report found — assuming PARTIAL"}
    overall = health.get("overall", "UNKNOWN").upper()
    passed  = overall in ("HEALTHY", "CACHED", "PARTIAL")
    level   = "BLOCK" if overall == "CRITICAL" else "WARN"
    return {
        "name":   "DATA_HEALTH",
        "passed": passed,
        "level":  level,
        "detail": f"Data health = {overall}",
    }


def _check_fresh_layers(health: Optional[dict]) -> dict:
    if health is None:
        return {"name": "FRESH_LAYERS", "passed": False, "level": "WARN",
                "detail": "No health report — cannot verify layer freshness"}

    sources = health.get("sources", {})
    fresh_count = 0
    fresh_layers = []
    stale_layers = []

    for layer in REQUIRED_LAYERS:
        info   = sources.get(layer, {})
        status = (info if isinstance(info, str) else info.get("status", "UNKNOWN")).upper()
        if status in ("FRESH", "CACHED"):
            fresh_count += 1
            fresh_layers.append(layer)
        else:
            stale_layers.append(f"{layer}:{status}")

    passed = fresh_count >= MIN_FRESH_LAYERS
    return {
        "name":   "FRESH_LAYERS",
        "passed": passed,
        "level":  "WARN",
        "detail": (
            f"{fresh_count}/{len(REQUIRED_LAYERS)} layers fresh/cached "
            f"({'OK' if passed else f'need >={MIN_FRESH_LAYERS}'})"
            + (f" — stale: {', '.join(stale_layers)}" if stale_layers else "")
        ),
        "fresh_count":  fresh_count,
        "fresh_layers": fresh_layers,
    }


def _check_signal_consistency(trade: dict) -> dict:
    """Check if signal direction is internally consistent (1D + 1H if available)."""
    signal       = trade.get("signal", "").upper()
    timeframe_1d = trade.get("timeframe_1d_signal", "").upper()
    timeframe_1h = trade.get("timeframe_1h_signal", "").upper()

    if not signal:
        return {"name": "SIGNAL_CONSISTENCY", "passed": False, "level": "WARN",
                "detail": "No signal direction in trade dict"}

    conflicts = []
    if timeframe_1d and timeframe_1d != signal and timeframe_1d != "NEUTRAL":
        conflicts.append(f"1D={timeframe_1d}")
    if timeframe_1h and timeframe_1h != signal and timeframe_1h != "NEUTRAL":
        conflicts.append(f"1H={timeframe_1h}")

    passed = len(conflicts) == 0
    return {
        "name":   "SIGNAL_CONSISTENCY",
        "passed": passed,
        "level":  "WARN",
        "detail": (
            f"Signal {signal} consistent across timeframes" if passed
            else f"Signal {signal} conflicts with: {', '.join(conflicts)}"
        ),
    }


def run_premarket_checklist(
    trade:      Optional[dict] = None,
    vix:        Optional[float] = None,
    health:     Optional[dict] = None,
    check_date: Optional[date] = None,
) -> dict:
    """
    Run all pre-market checks.

    Parameters
    ----------
    trade      : single trade dict (used for signal consistency check)
    vix        : current VIX value
    health     : health report dict (from data/health/health_YYYY-MM-DD.json)
    check_date : date to check (defaults to today)

    Returns
    -------
    {
      "checklist_passed": bool,
      "failed_items": [{"name": str, "level": "BLOCK"|"WARN", "detail": str}],
      "all_items": [...],
    }
    """
    items = [
        _check_trading_day(check_date),
        _check_vix(vix),
        _check_data_health(health),
        _check_fresh_layers(health),
    ]
    if trade:
        items.append(_check_signal_consistency(trade))

    blockers = [it for it in items if not it["passed"] and it["level"] == "BLOCK"]
    warnings = [it for it in items if not it["passed"] and it["level"] == "WARN"]
    failed   = blockers + warnings

    return {
        "checklist_passed": len(blockers) == 0,
        "has_warnings":     len(warnings) > 0,
        "failed_items":     [{"name": it["name"], "level": it["level"], "detail": it["detail"]}
                             for it in failed],
        "all_items":        items,
        "block_count":      len(blockers),
        "warn_count":       len(warnings),
    }


def apply_checklist_to_brief(brief: dict, health: Optional[dict] = None) -> dict:
    """
    Run checklist for every actionable trade in a morning brief.
    Adds checklist_result to each trade dict.
    Returns modified brief (in-place mutation).
    """
    from datetime import date as date_type
    import re

    vix_raw = (
        brief.get("market_context", {}).get("vix") or
        brief.get("market_context", {}).get("india_vix")
    )
    try:
        vix = float(vix_raw) if vix_raw is not None else None
    except (TypeError, ValueError):
        vix = None

    date_str = brief.get("date", "") or ""
    try:
        parts = re.split(r"[-/]", date_str.strip())
        check_date = date_type(int(parts[0]), int(parts[1]), int(parts[2])) if len(parts) == 3 else None
    except Exception:
        check_date = None

    # Overall checklist (date + VIX + health)
    overall = run_premarket_checklist(vix=vix, health=health, check_date=check_date)
    brief["checklist_result"] = overall

    # Per-trade signal consistency check
    for trade in brief.get("trades", []) + brief.get("stock_trades", []):
        trade_check = run_premarket_checklist(
            trade=trade, vix=vix, health=health, check_date=check_date
        )
        trade["checklist_result"] = trade_check

    return brief

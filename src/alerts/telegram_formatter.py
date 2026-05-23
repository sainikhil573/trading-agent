"""
Telegram alert formatter — formats (does NOT send) alert messages.

Three alert types:
  - GO signal   : symbol, signal, confidence, entry, SL, T1
  - NO_TRADE day: brief reason
  - DATA_DEGRADED warning

Output: data/alerts/pending_alerts_YYYY-MM-DD.json

Actual bot connection added only after 10 clean paper trades are confirmed.
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are placeholders in .env.example.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger   = logging.getLogger(__name__)
OUT_DIR  = Path("data/alerts")


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def format_go_signal(trade: dict, vix: Optional[float] = None) -> str:
    """
    Format a GO signal alert for one trade.

    Example output:
      SIGNAL: WIPRO CALL [MID]
      Entry premium : ~7.0
      Stop-loss     : 3.5
      Target 1      : 10.5
      Confidence    : 7.5 / 10
      VIX           : 14.3
    """
    sym    = trade.get("symbol", "?")
    sig    = trade.get("signal", "?").upper()
    tier   = trade.get("confidence_tier", "?")
    conf   = trade.get("gate_effective_confidence") or trade.get("confidence") or "?"
    entry  = trade.get("entry_price")
    sl     = trade.get("stop_loss")
    t1     = trade.get("target_1")
    t2     = trade.get("target_2")
    strike = trade.get("strike")
    expiry = trade.get("expiry", "")

    lines = [
        f"[GO] {sym} {sig}",
        f"Confidence tier : {tier}",
        f"Confidence      : {conf} / 10",
    ]
    if strike:
        exp_str = f" {expiry}" if expiry else ""
        lines.append(f"Strike          : {strike}{exp_str}")
    if entry is not None:
        lines.append(f"Entry premium   : ~{entry}")
    if sl is not None:
        lines.append(f"Stop-loss       : {sl}")
    if t1 is not None:
        lines.append(f"Target 1        : {t1}")
    if t2 is not None:
        lines.append(f"Target 2        : {t2}")
    if vix is not None:
        lines.append(f"VIX             : {vix:.1f}")

    return "\n".join(lines)


def format_no_trade(reason: str, date_str: Optional[str] = None) -> str:
    """
    Format a NO_TRADE day alert.

    Example:
      [NO TRADE] 2026-05-22
      Reason: VIX = 27.3 — above 25 threshold
    """
    d = date_str or _today()
    lines = [
        f"[NO TRADE] {d}",
        f"Reason: {reason}",
    ]
    return "\n".join(lines)


def format_data_degraded(health: dict, date_str: Optional[str] = None) -> str:
    """
    Format a DATA_DEGRADED warning alert.

    Example:
      [DATA WARNING] 2026-05-22
      Health: DEGRADED
      Failed : fii_dii, participant_oi
    """
    d       = date_str or _today()
    overall = health.get("overall", "UNKNOWN")
    sources = health.get("sources", {})

    failed  = [k for k, v in sources.items()
               if (v if isinstance(v, str) else v.get("status", "")).upper() == "FAILED"]
    cached  = [k for k, v in sources.items()
               if (v if isinstance(v, str) else v.get("status", "")).upper() == "CACHED"]

    lines = [f"[DATA WARNING] {d}", f"Health : {overall}"]
    if failed:
        lines.append(f"Failed : {', '.join(failed)}")
    if cached:
        lines.append(f"Cached : {', '.join(cached)}")
    return "\n".join(lines)


def save_pending_alerts(
    alerts: list[dict],
    date_str: Optional[str] = None,
) -> Path:
    """
    Write formatted alerts to data/alerts/pending_alerts_YYYY-MM-DD.json.
    Each entry: {"type": str, "formatted": str, "payload": dict}
    """
    d        = date_str or _today()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"pending_alerts_{d}.json"

    existing = []
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            try:
                existing = json.load(f).get("alerts", [])
            except (json.JSONDecodeError, AttributeError):
                existing = []

    all_alerts = existing + alerts
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"date": d, "alerts": all_alerts}, f, indent=2, ensure_ascii=False)

    logger.info("Pending alerts saved: %s (%d total)", out_path, len(all_alerts))
    return out_path


def build_alerts_from_brief(
    brief:  dict,
    health: Optional[dict] = None,
    vix:    Optional[float] = None,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Build formatted alert list from a morning brief + health report.
    Does NOT save — call save_pending_alerts() separately.
    """
    alerts: list[dict] = []
    d = date_str or brief.get("date") or _today()

    # Data degraded warning
    if health:
        overall = health.get("overall", "").upper()
        if overall in ("DEGRADED", "CRITICAL"):
            msg = format_data_degraded(health, d)
            alerts.append({"type": "DATA_DEGRADED", "formatted": msg, "payload": health})

    # Actionable trades → GO alerts
    actionable = [
        t for t in (brief.get("trades", []) + brief.get("stock_trades", []))
        if t.get("checklist_result", {}).get("checklist_passed", True)
        and not t.get("gated_out")
    ]

    for trade in actionable:
        msg = format_go_signal(trade, vix=vix)
        alerts.append({"type": "GO_SIGNAL", "formatted": msg, "payload": trade})

    # If no trades, format NO_TRADE
    if not actionable:
        gate_summary = brief.get("_gate_summary", {})
        status       = brief.get("post_gate_summary", {})
        if isinstance(status, dict):
            reason = status.get("message", gate_summary.get("final_recommendation_status", "No actionable trades"))
        else:
            reason = str(status) if status else "No actionable trades"
        msg = format_no_trade(reason, d)
        alerts.append({"type": "NO_TRADE", "formatted": msg, "payload": {"reason": reason}})

    return alerts

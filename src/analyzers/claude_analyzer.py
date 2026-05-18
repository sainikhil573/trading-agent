"""
Claude API integration.
Sends the full market context and returns a structured trade brief as JSON.
Uses prompt caching on the system prompt to reduce cost on repeated daily runs.
"""

from __future__ import annotations
import json
import logging
from datetime import date

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (cached — changes rarely)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert Indian stock market analyst specialising in F&O (Futures & Options) trading on NSE.

Your role is to analyse pre-market data every morning and produce a structured trade brief for the trading day.

## Your expertise covers:
- Option chain analysis: PCR, max pain, OI buildup, IV skew
- Global cue interpretation: US futures, crude oil, USD/INR impact on Nifty
- India VIX and its effect on option premiums
- Identifying key support/resistance via OI concentration
- Risk management: entry, stop-loss, and target for each trade

## Output format — strictly valid JSON array:
[
  {
    "symbol": "NIFTY" | "BANKNIFTY" | stock_ticker,
    "instrument": "INDEX" | "STOCK",
    "signal": "CALL" | "PUT" | "NEUTRAL",
    "confidence": <integer 1-10>,
    "entry_price": <nearest ATM/OTM strike LTP as float>,
    "stop_loss": <float>,
    "target": <float>,
    "risk_reward": <float, e.g. 1.5>,
    "strike": <recommended option strike as integer>,
    "expiry": "<nearest expiry date string>",
    "reasoning": "<2-3 sentence explanation referencing the specific data points>"
  }
]

## Rules:
- Only recommend trades where confidence >= 6
- For NEUTRAL signals, still include the entry but note "wait for confirmation"
- Stop-loss must be ≥ 20% of entry price for index options (high leverage)
- Target must give at minimum 1:1.5 risk-reward ratio
- Reasoning must cite specific numbers from the data (e.g., "PCR of 0.77 suggests...", "23800CE has 95k OI buildup...")
- Return ONLY the JSON array — no markdown, no preamble, no explanation outside the JSON"""


def build_user_prompt(
    analysis_date: str,
    nifty_signal: dict,
    banknifty_signal: dict,
    global_cues: dict,
    top_stocks: list[dict],
) -> str:
    """Construct the user-turn message with all market data."""

    def _fmt_global(cues: dict) -> str:
        lines = []
        for label, data in cues.items():
            if not isinstance(data, dict):
                continue
            pct = data.get("pct_change")
            last = data.get("last")
            if last is None:
                continue
            arrow = "+" if (pct or 0) >= 0 else ""
            lines.append(f"  {label:22s}: {last:>10.2f}  ({arrow}{pct:.2f}%)")
        return "\n".join(lines)

    def _fmt_oi_levels(levels: dict) -> str:
        res = levels.get("resistance", [])
        sup = levels.get("support", [])
        r_str = ", ".join(f"{x['strike']} (CE OI {x['ce_oi']:,})" for x in res)
        s_str = ", ".join(f"{x['strike']} (PE OI {x['pe_oi']:,})" for x in sup)
        return f"  Resistance strikes: {r_str}\n  Support strikes   : {s_str}"

    def _fmt_oi_buildup(buildup: dict) -> str:
        ce = buildup.get("fresh_ce_buildup", [])
        pe = buildup.get("fresh_pe_buildup", [])
        c_str = ", ".join(f"{x['strike']} (+{x['chng_oi']:,})" for x in ce)
        p_str = ", ".join(f"{x['strike']} (+{x['chng_oi']:,})" for x in pe)
        return f"  Fresh CE buildup: {c_str}\n  Fresh PE buildup: {p_str}"

    def _fmt_signal(sig: dict) -> str:
        pcr = sig["pcr"]
        vix = sig["vix"]
        return f"""  Spot            : {sig['spot']}
  ATM Strike      : {sig['atm_strike']}
  Nearest Expiry  : {sig['nearest_expiry']}
  PCR             : {pcr['pcr']}  → {pcr['sentiment']} (contrarian: {pcr['contrarian']})
  PCR Note        : {pcr['note']}
  Max Pain        : {sig['max_pain']}  ({sig['mp_direction']}, gap {sig['mp_gap']})
  Pre-bias        : {sig['pre_bias']}
  Global Bias     : {sig['global_bias']}

  OI Levels:
{_fmt_oi_levels(sig['oi_levels'])}

  Fresh OI Buildup (today):
{_fmt_oi_buildup(sig['oi_buildup'])}"""

    stocks_section = ""
    if top_stocks:
        lines = [f"  {'Symbol':12s} {'LTP':>8s} {'%Chg':>8s} {'Bias':10s} {'Volume':>15s}"]
        for s in top_stocks[:8]:
            lines.append(
                f"  {s['symbol']:12s} {s['LTP']:>8.2f} {s['pct_change']:>+8.2f}% "
                f"{s['bias']:10s} {s['volume']:>15,}"
            )
        stocks_section = "\n".join(lines)

    prompt = f"""=== MORNING MARKET BRIEF — {analysis_date} ===

## INDIA VIX
  Value: {nifty_signal['vix']['value']}  Zone: {nifty_signal['vix']['zone']}
  Implication: {nifty_signal['vix']['implication']}

## GLOBAL CUES  (overall bias: {global_cues.get('overall_bias', 'UNKNOWN')})
{_fmt_global(global_cues)}

## NIFTY OPTION CHAIN ANALYSIS
{_fmt_signal(nifty_signal)}

## BANKNIFTY OPTION CHAIN ANALYSIS
{_fmt_signal(banknifty_signal)}

## TOP F&O STOCKS (by traded volume)
{stocks_section}

---
Based on this pre-market data, generate the trade brief JSON array.
Include NIFTY, BANKNIFTY, and 2-3 stocks from the list above where a clear signal exists.
Prioritise index trades. Only recommend trades with confidence >= 6."""

    return prompt


class ClaudeAnalyzer:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def generate_trade_brief(
        self,
        nifty_signal: dict,
        banknifty_signal: dict,
        global_cues: dict,
        top_stocks: list[dict],
        analysis_date: str | None = None,
    ) -> list[dict]:
        """
        Call Claude API with full market context.
        Returns a list of trade recommendation dicts.
        System prompt is sent with cache_control so it is cached across daily runs.
        """
        if analysis_date is None:
            analysis_date = date.today().strftime("%d-%b-%Y (%A)")

        user_prompt = build_user_prompt(
            analysis_date, nifty_signal, banknifty_signal, global_cues, top_stocks
        )

        logger.info("Sending market data to Claude API...")

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},   # prompt caching
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]          # drop opening fence line
            if raw_text.endswith("```"):
                raw_text = raw_text[: raw_text.rfind("```")].strip()

        # Log token usage
        usage = response.usage
        logger.info(
            "Claude usage — input: %d, output: %d, cache_read: %d, cache_write: %d",
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "cache_creation_input_tokens", 0),
        )

        try:
            trades = json.loads(raw_text)
            if not isinstance(trades, list):
                raise ValueError("Expected a JSON array")
            logger.info("Claude returned %d trade recommendations", len(trades))
            return trades
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse Claude JSON response: %s", exc)
            logger.error("Raw response: %s", raw_text[:500])
            return []

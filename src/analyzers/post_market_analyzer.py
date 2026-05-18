"""
Post-market Claude analyzer — runs at 3:30 PM IST after outcome tracker.
Grades today's predictions and produces a market memory for tomorrow's analysis.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

OUT_DIR = Path("data/processed")

POSTMARKET_SYSTEM_PROMPT = """You are an elite trading performance analyst reviewing end-of-day results.

You will receive:
1. Morning predictions (what was planned at 8 AM)
2. Actual market outcomes (OHLC, premium estimates, SL/T1/T2 results)

Grade each prediction:
- A: Excellent — target hit or direction correct with minimal drawdown
- B: Good — direction correct, partial target reached
- C: Mediocre — direction correct but SL nearly hit
- D: Poor — wrong direction but small loss
- F: Failed — SL hit badly, thesis completely wrong

Identify which analysis layers (global cues, institutional flow, derivatives, technicals) proved accurate vs misleading.

Build a "market memory" for tomorrow's morning analyst: what to watch, what the trend continuation looks like, and whether to apply a confidence adjustment.

confidence_adjustment rules:
- +0.5: multiple layers correct today, trend intact — be more aggressive tomorrow
-  0.0: mixed results, neutral
- -0.5: key signal layers failed — be more conservative tomorrow
- Never exceed ±1.0

Return ONLY valid JSON, no markdown:
{
  "date": "YYYY-MM-DD",
  "analysis_time": "HH:MM IST",
  "overall_grade": "A/B/C/D/F",
  "accuracy_summary": "2-3 sentences on how the day played out vs prediction",
  "trade_grades": [
    {
      "symbol": "...",
      "signal": "CALL/PUT",
      "outcome": "TARGET_2_HIT/TARGET_1_HIT/SL_HIT/DIRECTION_RIGHT/DIRECTION_WRONG",
      "grade": "A/B/C/D/F",
      "grade_reasoning": "one sentence explaining the grade"
    }
  ],
  "what_worked": ["list of analysis factors that proved correct today"],
  "what_failed": ["list of analysis factors that misled today"],
  "key_level_observations": "which support/resistance levels held or broke",
  "institutional_pattern": "what FII/DII/PRO behavior was confirmed or denied today",
  "tomorrow_bias": {
    "confidence_adjustment": 0.0,
    "adjustment_reason": "why to add/subtract this from tomorrow's confidence",
    "watch_levels": [],
    "key_reminder": "one critical thing to remember for tomorrow's analysis"
  }
}"""


class PostMarketAnalyzer:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def generate_postmarket_analysis(
        self,
        today: str,
        trade_results: list[dict],
        morning_brief: dict,
    ) -> dict:
        prompt = self._build_prompt(today, trade_results, morning_brief)
        logger.info("Sending post-market data to Claude API...")

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=[{
                "type": "text",
                "text": POSTMARKET_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        usage = response.usage
        logger.info(
            "Post-market Claude usage — in: %d  out: %d  cache_read: %d",
            usage.input_tokens, usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")].strip()

        try:
            result = json.loads(raw)
            out_path = OUT_DIR / f"post_market_{today}.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info("Post-market analysis saved -> %s", out_path)
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse post-market JSON: %s", exc)
            logger.error("Raw (first 400 chars): %s", raw[:400])
            return {"error": str(exc)}

    def _build_prompt(
        self,
        today: str,
        trade_results: list[dict],
        morning_brief: dict,
    ) -> str:
        ctx  = morning_brief.get("market_context", {})
        meta = morning_brief.get("_meta", {})

        return f"""=== POST-MARKET ANALYSIS — {today} ===

## MORNING PREDICTIONS (8:00 AM analysis)
  Overall bias      : {ctx.get('overall_market_bias', '?')}
  VIX zone          : {ctx.get('vix_zone', '?')}
  FII futures       : {ctx.get('fii_futures_position', '?')}
  Global bias       : {ctx.get('global_bias', '?')}
  Smart money dir   : {ctx.get('smart_money_direction', '?')}
  Nifty spot @ 8AM  : {meta.get('nifty_spot', '?')}
  BankNifty @ 8AM   : {meta.get('banknifty_spot', '?')}
  VIX @ 8AM         : {meta.get('vix_at_8am', '?')}

  Morning summary   : {morning_brief.get('morning_summary', 'N/A')}

## ACTUAL OUTCOMES (3:30 PM close data)
{json.dumps(trade_results, indent=2)}

---
Grade all predictions, identify what analysis layers worked/failed,
and produce tomorrow's market memory. Return only the JSON object."""

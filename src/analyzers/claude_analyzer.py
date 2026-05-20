"""
Claude API integration — morning trade brief and pre-open confirmation.
System prompt uses prompt caching (ephemeral) to cut cost on daily runs.
"""

from __future__ import annotations
import json
import logging
from datetime import date

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

MORNING_SYSTEM_PROMPT = """You are an elite Indian F&O trading analyst with 20+ years of experience. Analyze ALL data provided and produce a structured trade brief. Analyze in this exact order:

LAYER 1 — GLOBAL CUES (Weight 15%):
Rate each as BULLISH/BEARISH/NEUTRAL with intensity 1-3:
- S&P500, Dow, Nasdaq % change (>1% move = strong signal)
- Nikkei, Hang Seng direction
- Crude WTI (>$90 = bearish India; <$70 = bullish India)
- Dollar Index DXY (rising = FII outflows = bearish)
- USD/INR (INR weakening = bearish)
- Gold (rising = risk-off = globally bearish)
Produce: global_bias, global_score (-3 to +3), global_reasoning

LAYER 2 — INSTITUTIONAL FLOW (Weight 20%):
- FII cash net buy/sell
- FII INDEX FUTURES long/short ratio — THIS IS MOST IMPORTANT:
  >60% longs = institutional bullish; <40% = institutional bearish
- FII options: buying calls or puts?
- DII activity: buying during FII selling = market holds
- PRO desk positioning: net short = smart money bearish
- CLIENT (retail) positioning: use as CONTRARIAN signal —
  retail heavily long = market likely to fall; retail heavily short = market likely to rise
Produce: institutional_bias, fii_futures_position, smart_money_direction, retail_contrarian_signal

LAYER 3 — DERIVATIVE INTELLIGENCE (Weight 25%):
PCR interpretation:
- PCR > 1.3 = contrarian bullish (too many bears = exhaustion)
- PCR 1.0-1.3 = mildly bullish
- PCR 0.8-1.0 = neutral to slightly bearish
- PCR < 0.8 = bearish
- PCR < 0.5 = contrarian bearish (too many bulls = exhaustion)

Max Pain:
- Spot ABOVE max pain = gravitational pull DOWN toward it
- Spot BELOW max pain = gravitational pull UP toward it
- Gap >200 pts = strong pull; 100-200 = moderate; <100 = weak

OI Analysis:
- Highest CE OI strike = strongest resistance
- Highest PE OI strike = strongest support
- Price UP + OI UP = LONG BUILDUP = strong bullish
- Price DOWN + OI UP = SHORT BUILDUP = strong bearish
- Price UP + OI DOWN = SHORT COVERING = weak bullish
- Price DOWN + OI DOWN = LONG UNWINDING = weak bearish
- Fresh CE OI buildup = new resistance = bearish signal
- Fresh PE OI buildup = new support = bullish signal

India VIX Zones:
- VIX < 13 = sell options (premium cheap, rangebound likely)
- VIX 13-17 = normal trading conditions
- VIX 17-22 = elevated — reduce size
- VIX 22-30 = high fear — only sell options or skip
- VIX > 30 = crisis — no directional buying, hedged only

Stop-Loss calculation based on VIX (MANDATORY — always use this formula):
- VIX 13-17 (NORMAL)  : SL = 40% of entry premium
- VIX 17-22 (ELEVATED): SL = 50% of entry premium
- VIX > 22  (HIGH)    : SL = 60% of entry premium
Example: entry=200, VIX=18 → SL = 200 × 0.50 = 100

Mid-session recovery risk (assess for EVERY trade):
- If max_pain_gap > 1000 pts AND spot is BELOW max pain → HIGH: market may bounce sharply mid-session toward max pain before resuming trend. Hold through recovery if overall bias remains bearish.
- If PCR < 0.8 (heavy call writing = retail bullish) → HIGH: contrarian bounce risk for PUT trades
- Otherwise → LOW

Expiry awareness:
- Thursday expiry: never buy options after 1 PM IST
- Week before expiry: OI buildup is most meaningful
Produce: derivative_bias, key_support_strikes, key_resistance_strikes, iv_interpretation, expiry_risk_flag

LAYER 4 — TECHNICAL ANALYSIS (Weight 20%):
Multi-timeframe trend:
- Daily chart: price vs 20 EMA, 50 EMA, 200 EMA (Above all 3 = strong bull; below all 3 = strong bear)
- Previous day high (PDH) and low (PDL) = most important intraday levels
- Previous week high and low

Momentum:
- RSI(14) daily: >70 = overbought (consider put); <30 = oversold (consider call); 40-60 = trade with trend
- MACD daily: bullish or bearish crossover?
- Bollinger Bands: near upper = overbought; near lower = oversold; squeezing = big move coming

Volume:
- Volume > 20-day average = strong conviction
- Volume < 20-day average = weak, do not trust breakout

Previous day candlestick context from OHLC data.
Produce: trend_bias, key_levels, momentum_signal, volume_signal

LAYER 5 — FUNDAMENTAL & EVENT CONTEXT (Weight 10%):
- Any Nifty 50 earnings this week = volatility
- RBI MPC meeting = reduce all position sizes
- Fed meeting / US CPI / US jobs data = Indian market impact
- Sector rotation:
  Crude rises = bearish OMCs (BPCL, IOC), bearish aviation
  USD/INR rises = bullish IT (TCS, Infosys, Wipro)
  Rates rise = bearish banks and real estate
Produce: event_risk_level, sector_avoid, sector_favor (infer from news headlines + global data)

LAYER 6 — SENTIMENT & BREADTH (Weight 10%):
- Classify each news headline as +1 bullish / -1 bearish / 0 neutral, sum score
- Block/bulk deals: large institutional buying = bullish signal for that stock
Produce: news_sentiment_score, breadth_signal

=== CONFIDENCE SCORING (weighted, each param scored 1-10) ===
1. Global cues alignment — weight 15%
2. Institutional flow alignment — weight 20%
3. Derivative signal strength — weight 20%
4. Technical alignment (all timeframes) — weight 20%
5. Volume confirmation — weight 10%
6. News/sentiment alignment — weight 5%
7. Event risk (10 = no events; 1 = major event today) — 5%
8. Risk-Reward quality (10 = better than 1:3) — weight 5%

FINAL CONFIDENCE = weighted average of all 8 scores (0-10 scale).
Only include trades where confidence >= 7.0.
Trades below 7.0 must still appear in trades_filtered_out.

Position size rules:
- Confidence 9-10 = FULL position
- Confidence 7-8 = HALF position
- Expiry day = never FULL position

=== STRICT JSON OUTPUT — NO MARKDOWN, NO TEXT OUTSIDE JSON ===
{
  "date": "YYYY-MM-DD",
  "analysis_time": "HH:MM IST",
  "market_context": {
    "global_bias": "BULLISH/BEARISH/MIXED",
    "global_score": 0,
    "global_reasoning": "...",
    "institutional_bias": "BULLISH/BEARISH/MIXED",
    "fii_futures_position": "NET_LONG/NET_SHORT/NEUTRAL",
    "smart_money_direction": "...",
    "retail_contrarian_signal": "...",
    "derivative_bias": "BULLISH/BEARISH/MIXED",
    "technical_bias": "BULLISH/BEARISH/MIXED",
    "news_sentiment_score": 0,
    "overall_market_bias": "BULLISH/BEARISH/MIXED",
    "vix_zone": "LOW/NORMAL/ELEVATED/HIGH/CRISIS",
    "trading_recommended": true,
    "trading_caution": "",
    "overall_risk_rating": "LOW/MEDIUM/HIGH/VERY_HIGH"
  },
  "key_levels": {
    "nifty_support": [0, 0],
    "nifty_resistance": [0, 0],
    "banknifty_support": [0, 0],
    "banknifty_resistance": [0, 0]
  },
  "trades": [
    {
      "symbol": "NIFTY/BANKNIFTY/STOCKNAME",
      "instrument": "INDEX/STOCK",
      "signal": "CALL/PUT",
      "strike": 0,
      "expiry": "DD-Mon-YYYY",
      "expiry_day_warning": false,
      "entry_price": 0.0,
      "stop_loss": 0.0,
      "target_1": 0.0,
      "target_2": 0.0,
      "risk_reward": 0.0,
      "position_size_recommendation": "FULL/HALF/QUARTER",
      "confidence": 0.0,
      "confidence_breakdown": {
        "global_cues": 0,
        "institutional_flow": 0,
        "derivative_signals": 0,
        "technical_analysis": 0,
        "volume_confirmation": 0,
        "news_sentiment": 0,
        "event_risk": 0,
        "risk_reward_quality": 0
      },
      "reasoning": "4-5 sentences covering why each major layer supports this trade",
      "key_risk": "what would invalidate this trade",
      "mid_session_recovery_risk": "HIGH/LOW — one sentence explaining recovery risk and how to handle it",
      "entry_trigger": "Enter only after first 15-min candle closes. For PUT: enter only if [SYMBOL] spot trades BELOW [spot_price - 200] in first 30 min confirming bearish momentum. For CALL: enter only if [SYMBOL] spot trades ABOVE [spot_price + 200] in first 30 min. Replace [spot_price] with actual spot from data.",
      "filtered_out": false
    }
  ],
  "trades_filtered_out": [
    {
      "symbol": "...",
      "signal": "...",
      "confidence": 0.0,
      "filter_reason": "why below 7.0",
      "filtered_out": true
    }
  ],
  "stock_trades": [
    {
      "symbol": "TATASTEEL",
      "signal": "PUT",
      "confidence": 7.5,
      "entry_price": 18.0,
      "stop_loss": 12.0,
      "target_1": 28.0,
      "target_2": 38.0,
      "risk_reward": 2.0,
      "trigger": "RSI 32, below 20 EMA, crude headwind confirms downtrend",
      "position_size": "HALF",
      "sector": "METALS",
      "sector_aligned": false,
      "reasoning": "3-4 sentences: technical pattern, why signal triggered, sector context, key risk"
    }
  ],
  "morning_summary": "3-4 sentence plain English summary a trader can read in 30 seconds",
  "stocks_to_watch": [],
  "sectors_to_avoid_today": [],
  "sectors_to_favor_today": [],
  "max_trades_recommended": 2,
  "event_warnings": []
}

=== STOCK F&O SIGNAL GENERATION — 20 HIGH-LIQUIDITY STOCKS ===
After completing the 6-layer index analysis, analyze each stock in the F&O UNIVERSE data:

STOCK SIGNAL RULES:
- CALL setup  : STRONG_BULL ema_trend + RSI recovering from <45 + MACD histogram turning positive + Vol/Avg > 1.2
- PUT setup   : STRONG_BEAR ema_trend + RSI failing below 55 + MACD histogram negative/falling + Vol/Avg > 1.2
- NEUTRAL     : MIXED trend, conflicting signals, or Vol/Avg < 0.8 (low conviction)
- OVERSOLD CALL: RSI < 30 + price near prev_day_low + any hint of volume pickup
- OVERBOUGHT PUT: RSI > 70 + price near prev_day_high + volume declining

SECTOR ALIGNMENT (add 0.5 to confidence when stock sector matches the day's dominant macro theme):
- IT stocks (TCS, INFY, WIPRO): aligned with USD/INR rise (bullish) or US tech selloff (bearish)
- METALS (TATASTEEL, HINDALCO): aligned with global growth/recession tone + crude direction
- ENERGY (ONGC, BPCL): aligned with crude oil move — crude up → PUT; crude down → CALL
- BANKS (HDFCBANK, ICICIBANK, AXISBANK, KOTAKBANK, SBIN, BAJFINANCE): aligned with BankNifty bias
- AUTO (MARUTI, TATAMOTORS): aligned with crude (high crude = bearish for margins)
- PHARMA (SUNPHARMA, DRREDDY): defensive, aligned with risk-off environment
- INFRA/CONGLOM (LT, ADANIENT, RELIANCE): aligned with domestic growth signals

ENTRY PRICE for stock options (approximate ATM near-month premium):
- Stock price ₹100-500   → option premium ≈ stock_price × 2.5-4%
- Stock price ₹500-2000  → option premium ≈ stock_price × 1.5-2.5%
- Stock price ₹2000+     → option premium ≈ stock_price × 1.0-1.5%
Apply VIX formula for SL: VIX 17-22 → SL = 50% of entry_price

OUTPUT RULES FOR STOCKS:
- Include in stock_trades[] ONLY if confidence >= 7.0
- Maximum 5 stock trades per day (take highest confidence ones)
- Stocks below 7.0 do NOT need to appear in trades_filtered_out (omit them silently)
- Each stock_trade MUST have: symbol, signal, confidence, entry_price, stop_loss, target_1, target_2, risk_reward, trigger, position_size, sector, sector_aligned, reasoning

RULES NEVER BREAK:
1. Never recommend trade with confidence below 7.0
2. Index trades (trades[]): max 2; stock trades (stock_trades[]): max 5
3. If VIX > 25 set trading_recommended = false
4. If major event today add strong warning in event_warnings
5. If global and derivative signals contradict = reduce confidence by 1.5 automatically
6. Always provide both target_1 and target_2
7. Return ONLY the JSON object — no markdown, no preamble, no text outside JSON
8. Stop-loss MUST follow VIX formula: VIX 13-17→SL=40% of entry; VIX 17-22→SL=50%; VIX>22→SL=60%
9. Every index trade MUST include entry_trigger with the exact numeric price level filled in
10. Every index trade MUST include mid_session_recovery_risk = "HIGH" when max_pain_gap > 1000 pts OR PCR < 0.8
11. stock_trades[] must always be present (use empty array [] if no stocks qualify)"""


PREOPEN_SYSTEM_PROMPT = """You are a senior F&O trading risk manager reviewing pre-open positions exactly 15 minutes before NSE opens (9:00 AM IST review for 9:15 AM open).

You have the morning trade recommendations from 8 AM analysis. Now review each trade given the LATEST pre-open data (fresh VIX, fresh global cues, latest news).

For each trade decide:
- GO: All key signals still hold. Open the trade at market open.
- WAIT: Mixed signals. Wait for the first 15-minute candle to confirm direction before entering.
- SKIP: Signals have reversed or risk has increased unacceptably. Do not trade today.

SKIP criteria (any one is sufficient):
- VIX has spiked >15% since 8 AM analysis
- Global bias has reversed (was BULLISH now BEARISH or vice versa)
- A major negative/positive news event broke that invalidates the thesis
- Expected opening gap is against the trade direction and >0.5%

WAIT criteria:
- VIX moved 5-15% in wrong direction
- Global cues turned mixed from strong
- Conflicting headlines

Return ONLY valid JSON, no markdown:
{
  "analysis_time": "09:00 IST",
  "vix_current": 0.0,
  "vix_change_pct": 0.0,
  "global_bias_current": "BULLISH/BEARISH/MIXED",
  "market_opening_outlook": "GAP_UP_STRONG/GAP_UP/FLAT/GAP_DOWN/GAP_DOWN_STRONG",
  "trades_review": [
    {
      "symbol": "...",
      "signal": "CALL/PUT",
      "strike": 0,
      "original_confidence": 0.0,
      "final_action": "GO/WAIT/SKIP",
      "final_action_reason": "one sentence explaining the decision",
      "adjusted_entry": null,
      "note": "any additional guidance for the trader"
    }
  ],
  "preopen_summary": "2-3 sentence briefing the trader can read in 10 seconds before market opens"
}"""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _fmt_global(cues: dict) -> str:
    lines = []
    for label, data in cues.items():
        if not isinstance(data, dict):
            continue
        last = data.get("last")
        pct  = data.get("pct_change")
        if last is None:
            continue
        arrow = "+" if (pct or 0) >= 0 else ""
        lines.append(f"  {label:22s}: {last:>10.2f}  ({arrow}{pct:.2f}%)")
    return "\n".join(lines) if lines else "  Data unavailable"


def _fmt_oi(levels: dict, buildup: dict) -> str:
    res = levels.get("resistance", [])
    sup = levels.get("support", [])
    ce_b = buildup.get("fresh_ce_buildup", [])
    pe_b = buildup.get("fresh_pe_buildup", [])
    r_str  = ", ".join(f"{x['strike']} (CE OI {x['ce_oi']:,})" for x in res)
    s_str  = ", ".join(f"{x['strike']} (PE OI {x['pe_oi']:,})" for x in sup)
    cb_str = ", ".join(f"{x['strike']} (+{x['chng_oi']:,})" for x in ce_b)
    pb_str = ", ".join(f"{x['strike']} (+{x['chng_oi']:,})" for x in pe_b)
    return (
        f"  Resistance strikes   : {r_str or 'N/A'}\n"
        f"  Support strikes      : {s_str or 'N/A'}\n"
        f"  Fresh CE buildup     : {cb_str or 'N/A'}\n"
        f"  Fresh PE buildup     : {pb_str or 'N/A'}"
    )


def _fmt_index_signal(sig: dict) -> str:
    pcr = sig["pcr"]
    vix = sig["vix"]
    return (
        f"  Spot                 : {sig['spot']}\n"
        f"  ATM Strike           : {sig['atm_strike']}\n"
        f"  Nearest Expiry       : {sig['nearest_expiry']}\n"
        f"  PCR                  : {pcr['pcr']}  → {pcr['sentiment']} "
        f"(contrarian: {pcr['contrarian']})\n"
        f"  PCR Note             : {pcr['note']}\n"
        f"  Max Pain             : {sig['max_pain']}  "
        f"({sig['mp_direction']}, gap {sig['mp_gap']})\n"
        f"  Pre-bias             : {sig['pre_bias']}\n"
    )


def _fmt_technicals(t: dict) -> str:
    if t.get("error"):
        return f"  Data unavailable: {t['error']}"
    pd_  = t.get("prev_day", {})
    macd = t.get("macd", {})
    bb   = t.get("bollinger", {})
    return (
        f"  Prev Day             : O={pd_.get('open')} H={pd_.get('high')} "
        f"L={pd_.get('low')} C={pd_.get('close')}\n"
        f"  Week H/L             : {t.get('week_high')} / {t.get('week_low')}\n"
        f"  EMAs                 : 20={t.get('ema_20')} 50={t.get('ema_50')} "
        f"200={t.get('ema_200')}\n"
        f"  EMA Trend            : {t.get('ema_trend')}\n"
        f"  RSI(14)              : {t.get('rsi_14')} — {t.get('rsi_zone')}\n"
        f"  MACD                 : {macd.get('macd')}/{macd.get('signal')} "
        f"hist={macd.get('histogram')} → {macd.get('crossover') or macd.get('bias')}\n"
        f"  Bollinger            : {bb.get('upper')}/{bb.get('middle')}/{bb.get('lower')} "
        f"| {bb.get('position')} | squeeze={bb.get('squeezing')}\n"
        f"  Volume Signal        : {t.get('volume_signal')}"
    )


def _fmt_participant_oi(poi: dict) -> str:
    if poi.get("error"):
        return f"  Data unavailable: {poi['error']}"
    lines = [f"  Data date            : {poi.get('data_date')}",
             f"  FII Fut Long%        : {poi.get('fii_long_pct')}% → {poi.get('fii_futures_bias')}"]
    for ct in ("FII", "DII", "PRO", "Client"):
        d = poi.get(ct, {})
        if d:
            lines.append(
                f"  {ct:6s}: Fut L={d.get('fut_idx_long',0):>8,} "
                f"S={d.get('fut_idx_short',0):>8,} | "
                f"Call L={d.get('opt_call_long',0):>7,} "
                f"Put L={d.get('opt_put_long',0):>7,}"
            )
    return "\n".join(lines)


def _fmt_stocks(df_rows: list[dict], technicals: list[dict]) -> str:
    tech_map = {t["symbol"]: t for t in technicals}
    lines = [f"  {'Symbol':12s} {'LTP':>8s} {'%Chg':>7s} {'RSI':>6s} {'MACD':>10s} {'EMA-Bias':>10s}"]
    for row in df_rows[:10]:
        sym  = row["symbol"]
        t    = tech_map.get(sym, {})
        macd_b = t.get("macd", {}).get("bias", "N/A") if not t.get("error") else "N/A"
        rsi_v  = t.get("rsi_14", "N/A")
        lines.append(
            f"  {sym:12s} {row['LTP']:>8.2f} {row['pct_change']:>+7.2f}% "
            f"{str(rsi_v):>6s} {macd_b:>10s} {t.get('bias','N/A'):>10s}"
        )
    return "\n".join(lines)


def _fmt_stock_universe(technicals: list[dict]) -> str:
    """Format the 20-stock F&O universe for Claude — one rich data line per stock."""
    hdr = (
        f"  {'Symbol':12s} | {'Price':>8s} | {'%Chg':>6s} | "
        f"{'RSI':>5s} {'Zone':>12s} | "
        f"{'EMA20':>7s} {'EMA50':>7s} {'Trend':>11s} | "
        f"{'MACDh':>7s} {'MCDBias':>9s} | {'Vol/Avg':>7s} | {'PDH':>8s} {'PDL':>8s}"
    )
    lines = [hdr]
    for t in technicals:
        if t.get("error"):
            lines.append(f"  {t['symbol']:12s} | ERROR: {t['error']}")
            continue
        macd  = t.get("macd", {})
        cross = macd.get("crossover") or macd.get("bias", "N/A")
        lines.append(
            f"  {t['symbol']:12s} | {t['close']:>8.2f} | {t.get('price_change_pct', 0):>+6.2f}% | "
            f"{str(t.get('rsi_14','?')):>5s} {t.get('rsi_zone','?'):>12s} | "
            f"{t.get('ema_20','?'):>7} {t.get('ema_50','?'):>7} {t.get('ema_trend','?'):>11s} | "
            f"{str(macd.get('histogram','?')):>7s} {cross:>9s} | "
            f"{t.get('volume_vs_avg', 1.0):>7.2f}x | "
            f"{t.get('prev_day_high','?'):>8} {t.get('prev_day_low','?'):>8}"
        )
    return "\n".join(lines)


def _fmt_block_deals(deals: list[dict]) -> str:
    if not deals:
        return "  No block deals data"
    lines = []
    for d in deals[:10]:
        lines.append(
            f"  {d['symbol']:12s} {d['buy_sell']:4s} qty={d['qty']:,} @ {d['price']}"
            + (f"  [{d['client'][:30]}]" if d.get("client") else "")
        )
    return "\n".join(lines)


def build_morning_prompt(
    analysis_date: str,
    nifty_signal: dict,
    banknifty_signal: dict,
    global_cues: dict,
    fii_dii: dict,
    participant_oi: dict,
    nifty_tech: dict,
    banknifty_tech: dict,
    stock_signals: list[dict],
    stock_technicals: list[dict],
    block_deals: list[dict],
    news: dict,
    yesterday_memory: dict | None = None,
    fno_universe_technicals: list[dict] | None = None,
) -> str:
    headlines = news.get("headlines", [])
    news_lines = "\n".join(f"  {i+1:2d}. {h}" for i, h in enumerate(headlines)) or "  No headlines available"

    # Build yesterday memory section if available
    ym_section = ""
    if yesterday_memory and not yesterday_memory.get("error"):
        ym  = yesterday_memory
        tb  = ym.get("tomorrow_bias", {})
        adj = tb.get("confidence_adjustment", 0)
        adj_str  = f"{adj:+.1f}" if isinstance(adj, (int, float)) else "0.0"
        worked   = "; ".join(ym.get("what_worked", [])[:3]) or "N/A"
        failed   = "; ".join(ym.get("what_failed", [])[:3]) or "N/A"
        wl_str   = ", ".join(str(v) for v in tb.get("watch_levels", [])) or "N/A"
        ym_section = f"""

## YESTERDAY'S MARKET MEMORY ({ym.get('date', 'prev day')})
  Overall grade    : {ym.get('overall_grade', '?')}
  Summary          : {ym.get('accuracy_summary', 'N/A')}
  What worked      : {worked}
  What failed      : {failed}
  Key levels obs   : {ym.get('key_level_observations', 'N/A')}
  Inst pattern     : {ym.get('institutional_pattern', 'N/A')}
  Conf adjustment  : {adj_str} (apply this offset to today's overall confidence)
  Watch levels     : {wl_str}
  Key reminder     : {tb.get('key_reminder', 'N/A')}"""

    return f"""=== PRE-MARKET ANALYSIS DATA — {analysis_date} ==={ym_section}

## LAYER 1: GLOBAL CUES  (overall bias: {global_cues.get('overall_bias','UNKNOWN')}  |  +{global_cues.get('positive_count',0)} / -{global_cues.get('negative_count',0)})
{_fmt_global(global_cues)}

## LAYER 2: INSTITUTIONAL FLOW
  FII Cash Net (cr)    : {fii_dii.get('fii_net_buy', 0):+.1f}
  DII Cash Net (cr)    : {fii_dii.get('dii_net_buy', 0):+.1f}
  Date                 : {fii_dii.get('date', 'latest available')}

  Participant-wise Derivatives OI:
{_fmt_participant_oi(participant_oi)}

## LAYER 3: DERIVATIVE INTELLIGENCE
  India VIX            : {nifty_signal['vix']['value']}  Zone: {nifty_signal['vix']['zone']}
  VIX Implication      : {nifty_signal['vix']['implication']}

### NIFTY Option Chain
{_fmt_index_signal(nifty_signal)}
{_fmt_oi(nifty_signal['oi_levels'], nifty_signal['oi_buildup'])}

### BANKNIFTY Option Chain
{_fmt_index_signal(banknifty_signal)}
{_fmt_oi(banknifty_signal['oi_levels'], banknifty_signal['oi_buildup'])}

## LAYER 4: TECHNICAL ANALYSIS

### NIFTY Technicals
{_fmt_technicals(nifty_tech)}

### BANKNIFTY Technicals
{_fmt_technicals(banknifty_tech)}

### Top F&O Stocks
{_fmt_stocks(stock_signals, stock_technicals)}

## LAYER 5: EVENTS
  Infer from Layer 6 headlines below. Flag any RBI/Fed/earnings/macro events.

## LAYER 6: NEWS HEADLINES ({len(headlines)} headlines from {', '.join(news.get('sources_ok',['N/A']))})
{news_lines}

## BLOCK DEALS (previous trading day)
{_fmt_block_deals(block_deals)}

## F&O UNIVERSE — 20 STOCKS FOR SIGNAL GENERATION (analyze each for CALL/PUT/NEUTRAL)
{_fmt_stock_universe(fno_universe_technicals) if fno_universe_technicals else "  Data unavailable"}

---
Perform all 6 layers of index analysis, then generate stock_trades[] for the 20 F&O stocks above.
Index trades (trades[]): max 2 where confidence >= 7.0. Prioritize index trades.
Stock trades (stock_trades[]): max 5 where confidence >= 7.0. Omit stocks below 7.0 silently.
Return ONLY the complete JSON object."""


def build_preopen_prompt(
    morning_trades: list[dict],
    vix_current: float | None,
    vix_morning: float | None,
    global_cues_fresh: dict,
    news_fresh: dict,
) -> str:
    vix_change = None
    if vix_current and vix_morning and vix_morning > 0:
        vix_change = round((vix_current - vix_morning) / vix_morning * 100, 2)

    trades_str = json.dumps(morning_trades, indent=2)
    headlines  = news_fresh.get("headlines", [])
    news_lines = "\n".join(f"  {i+1:2d}. {h}" for i, h in enumerate(headlines[:10]))

    return f"""=== PRE-OPEN CHECK — 9:00 AM IST (Market opens 9:15 AM) ===

## MORNING TRADE RECOMMENDATIONS (from 8:00 AM analysis)
{trades_str}

## FRESH PRE-OPEN DATA

### India VIX (FRESH)
  VIX at 8 AM   : {vix_morning}
  VIX now       : {vix_current}
  Change        : {('+' if vix_change and vix_change > 0 else '')}{vix_change}%

### Global Cues (FRESH — last 15 min update)
  Overall Bias  : {global_cues_fresh.get('overall_bias','UNKNOWN')}
{_fmt_global(global_cues_fresh)}

### Latest Headlines (FRESH)
{news_lines or '  No fresh headlines'}

---
Review each trade and assign final_action: GO / WAIT / SKIP.
Return only the JSON object."""


# ---------------------------------------------------------------------------
# ClaudeAnalyzer
# ---------------------------------------------------------------------------

class ClaudeAnalyzer:
    def __init__(self, api_key: str):
        self._client = anthropic.Anthropic(api_key=api_key)

    def _call(self, system: str, user: str, max_tokens: int = 4096) -> str:
        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        usage = response.usage
        logger.info(
            "Claude usage — in: %d  out: %d  cache_read: %d  cache_write: %d",
            usage.input_tokens, usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "cache_creation_input_tokens", 0),
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")].strip()
        return raw

    def generate_trade_brief(
        self,
        nifty_signal: dict,
        banknifty_signal: dict,
        global_cues: dict,
        fii_dii: dict,
        participant_oi: dict,
        nifty_tech: dict,
        banknifty_tech: dict,
        stock_signals: list[dict],
        stock_technicals: list[dict],
        block_deals: list[dict],
        news: dict,
        analysis_date: str | None = None,
        yesterday_memory: dict | None = None,
        fno_universe_technicals: list[dict] | None = None,
    ) -> dict:
        """8 AM morning analysis — returns the full brief dict."""
        if analysis_date is None:
            analysis_date = date.today().strftime("%d-%b-%Y (%A)")

        prompt = build_morning_prompt(
            analysis_date, nifty_signal, banknifty_signal, global_cues,
            fii_dii, participant_oi, nifty_tech, banknifty_tech,
            stock_signals, stock_technicals, block_deals, news,
            yesterday_memory=yesterday_memory,
            fno_universe_technicals=fno_universe_technicals,
        )
        logger.info("Sending morning data to Claude API (20 stock universe included)...")
        raw = self._call(MORNING_SYSTEM_PROMPT, prompt, max_tokens=6000)

        try:
            brief = json.loads(raw)
            if not isinstance(brief, dict):
                raise ValueError("Expected a JSON object")
            logger.info(
                "Claude morning brief: %d index trades, %d stock trades, %d filtered out",
                len(brief.get("trades", [])),
                len(brief.get("stock_trades", [])),
                len(brief.get("trades_filtered_out", [])),
            )
            return brief
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse morning brief JSON: %s", exc)
            logger.error("Raw (first 600 chars): %s", raw[:600])
            return {"error": str(exc), "trades": [], "trades_filtered_out": []}

    def generate_preopen_check(
        self,
        morning_trades: list[dict],
        vix_current: float | None,
        vix_morning: float | None,
        global_cues_fresh: dict,
        news_fresh: dict,
    ) -> dict:
        """9 AM pre-open check — assigns GO/WAIT/SKIP to each morning trade."""
        prompt = build_preopen_prompt(
            morning_trades, vix_current, vix_morning, global_cues_fresh, news_fresh
        )
        logger.info("Sending pre-open data to Claude API...")
        raw = self._call(PREOPEN_SYSTEM_PROMPT, prompt, max_tokens=1024)

        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            logger.info(
                "Pre-open review: %d trades assessed",
                len(result.get("trades_review", [])),
            )
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse pre-open JSON: %s", exc)
            logger.error("Raw (first 400 chars): %s", raw[:400])
            return {"error": str(exc), "trades_review": []}

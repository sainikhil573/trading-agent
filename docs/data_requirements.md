# Data Requirements — AI F&O Trading Agent

This document is the authoritative record of every data input the system uses,
what breaks when each is missing, and what CSV schema to use when providing data locally.

---

## Summary Table

| Layer | Required | Available Now | Source | Weight in Confidence |
|-------|----------|--------------|--------|----------------------|
| NSE Option Chain (OI / PCR / Max Pain) | Yes | ✓ | jugaad-data | 25% (derivatives) |
| India VIX | Yes | ✓ | jugaad-data | Stop-loss sizing |
| FII / DII Cash Market | Yes | ⚠ Unreliable | NSE API | 20% (institutional) |
| Participant OI (FII/DII/PRO/Client) | Yes | ⚠ Prior day only | NSE archives CSV | ~10% (smart money) |
| Global Cues (S&P, Nikkei, USD/INR…) | No | ✓ | yfinance | 15% (global) |
| Technical Indicators (EMA/RSI/MACD) | No | ✓ | yfinance | 20% (technical) |
| Market News / Sentiment | No | ✓ | RSS (ET, MC) | 10% (sentiment) |
| Intraday Candles (5m / 15m / 30m) | No | ✗ Not wired | Angel One / Kite / Local CSV | Outcome resolution only |
| GIFT Nifty Futures | No | ✗ No source | No API available | Pre-market gap proxy |

---

## Layer Details

### 1. NSE Option Chain

**What it provides:** OI per strike and option type (CE/PE), PCR, max pain, ATM strike,
IV per strike, LTP, bid/ask.

**Why it matters:**
- PCR ≥ 1.3 triggers a CONFLICTING_SIGNALS gate (blocks PUT trades — contrarian bullish)
- PCR ≤ 0.6 triggers a CONFLICTING_SIGNALS gate (blocks CALL trades — contrarian bearish)
- Max pain gap > 1000 pts opposing trade direction triggers a CONFLICTING_SIGNALS gate
- OI buildup identifies where institutions are writing options (resistance/support)

**What breaks when missing:**
- All derivative gates cannot run — confidence scores are unvalidated
- PCR and max pain analysis is absent (25% of Claude's confidence weight)
- The system will produce briefs but gate logic degrades to pure confidence threshold

**Source:** `jugaad_data.nse.NSELive.index_option_chain("NIFTY")` / `index_option_chain("BANKNIFTY")`

**No CSV fallback** — this is a live market feed; historical option chains are not used.

---

### 2. India VIX

**What it provides:** Current India VIX value (fear index).

**Why it matters:**
- VIX < 17 → SL = 40% of entry premium
- VIX 17–22 → SL = 50% of entry premium
- VIX > 22 → SL = 60% of entry premium
- Claude uses VIX zone to adjust position sizing recommendations

**What breaks when missing:**
- SL defaults to 50% (elevated zone assumed) — position sizing less precise
- Claude cannot accurately size positions

**Source:** `NSELive.all_indices()` — loops until INDIA VIX entry is found.

---

### 3. FII / DII Cash Market

**What it provides:** Net buying/selling in cash equities by FIIs and DIIs (in crores).

**Why it matters:**
- Large FII net buy (> +2000 Cr) → institutional bullish pressure → supports CALL
- Large FII net sell (< -2000 Cr) → institutional selling → supports PUT
- Confirmation of directional bias from 20% weight layer

**What breaks when missing:**
- `FII_DII_DATA_ZERO` flag set, confidence penalty of −0.5 applied
- If BOTH this and Participant OI are missing → DATA_INSUFFICIENT gate → trade blocked
- Claude receives zero flows, which it interprets as no institutional conviction

**Reliability issue:** NSE's `/api/fiidiiTradeReact` often returns 0 pre-market and can return
stale data. The field `fii_dii.date` tells you which date the data covers.

**CSV schema (for manual ingestion if needed):**
```
date,category,buy_value,sell_value,net_value
2026-05-20,FII,45230.50,38120.25,7110.25
2026-05-20,DII,22450.00,19800.75,2649.25
```

Fields:
- `date`: YYYY-MM-DD
- `category`: `FII` | `DII`
- `buy_value`: gross purchase in crores (INR)
- `sell_value`: gross sale in crores (INR)
- `net_value`: buy_value − sell_value (positive = net buy)

---

### 4. Participant-wise OI (Derivatives)

**What it provides:** FII, DII, PRO, and Client futures index positions (long/short counts).
Derived metric: `fii_long_pct` — FII future longs as percentage of total FII futures.

**Why it matters:**
- FII long % > 60% → institutional bullish bias → supports CALL
- FII long % < 40% → institutional bearish bias → supports PUT
- Provides smart money direction that the cash FII/DII data alone cannot show

**What breaks when missing:**
- `PARTICIPANT_OI_MISSING` flag set, confidence penalty of −0.5 applied
- Claude marks smart money direction as "unavailable"
- Combined with FII/DII cash zero → DATA_INSUFFICIENT → trade blocked

**Availability constraint:** NSE archives only publish the prior trading day's CSV.
The file for today will not be available until ~18:00 IST. Morning runs will always see T−1 data.

**CSV schema (for manual ingestion):**
```
date,participant,long_call,short_call,long_put,short_put,long_future,short_future
2026-05-19,FII,2834521,1923445,3102934,1845672,1234567,987654
2026-05-19,DII,123456,234567,345678,456789,87654,98765
2026-05-19,PRO,987654,876543,765432,654321,543210,432109
2026-05-19,Client,4567890,5678901,6789012,7890123,8901234,9012345
```

Fields:
- `date`: YYYY-MM-DD (must be the trading day the data covers)
- `participant`: `FII` | `DII` | `PRO` | `Client`
- `long_call`: call option long positions (contracts)
- `short_call`: call option short positions
- `long_put`: put option long positions
- `short_put`: put option short positions
- `long_future`: index futures long positions
- `short_future`: index futures short positions

---

### 5. Global Cues

**What it provides:** Overnight price changes in S&P 500 futures, Dow futures, Nasdaq futures,
Nikkei 225, Hang Seng, Crude Oil WTI, USD/INR, and Gold futures.

**Why it matters:**
- Strong US/Asia overnight gains → positive global bias → supports bullish India open
- Crude Oil WTI rise is BEARISH for India (import cost) — inverted in signal builder
- USD/INR rise is BEARISH for India equities — inverted in signal builder
- Global bias feeds into 15% weight of Claude's confidence

**What breaks when missing:** Analysis proceeds with reduced global context; Claude scores
global layer lower.

**Source:** yfinance (ES=F, YM=F, NQ=F, ^N225, ^HSI, CL=F, USDINR=X, GC=F)

**Note:** GIFT Nifty (the primary NSE overnight indicator) is NOT available on Yahoo Finance.
SGX/GIFT Nifty requires a dedicated API or subscription feed.

---

### 6. Technical Indicators (Daily OHLC)

**What it provides:** EMA-20, EMA-50, EMA-200, RSI-14, MACD (12/26/9), Bollinger Bands (20),
week high/low, and 20-day average volume — computed from 60 days of daily bars.

**Why it matters:**
- EMA trend (price vs EMA-20 vs EMA-50) confirms directional bias
- RSI overbought (>70) or oversold (<30) adds contrarian signals
- MACD crossover signals short-term momentum shift
- Volume vs 20-day average confirms or rejects signal strength

**What breaks when missing:** Technical layer (20% weight) is absent; Claude may still recommend
trades but with lower technical confidence scores.

**Source:** `yfinance.Ticker("^NSEI").history(period="60d", interval="1d")`

---

### 7. Market News / Sentiment

**What it provides:** Up to 20 deduplicated headlines from Economic Times Markets and Moneycontrol.

**Why it matters:**
- Claude classifies sentiment from headlines as part of Layer 6 (10% weight)
- RBI policy, budget, geopolitical events that move markets are captured here
- Provides the "events layer" that options metrics cannot show

**What breaks when missing:** Sentiment layer is absent; Claude scores news_sentiment and
event_risk lower, reducing overall confidence.

**Source:** RSS feeds — ET (`economictimes.indiatimes.com/rssfeedstopstories.cms`) and
Moneycontrol (`moneycontrol.com/rss/marketsnews.xml`).

---

### 8. Intraday Candles (5m / 15m / 30m)

**What it provides:** Time-stamped OHLCV data at sub-daily resolution for NIFTY and BANKNIFTY.

**Why it matters (critical for outcome tracking):**
When daily OHLC shows both the day's high AND low touched the SL and target levels,
we cannot determine from OHLC alone whether SL or the target was hit first.
Intraday candles provide the chronological sequence needed to resolve the true outcome.

Without intraday data → `OUTCOME_UNKNOWN` (excluded from win rate, not falsely credited).
With intraday data → outcome is resolved from actual candle sequence.

**What breaks when missing:**
- Ambiguous trades (SL + target both in OHLC range, wrong direction) → `OUTCOME_UNKNOWN`
- Win rate cannot be computed accurately for ambiguous sessions
- Cannot confirm entry trigger (first 30-min candle) was met

**NOT used for morning brief generation** — only used at post-market tracking (3:30 PM IST).

**CSV schema:**
```
symbol,datetime,open,high,low,close,volume,timeframe
NIFTY,2026-05-20 09:15:00,22500.0,22550.0,22480.0,22520.0,1200000,5m
NIFTY,2026-05-20 09:20:00,22520.0,22530.0,22470.0,22490.0,980000,5m
BANKNIFTY,2026-05-20 09:15:00,53000.0,53100.0,52900.0,53050.0,800000,5m
```

File naming: `data/intraday/{SYMBOL}_{YYYYMMDD}_{timeframe}.csv`
Example: `data/intraday/NIFTY_20260520_5m.csv`

Fields:
- `symbol`: `NIFTY` | `BANKNIFTY`
- `datetime`: `YYYY-MM-DD HH:MM:SS` (IST, tz-naive)
- `open`, `high`, `low`, `close`: index spot price (not premium)
- `volume`: number of contracts or lots traded in this candle
- `timeframe`: `5m` | `15m` | `30m`

**Future provider options:**

| Provider | API | Notes |
|----------|-----|-------|
| Angel One SmartAPI | `smartapi-python` | Free tier available; requires client credentials |
| Zerodha Kite Connect | `kiteconnect` | Paid subscription; excellent data quality |
| NSE Historical | NSE Bhavcopy | Only EOD daily; no intraday granularity |
| Local CSV (current) | File-based | Manual export from broker terminal |

Stub classes `AngelOneIntradayProvider` and `KiteIntradayProvider` exist in
`src/fetchers/intraday_provider.py` — wire credentials and implement `get_candles()`.

---

### 9. GIFT Nifty Futures

**What it provides:** Overnight GIFT Nifty futures price — the primary indicator of
how Nifty will open the next trading session.

**Why it matters:**
- A large positive GIFT Nifty gap predicts a bullish Nifty open
- Used by most professional India traders as the first morning signal
- Currently replaced by S&P 500 futures as a rough proxy

**What breaks when missing:**
- Opening gap estimate is less precise (S&P 500 correlation with Nifty is ~0.6, not 1.0)
- Pre-market directional conviction is reduced

**Source:** Not available on Yahoo Finance. Requires dedicated data provider:
- NSE's own portal (web scraping, unreliable)
- Bloomberg / Refinitiv (paid subscription)
- Interactive Brokers API (if subscribed to Indian data)

---

## Data Availability Gates

The trade gate system (`src/analyzers/trade_gates.py`) enforces hard blocks based on
data availability:

| Condition | Gate Status |
|-----------|-------------|
| FII/DII + Participant OI both missing | `DATA_INSUFFICIENT` — trade blocked |
| PCR ≥ 1.3 + PUT signal | `CONFLICTING_SIGNALS` — trade blocked |
| PCR ≤ 0.6 + CALL signal | `CONFLICTING_SIGNALS` — trade blocked |
| Max pain gap > 1000 pts opposing direction | `CONFLICTING_SIGNALS` — trade blocked |
| Only FII/DII missing | −0.5 confidence penalty |
| Only Participant OI missing | −0.5 confidence penalty |
| Volume confirmation < 5/10 | Confidence capped at 6.8 |
| Effective confidence < 7.0 after adjustments | `NO_TRADE` |

---

## Outcome Tracking Truth Table

| OHLC extremes hit? | Direction correct? | Intraday available? | Outcome |
|--------------------|--------------------|--------------------:|---------|
| T1 only | Yes | Either | `TARGET_1_HIT` |
| T2 only | Yes | Either | `TARGET_2_HIT` |
| SL only | Either | Either | `SL_HIT` |
| SL + T1 | Yes | No | `OUTCOME_UNKNOWN` |
| SL + T1 | Yes | Yes, T1 first | `TARGET_1_HIT` |
| SL + T1 | Yes | Yes, SL first | `SL_HIT` |
| SL + T1 | No | Either | `OUTCOME_UNKNOWN` |
| Neither | Yes | Either | `DIRECTION_RIGHT` |
| Neither | No | Either | `DIRECTION_WRONG` |

`OUTCOME_UNKNOWN` is **never counted as a win** and is excluded from direction accuracy %.

---

## Accepted CSV File Locations

| Data Type | File Path |
|-----------|-----------|
| Intraday candles | `data/intraday/{SYMBOL}_{YYYYMMDD}_{timeframe}.csv` |
| (Future) FII/DII override | `data/intraday/fii_dii_{YYYYMMDD}.csv` |
| (Future) Participant OI override | `data/intraday/participant_oi_{YYYYMMDD}.csv` |

The `data/intraday/` directory is tracked in git (the `.gitkeep` is committed).
Individual CSV files with real market data should be added locally and not committed.

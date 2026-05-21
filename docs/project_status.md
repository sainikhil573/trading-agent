# Project Status — Indian F&O AI Trading Agent

> Keep this file concise. Update Current Stage, Completed Phases, Known Limitations,
> Next Recommended Phases, and Change Log after every meaningful change.

---

## Current Project Goal

Build a Python + Streamlit system for NSE F&O pre-market analysis, trade filtering,
dashboard display, and post-market accuracy tracking using Claude API.

---

## Current Stage

**Post-gate output consistency fixed (feature/post-gate-output-consistency).**
All output fields now accurately reflect post-gate state: `trading_recommended` is
updated after gates, stock trades go through data-quality gates, `max_trades_recommended`
is enforced, `watchlist_only` is separated from actionable trades, FII/DII zero is shown
as "Unavailable" not neutral, and `post_gate_summary` replaces pre-gate Claude summary
when trades are blocked. Dashboard shows a truthful post-gate banner and blocked stock trades.

---

## Completed Phases

| Phase | Description | Commit |
|-------|-------------|--------|
| Phase 1 | NSE fetcher, global cues, signal analyzer, Claude API integration, scheduler | f1655e6 |
| Phase 2 | 6-layer analysis, dual IST runs, holiday calendar, technicals, news | 2659f32 |
| Phase 3 | Streamlit dashboard (military style), post-market Claude analysis, yesterday memory loop | af8c8ab / 74bbc42 |
| Fix/PR1 | Dashboard readability fixes, CLAUDE.md project instructions | 97f6029 |
| PR2 | Analysis root-cause audit — signal validation hardening, data quality gates, data_requirements.md | e285bf5–10b61dc |
| PR3 | Intraday CSV ingestion, validation, dashboard upload UI | 32051de |
| PR4 | Broker data provider integration (Angel One + Kite stubs, normalisation, broker_config) | e6cff2f |
| PR5 | Post-gate output consistency — trading_recommended, stock gates, max_trades cap, watchlist_only, FII/DII unavailable semantics | (this branch) |

---

## Completed Branches / Features

| Branch | Feature |
|--------|---------|
| `fix/claude-memory-and-dashboard-updates` | Dashboard polish, CLAUDE.md |
| `feature/analysis-root-cause-audit` | Trade gates, data quality gates, FII/DII reliability docs |
| `feature/intraday-csv-upload-and-loader` | CSV intraday ingestion, schema validation, dashboard uploader |
| `feature/broker-data-provider-integration` | AngelOne/Kite stubs, normalisation layer, broker_config, docs |
| `feature/post-gate-output-consistency` | Post-gate output truthfulness: stock gates, trading_recommended flip, watchlist_only, FII/DII unavailable display |

---

## Current Architecture

```
main.py
  ├── --morning     → src/orchestrator.run_morning_analysis()
  ├── --preopen     → src/orchestrator.run_preopen_analysis()
  ├── --candle      → src/orchestrator.run_candle_check()
  ├── --postmarket  → src/orchestrator.run_postmarket_analysis()
  ├── --schedule    → blocks, runs all four at IST times
  └── --dashboard   → streamlit run src/dashboard/app.py

src/fetchers/
  nse_fetcher.py          — jugaad-data: option chain, VIX, FII/DII, participant OI
  global_fetcher.py       — yfinance: S&P/Nikkei/Crude/USD-INR/Gold futures
  technical_fetcher.py    — yfinance: EMA/RSI/MACD/BB for NIFTY, BANKNIFTY, F&O stocks
  news_fetcher.py         — RSS: Economic Times + Moneycontrol headlines
  intraday_loader.py      — reads CSVs from data/intraday/
  intraday_validator.py   — validates CSV schema and data quality
  intraday_provider.py    — provider interface: AngelOne stub, Kite stub, LocalCSV, Unavailable
  broker_config.py        — reads BROKER_PROVIDER env var, selects provider
  data_availability.py    — derives data quality flags

src/analyzers/
  signal_analyzer.py      — builds NIFTY/BANKNIFTY/stock signal dicts from raw fetcher data
  claude_analyzer.py      — calls Claude API for morning brief + pre-open check
  trade_gates.py          — deterministic post-process: DATA_INSUFFICIENT, CONFLICTING_SIGNALS, NO_TRADE
  evaluation.py           — resolves trade outcomes (SL/T1/T2/UNKNOWN) with or without intraday
  post_market_analyzer.py — calls Claude API for post-market grade

src/utils/
  logger.py               — structured file + console logging
  trading_calendar.py     — NSE holiday calendar, is_trading_day()

src/dashboard/app.py      — Streamlit UI: morning brief, pre-open, post-market, accuracy tracker,
                            intraday CSV upload, provider status
```

---

## Current Data Flow

```
8:00 AM IST  — morning analysis
  NSEFetcher (jugaad-data)   → option chain, VIX, FII/DII, participant OI
  fetch_global_cues()        → yfinance overnight levels
  fetch_index_technicals()   → yfinance 60-day daily bars → EMA/RSI/MACD
  fetch_market_headlines()   → RSS headlines
  build_market_signal()      → combined signal dict per index
  ClaudeAnalyzer             → claude-3-5-sonnet → trade brief JSON
  apply_trade_gates()        → deterministic blocks/flags
  → data/processed/trade_brief_morning_YYYY-MM-DD.json

9:00 AM IST  — pre-open check
  Re-fetch VIX, global cues, news
  ClaudeAnalyzer             → GO/WAIT/SKIP per trade
  → data/processed/trade_brief_preopen_YYYY-MM-DD.json

9:15 AM IST  — candle check
  Re-fetch VIX + global (no Claude)
  → data/processed/candle_check_YYYY-MM-DD.json

3:30 PM IST  — post-market
  yfinance daily OHLC        → outcome resolution (SL/T1/T2/UNKNOWN)
  intraday_provider          → candles for chronological resolution (if available)
  PostMarketAnalyzer         → Claude grading
  → data/processed/post_market_YYYY-MM-DD.json
  → data/processed/prediction_accuracy.json (rolling append)
```

---

## Current Trading / Analysis Flow

1. **6-layer pre-market analysis** — derivatives (PCR, max pain, OI), institutional
   (FII/DII cash + participant futures), global cues, technicals (EMA/RSI/MACD),
   news/sentiment, yesterday's post-market memory.
2. **Confidence scoring** — Claude scores 0–10; gates enforce hard blocks:
   - `DATA_INSUFFICIENT` — both FII/DII and participant OI missing → block
   - `CONFLICTING_SIGNALS` — PCR or max pain opposing trade direction → block
   - `NO_TRADE` — effective confidence < 7.0 after data penalties → block
3. **Pre-open GO/WAIT/SKIP** — Claude re-evaluates with fresh VIX + news at 9:00 AM.
4. **Candle check at 9:15 AM** — entry trigger validation (no Claude call).
5. **Post-market grading** — outcome resolved from daily OHLC or intraday candles;
   Claude grades each trade; appended to rolling `prediction_accuracy.json`.
6. **F&O universe** — fixed 20-stock list in orchestrator (`RELIANCE`, `TCS`, `INFY`, …).

---

## Current Data Sources

| Layer | Source | Status |
|-------|--------|--------|
| NSE Option Chain / VIX / FII / OI | jugaad-data (`NSELive`) | Working |
| Global cues (S&P, Nikkei, Crude, USD/INR) | yfinance | Working |
| Technical indicators (EMA/RSI/MACD) | yfinance 60-day daily | Working |
| Market news / sentiment | RSS (ET + Moneycontrol) | Working |
| Intraday candles — CSV upload | `data/intraday/*.csv` | Working |
| Intraday candles — Angel One | SmartAPI stub | Not wired (stub only) |
| Intraday candles — Zerodha Kite | KiteConnect stub | Not wired (stub only) |
| GIFT Nifty futures | None | Missing — no free source |
| FII/DII (pre-market reliability) | NSE API | Unreliable pre-market |

---

## Known Limitations

1. **Stock gate always penalizes pre-market** — FII/DII is always 0 before NSE publishes
   (~15:30 IST), so the -0.5 + -0.5 = -1.0 penalty gates out all stock trades with
   confidence < 8.0 during morning analysis. This is honest but aggressive. Resolved by
   Phase 5 (live broker intraday data) or running analysis post-market.
2. **Intraday brokers not wired** — `AngelOneIntradayProvider._fetch()` and
   `KiteIntradayProvider._fetch()` return empty DataFrames; symbol→token lookup
   not implemented. Use CSV upload until credentials + wiring are done.
2. **GIFT Nifty unavailable** — S&P 500 futures used as proxy (correlation ~0.6).
3. **FII/DII pre-market data unreliable** — NSE API often returns zeros before market
   open; prior-day participant OI is always T−1 (published ~18:00 IST).
4. **OUTCOME_UNKNOWN for ambiguous trades** — when daily OHLC shows both SL and target
   were reached and intraday candles are absent, outcome is unknown (not counted as win).
5. **No rate-limit or retry logic** on broker API stubs.
6. **Kite access token rotates daily** — requires manual refresh each morning.
7. **No backtesting pipeline** — accuracy is tracked forward only from live runs.
8. **No position sizing in INR** — Claude provides relative sizing (small/medium/large)
   not absolute lot/quantity recommendations.

---

## Missing Data / Missing Providers

| Gap | Impact | Suggested Provider |
|-----|--------|--------------------|
| GIFT Nifty live feed | Opening gap estimate is imprecise | Bloomberg, IBKR, NSE portal |
| Angel One live wiring | Intraday outcome resolution | Complete `_fetch()` + token table |
| Kite live wiring | Same as above | Complete `_fetch()` + instrument CSV |
| FII/DII intraday accuracy | Confidence penalty common pre-market | Wait for 3:30 PM publish or use prior-day |
| Backtesting data | Cannot validate signals historically | NSE Bhavcopy + option chain archives |

---

## Next Recommended Phases

| Priority | Phase | Description |
|----------|-------|-------------|
| High | **Phase 5 — Live intraday wiring** | Wire Angel One or Kite `_fetch()`, add symbol→token lookup, enable live outcome resolution |
| High | **Phase 6 — GIFT Nifty** | Add dedicated GIFT Nifty source (IBKR or broker API) |
| Medium | **Phase 7 — Backtesting** | Build a replay pipeline using NSE Bhavcopy + option chain archives |
| Medium | **Phase 8 — Alerts** | Telegram/email alerts for GO/WAIT/SKIP decisions at 9:00 AM |
| Low | **Phase 9 — Multi-expiry** | Track weekly + monthly expiry trades separately in accuracy log |
| Low | **Phase 10 — Auto-login (Kite)** | Automate Kite daily access-token refresh via login flow |

---

## V1 Completion Checklist

- [x] NSE option chain + VIX + FII/DII fetcher
- [x] Global cues fetcher (yfinance)
- [x] Technical indicators (EMA/RSI/MACD)
- [x] News/sentiment fetcher (RSS)
- [x] 6-layer signal builder
- [x] Claude morning brief (pre-market)
- [x] Claude pre-open GO/WAIT/SKIP
- [x] 9:15 AM candle check
- [x] Post-market outcome tracker (OHLC-based)
- [x] Post-market Claude grading
- [x] Rolling prediction_accuracy.json
- [x] Streamlit dashboard
- [x] Trade gate system (DATA_INSUFFICIENT, CONFLICTING_SIGNALS, NO_TRADE)
- [x] Intraday CSV upload + outcome resolution
- [x] Broker provider interface (Angel One + Kite stubs)
- [ ] Live intraday API wiring (Angel One or Kite)
- [ ] GIFT Nifty feed
- [ ] Backtesting pipeline

---

## Future Claude Code Instructions

- Read this file before starting any task.
- Do not re-audit the full repository unless explicitly asked.
- Inspect only files relevant to the current task.
- After every meaningful feature or change, update: Current Stage, Completed Phases,
  Known Limitations, Next Recommended Phases, and Change Log.
- Keep this file concise — do not expand into a full report.

---

## Change Log

> Append-only. Add one entry per meaningful feature/branch.

---

### 2026-05-17 | Phase 1 + 2 bootstrap | `f1655e6` / `2659f32`

- **Files changed:** `main.py`, `src/fetchers/nse_fetcher.py`,
  `src/fetchers/global_fetcher.py`, `src/fetchers/technical_fetcher.py`,
  `src/fetchers/news_fetcher.py`, `src/analyzers/signal_analyzer.py`,
  `src/analyzers/claude_analyzer.py`, `src/orchestrator.py`,
  `src/utils/logger.py`, `src/utils/trading_calendar.py`
- **Feature added:** NSE option chain + VIX + FII/DII fetcher; global cues (yfinance);
  6-layer signal builder; Claude morning brief; dual IST runs (8 AM + 9 AM);
  holiday calendar; technicals (EMA/RSI/MACD); RSS news; scheduler.
- **Tests/checks:** Manual run — morning brief JSON produced.
- **Remaining limitations:** No dashboard, no post-market tracker.
- **Next step:** Add Streamlit dashboard.

---

### 2026-05-18 | Phase 3 dashboard + post-market | `af8c8ab` / `74bbc42`

- **Files changed:** `src/dashboard/app.py`, `src/analyzers/post_market_analyzer.py`,
  `src/orchestrator.py` (postmarket functions added), `main.py` (`--dashboard` flag).
- **Feature added:** Streamlit military-style dashboard with auto-refresh (60s);
  post-market Claude grading; yesterday's memory loop (looks back 7 days).
- **Tests/checks:** Dashboard launched and verified visually.
- **Remaining limitations:** No trade gate system; no intraday data.
- **Next step:** Add trade gates and data quality validation.

---

### 2026-05-20 | PR1 — Dashboard fixes + CLAUDE.md | `97f6029`

- **Branch:** `fix/claude-memory-and-dashboard-updates`
- **Files changed:** `src/dashboard/app.py`, `CLAUDE.md`
- **Feature added:** Dashboard readability improvements; Claude Code project instructions.
- **Tests/checks:** Visual dashboard check.
- **Remaining limitations:** Analysis failures not yet diagnosed.
- **Next step:** Root-cause audit of analysis failures.

---

### 2026-05-20 | PR2 — Analysis root-cause audit | `e285bf5` / `8c86c94` / `10b61dc`

- **Branch:** `feature/analysis-root-cause-audit`
- **Files changed:** `src/analyzers/signal_analyzer.py`, `src/analyzers/trade_gates.py`
  (new), `src/fetchers/data_availability.py` (new), `src/analyzers/evaluation.py`,
  `docs/data_requirements.md` (new).
- **Feature added:** Deterministic trade gate system (DATA_INSUFFICIENT,
  CONFLICTING_SIGNALS, NO_TRADE); data quality flags; FII/DII reliability documentation;
  full data requirements doc with CSV schemas.
- **Tests/checks:** Gate logic unit-tested; audit report generated.
- **Remaining limitations:** Intraday candles not yet ingestible.
- **Next step:** Add intraday CSV ingestion.

---

### 2026-05-20 | PR3 — Intraday CSV ingestion | `32051de`

- **Branch:** `feature/intraday-csv-upload-and-loader`
- **Files changed:** `src/fetchers/intraday_loader.py` (new),
  `src/fetchers/intraday_validator.py` (new), `src/dashboard/app.py`
  (upload UI added), `data/intraday/schema_example.csv` (new),
  `data/intraday/.gitkeep`.
- **Feature added:** Local CSV intraday ingestion; schema validation; dashboard
  upload widget; outcome resolution using candle chronological sequence.
- **Tests/checks:** CSV upload tested via dashboard; validation errors verified.
- **Remaining limitations:** Live broker APIs not yet integrated.
- **Next step:** Add broker provider abstraction layer.

---

### 2026-05-20 | PR4 — Broker data provider integration | `e6cff2f`

- **Branch:** `feature/broker-data-provider-integration`
- **Files changed:** `src/fetchers/intraday_provider.py` (new),
  `src/fetchers/broker_config.py` (new), `src/orchestrator.py`
  (provider wired into post-market tracker), `src/dashboard/app.py`
  (provider status line + credential warning), `docs/broker_data_providers.md` (new).
- **Feature added:** Provider interface (ABC); AngelOne + Kite stubs with response
  normalisation; LocalCSV provider; UnavailableProvider fallback;
  `BROKER_PROVIDER` env var selection; dashboard provider status display.
- **Tests/checks:** Normalisation functions tested without live credentials;
  fallback chain verified.
- **Remaining limitations:** `_fetch()` not wired in AngelOne/Kite — returns empty
  DataFrame; symbol→token lookup not implemented; Kite token rotates daily.
- **Next step:** Phase 5 — wire `_fetch()` + instrument token tables.

---

### 2026-05-21 | docs — Project status tracker | `baa69d9`

- **Branch:** `docs/project-status-tracker`
- **Files changed:** `docs/project_status.md` (new), `CLAUDE.md` (updated).
- **Feature added:** Compact project status tracker for future Claude Code sessions;
  CLAUDE.md instruction to read status file before any task.
- **Tests/checks:** Docs only — no tests required.
- **Remaining limitations:** None for this change.
- **Next step:** Fix post-gate output consistency.

---

### 2026-05-21 | PR5 — Post-gate output consistency | `feature/post-gate-output-consistency`

- **Branch:** `feature/post-gate-output-consistency`
- **Files changed:** `src/analyzers/trade_gates.py` (gate_stock_trade added,
  apply_trade_gates extended), `src/analyzers/claude_analyzer.py` (FII/DII
  unavailable prompt display), `src/dashboard/app.py` (post-gate banner, FII
  unavailable metric, blocked stock trades, watchlist section, post_gate_summary),
  `tests/test_trade_gates.py` (17 new tests in TestGateStockTrade +
  TestPostGateConsistency), `docs/project_status.md` (this entry).
- **Feature added:**
  - `gate_stock_trade()`: simplified gate for stock F&O trades (data penalty + confidence floor).
  - `apply_trade_gates()` now gates stock trades, enforces `max_trades_recommended` cap,
    separates `watchlist_only`, adds `post_gate_summary`, updates `trading_recommended` to
    `False` when `total_actionable == 0`, adds `final_recommendation_status` to `_gate_summary`.
  - `post_gate_summary` field: truthful one-line status replacing pre-gate Claude summary
    when trades are blocked.
  - FII/DII prompt shows "UNAVAILABLE — pre-market zero" instead of "+0.0 Cr".
  - Dashboard: post-gate status banner (green/amber/red), FII metric shows "Unavailable",
    blocked stock trades visible in BLOCKED section, `watchlist_only` section, morning
    summary labelled "[CLAUDE ANALYSIS — PRE-GATE]" when overridden.
- **Tests/checks:** 204 passed, 2 skipped (pre-existing; require live broker packages).
  All 49 trade gate tests pass including 17 new post-gate consistency tests.
- **Remaining limitations:** Stock gate's -1.0 pre-market penalty blocks all stocks
  with confidence < 8.0 (expected — FII/DII is always 0 before 15:30 IST). Resolved
  with Phase 5 live intraday data or post-market analysis run.
- **Next step:** Phase 5 — wire Angel One or Kite `_fetch()` + instrument token tables.

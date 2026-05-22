# Project Status — Indian F&O AI Trading Agent

> Keep this file concise. Update Current Stage, Completed Phases, Known Limitations,
> Next Recommended Phases, and Change Log after every meaningful change.

---

## Current Project Goal

Build a Python + Streamlit system for NSE F&O pre-market analysis, trade filtering,
dashboard display, and post-market accuracy tracking using Claude API.

---

## Current Stage

**Phase: S3 Live Paper Collection (branch: feature/s3-validation-and-next-steps, 2026-05-22)**

S2 validation dashboard: COMPLETE (330 tests → 356 tests after S3).

### S2 Verification Results (2026-05-22)
- V1: ALL PASS — fo_universe.json tiers ✓, journal WIPRO CALL+confidence_tier ✓, health dir ✓, fetch_opening_gap source ✓
- V2: 330/330 tests PASS, 2 skipped
- V3: Morning pipeline ran — health DEGRADED (FII/DII+ParticipantOI unavailable, no cache yet)
- V4: 30-day backtest = 45.4% (baseline 44.9% ✓), 60-day = 50.2%, no regression

### 60-day vs 30-day Backtest Comparison
- 30-day overall: 45.4% | 60-day overall: 50.2%
- 12 of 19 symbols agree on tier across both timeframes
- fo_universe.json tier changes (both timeframes agree): HDFCBANK WATCHLIST→SECONDARY, TATASTEEL SECONDARY→WATCHLIST
- Disagreements (not changed): KOTAKBANK, BAJFINANCE, WIPRO, TCS, SBIN, AXISBANK, MARUTI
- Extended results: `data/backtesting/results_60d.json`

### Memory Loop Status
- Confirmed working: looks back 7 days for post_market files
- Current: "No memory available — fresh start" (no post_market files yet — first run)
- Will start loading after first 3:30 PM post-market run

### Paper Trades Collected
- Current count: 2 (WIPRO CALL [OPEN], HINDALCO CALL [OPEN])
- Next milestone: 10 graded trades (need 8 more closed + graded)

### New S3 Features
- Signal quality analytics: `src/analytics/signal_quality.py` — time_to_sl, time_to_t1, best/worst PnL, outcome_quality
- Premarket checklist: `src/validators/premarket_checklist.py` — VIX/health/trading-day/layer-freshness gates
- Telegram formatter: `src/alerts/telegram_formatter.py` — GO/NO_TRADE/DATA_DEGRADED formatted (not sent yet)
- Dashboard: signal quality expandable rows, GIFT Nifty source label, FII prev-day date label
- Telegram: formatter built, sending pending 10 clean paper trades
- Pending alerts: `data/alerts/pending_alerts_YYYY-MM-DD.json`

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
| PR5 | Post-gate output consistency — trading_recommended, stock gates, max_trades cap, watchlist_only, FII/DII unavailable semantics | ea094c5 |
| PR6 | Instrument master token mapping — InstrumentMaster, lookup helpers, provider stub update, dashboard status | (this branch) |

---

## Completed Branches / Features

| Branch | Feature |
|--------|---------|
| `fix/claude-memory-and-dashboard-updates` | Dashboard polish, CLAUDE.md |
| `feature/analysis-root-cause-audit` | Trade gates, data quality gates, FII/DII reliability docs |
| `feature/intraday-csv-upload-and-loader` | CSV intraday ingestion, schema validation, dashboard uploader |
| `feature/broker-data-provider-integration` | AngelOne/Kite stubs, normalisation layer, broker_config, docs |
| `feature/post-gate-output-consistency` | Post-gate output truthfulness: stock gates, trading_recommended flip, watchlist_only, FII/DII unavailable display |
| `feature/instrument-master-token-mapping` | Instrument master / token mapping: InstrumentMaster, lookup helpers, broker stub update, dashboard status |

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
  broker_config.py        — reads BROKER_PROVIDER env var, selects provider; passes InstrumentMaster to providers
  data_availability.py    — derives data quality flags; reports instrument master status
  instrument_master.py    — loads data/instruments/instrument_master.csv; symbol→token lookup for Kite/Angel One

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
| Intraday candles — Angel One | SmartAPI stub | Token lookup wired; live fetch not yet implemented |
| Intraday candles — Zerodha Kite | KiteConnect stub | Token lookup wired; live fetch not yet implemented |
| Instrument token master | Local CSV `data/instruments/instrument_master.csv` | Schema defined; user must supply real tokens |
| GIFT Nifty futures | None | Missing — no free source |
| FII/DII (pre-market reliability) | NSE API | Unreliable pre-market |

---

## Known Limitations

1. **Instrument master file not supplied** — `data/instruments/instrument_master.csv` is gitignored
   and must be downloaded from the broker. Until present, broker providers return empty candles
   with a diagnostic log. The schema_example.csv shows the required column layout.
2. **Instrument tokens change on expiry** — option contract tokens are week/month specific.
   The master file must be refreshed before each expiry cycle.
3. **Stock gate always penalizes pre-market** — FII/DII is always 0 before NSE publishes
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
| **In Progress** | **S3 — Paper collection** | Accumulate 10 graded paper trades (currently 2 open). Signal quality + checklist + Telegram formatter ready. |
| High | **Telegram bot** | Connect TELEGRAM_BOT_TOKEN after 10 clean paper trades are graded |
| High | **Phase 5 — Live intraday wiring** | Wire Angel One or Kite `_fetch()` with real SmartConnect/KiteConnect calls (token lookup layer is ready) |
| High | **Phase 6 — GIFT Nifty** | Add dedicated GIFT Nifty source (IBKR or broker API) |
| Medium | **Phase 7 — Backtesting** | Build a replay pipeline using NSE Bhavcopy + option chain archives |
| Low | **Phase 9 — Multi-expiry** | Track weekly + monthly expiry trades separately in accuracy log |
| Low | **Phase 10 — Auto-login (Kite)** | Automate Kite daily access-token refresh via login flow |

**Out of scope (current phase):** live execution, ML-based signal enhancement, auto-pruning of F&O universe.

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

---

### 2026-05-21 | PR7 — Instrument master loader and validation | `feature/instrument-master-loader`

- **Branch:** `feature/instrument-master-loader`
- **Files changed:** `src/fetchers/instrument_master.py` (ValidationReport, CoverageReport,
  from_dataframe, validate, coverage_report, normalize_kite_native_df,
  normalize_angelone_native_df, load_best_available_master, source_name property),
  `src/fetchers/data_availability.py` (instrument_master_info extended with source_name,
  validation, coverage fields), `src/dashboard/app.py` (IM COVERAGE, EXPIRED OPTIONS,
  IM VALIDATION dashboard sections), `scripts/check_instrument_master.py` (new CLI tool),
  `data/instruments/schema_example.csv` (column alignment fix),
  `docs/instrument_master.md` (full rewrite with setup options and CLI docs),
  `tests/test_instrument_master.py` (62 new tests, 103 total), `docs/project_status.md` (this entry).
- **Feature added:**
  - `ValidationReport` dataclass + `InstrumentMaster.validate()`: separates structural
    errors (bad option rows, unsupported provider) from data-quality warnings (missing tokens,
    expired contracts). Caps at 20 errors and 20 warnings.
  - `CoverageReport` dataclass + `InstrumentMaster.coverage_report(provider)`: counts by
    instrument_type and exchange; flags per-symbol token readiness for indices and F&O stocks.
  - `Instrument.is_expired()`: checks expiry date against a reference date.
  - `InstrumentMaster.from_dataframe(df, source_name)`: factory for in-memory canonical DataFrames.
  - `normalize_kite_native_df(df)`: maps Kite native format to canonical schema;
    handles INDICES segment → INDEX, FUT+index → FUTIDX, CE/PE → OPTIDX/OPTSTK.
  - `normalize_angelone_native_df(df)`: maps Angel One scrip master to canonical schema;
    handles `symboltoken` alias, `29MAY2026` expiry format, `-EQ` suffix stripping.
  - `load_best_available_master(dir)`: priority 1=canonical CSV, 2=native files auto-normalised, 3=not-loaded.
  - `source_name` property: filename for file-loaded masters, custom string for from_dataframe.
  - `scripts/check_instrument_master.py`: standalone CLI (no Streamlit) with validate/convert
    modes; ANSI coloured output; flags `--file`, `--kite-file`, `--angelone-file`, `--output`,
    `--provider`, `--verbose`, `--no-color`; exit codes 0/1/2.
  - Dashboard extended with `[IM COVERAGE]`, `[EXPIRED OPTIONS]`, `[IM VALIDATION]` sections;
    NOT-loaded state now shows 3 file path options + CLI hint.
  - `data_availability` instrument_master_info now includes `source_name`, `validation`, `coverage`.
- **Tests/checks:** 307 passed, 2 skipped. 62 new tests covering ValidationReport,
  CoverageReport, is_expired, from_dataframe, normalize_kite, normalize_angelone,
  load_best_available_master, source_name, CLI script, data_availability integration.
- **Remaining limitations:** instrument_master.csv must be supplied by user; option tokens
  expire weekly; live `_fetch()` not yet implemented.
- **Next step:** Phase 5 — wire live `_fetch()` in AngelOne/Kite providers.

---

### 2026-05-22 | S3 — Paper validation dashboard | `feature/s2-paper-validation-dashboard`

- **Files changed:** `data/instruments/fo_universe.json` (backtest tiers), `src/orchestrator.py`
  (confidence_tier, atomic journal writes, dry-run postmarket, project_status auto-update),
  `src/fetchers/intraday_csv_parser.py` (new — Zerodha/TradingView parser),
  `src/fetchers/global_fetcher.py` (GIFT Nifty provider with 3-source fallback),
  `src/dashboard/app.py` (sidebar CSV upload, paper trade tracker table, health integrated into
  DATA AVAILABILITY, backtest tier display), `main.py` (--dry-run-postmarket flag),
  `project_status.md` (full S2 phase content), `tests/test_paper_validation.py` (20 new tests).
- **Feature added:**
  - T1: Backtest tiers (PRIMARY_CANDIDATE/SECONDARY_CANDIDATE/WATCHLIST_REVIEW) in fo_universe.json.
    KOTAKBANK/HINDALCO/ADANIENT = PRIMARY. BAJFINANCE/TATASTEEL = SECONDARY. All others = WATCHLIST_REVIEW.
    Diagnostic note: sample sizes too small for pruning decisions.
  - T2: `confidence_tier` (HIGH/MID/LOW) added to every paper journal entry based on effective confidence.
  - T3: `src/fetchers/intraday_csv_parser.py` — Zerodha + TradingView format parser; sidebar widget
    in dashboard for manual CSV upload + per-trade outcome verification (entry trigger, SL, T1, T2).
  - T4: PAPER TRADE JOURNAL section in dashboard — full table with tier/conf pills, running stats,
    "collecting data — X of 10 needed" message until 10 completed trades.
  - T5: Health report integrated INTO DATA AVAILABILITY section (no duplication). Old standalone block removed.
  - T6: `fetch_opening_gap()` in global_fetcher.py — GIFT Nifty NSE API → ^NSEI proxy → ES=F fallback;
    `fetch_global_cues()` includes `opening_gap` key.
  - T7: `run_dry_run_postmarket()` — verifies morning brief, yf 5m fetch (39 bars for WIPRO),
    atomic journal write, project_status update; dry-run result: PASS.
    Atomic write pattern (`_atomic_save_journal()`): write to .tmp → verify JSON round-trip → rename.
  - T8: `project_status.md` fully updated with S2 phase content and backtest context.
  - T9: 20 new tests in `test_paper_validation.py`: tier assignment, confidence_tier boundaries,
    journal append/no-duplicate, health schema, Zerodha/TradingView CSV parse, opening gap fallback,
    dry-run no-corruption.
- **Tests/checks:** 330 passed, 2 skipped. All 20 new tests pass. Dry-run PASS (yf 5m: 39 bars).
- **Remaining limitations:** Paper journal currently has 1 trade (WIPRO CALL, OPEN). Need 10 completed
  trades for meaningful tier accuracy stats. News RSS still down. FII/DII cache empty until next 3:30 PM run.
- **Next step:** Accumulate 10 graded paper trades. Then: Phase 5 live intraday wiring.

---

### 2026-05-22 | S2 — Paper journal, health report, backtest, F&O universe, memory loop | `main`

- **Files changed:** `src/orchestrator.py` (S2B journal helpers, S2C health report, S2F memory log),
  `src/backtesting/backtest_runner.py` (new), `src/dashboard/app.py` (health status bar),
  `data/paper_trades/journal.json` (new), `data/instruments/fo_universe.json` (new),
  `data/health/.gitkeep` (new dir), `project_status.md` (new root file), `docs/project_status.md` (updated).
- **Feature added:**
  - **S2A** Root `project_status.md` quick-reference (phase summary, data quality baseline, accuracy baseline).
  - **S2B** Paper trade journal: `_append_to_paper_journal()` after morning gates (OPEN entries), `_update_paper_journal_outcomes()` after post-market (CLOSED + outcome). Hypothetical trades excluded.
  - **S2C** Daily health report: `_save_health_report()` in orchestrator after all fetches; FRESH/CACHED/FAILED per source; `overall` = HEALTHY/CACHED/DEGRADED/CRITICAL. Compact status bar added to dashboard between main metrics and DATA AVAILABILITY.
  - **S2D** `src/backtesting/backtest_runner.py`: 60-day yfinance OHLCV, EMA20/EMA50/RSI14/vol_ratio rule-based signal (CALL/PUT/NEUTRAL), next-day direction accuracy, per-symbol + overall stats, saves `data/backtesting/results.json`. CLI: `python -m src.backtesting.backtest_runner [--symbol X Y] [--days N]`.
  - **S2E** `data/instruments/fo_universe.json`: 20 F&O stocks + NIFTY + BANKNIFTY with lot_size, tick_size, strike_step, expiry_cycle.
  - **S2F** Memory loop: `logger.info("Memory loaded from %s", past)` when file found; `logger.info("No memory available — fresh start")` when none found in last 7 days.
- **Tests/checks:** Orchestrator edits are non-breaking additions; backtest runner is standalone. No test suite changes needed (pure new functionality).
- **Remaining limitations:** Paper journal and health reports will be empty until next live 8 AM run. Backtest requires yfinance connectivity.
- **Next step:** Phase 5 — wire live `_fetch()` in AngelOne/Kite providers; run `python -m src.backtesting.backtest_runner` to validate signals historically.

---

### 2026-05-21 | PR6 — Instrument master token mapping | `feature/instrument-master-token-mapping`

- **Branch:** `feature/instrument-master-token-mapping`
- **Files changed:** `src/fetchers/instrument_master.py` (new),
  `src/fetchers/intraday_provider.py` (provider stubs updated to use token lookup),
  `src/fetchers/broker_config.py` (passes InstrumentMaster to providers),
  `src/fetchers/data_availability.py` (instrument master status added to report),
  `src/dashboard/app.py` (INSTRUMENT MASTER + TOKEN READINESS status lines added),
  `data/instruments/.gitkeep` (new directory), `data/instruments/schema_example.csv` (new),
  `docs/instrument_master.md` (new), `tests/test_instrument_master.py` (41 new tests),
  `.gitignore` (instrument_master.csv excluded), `docs/project_status.md` (this entry).
- **Feature added:**
  - `InstrumentMaster` class: loads `data/instruments/instrument_master.csv`,
    validates required columns, normalises all fields.
  - Lookup helpers: `lookup_index_symbol()`, `lookup_equity_symbol()`,
    `lookup_option_contract()`, `token_readiness()`.
  - `Instrument` dataclass with `get_token(provider)` / `has_token(provider)`:
    returns `instrument_token` (int) for Kite, `symbol_token` (str) for Angel One.
  - `normalize_symbol_input()`: strips `.NS`/`.BO`, maps `^NSEI` → NIFTY, uppercases.
  - Module-level singleton `get_instrument_master()` for lazy loading.
  - Provider stubs (`AngelOneIntradayProvider`, `KiteIntradayProvider`) now accept
    `instrument_master` kwarg; `_fetch()` performs lookup and logs clear diagnostics
    about missing file, missing symbol, or missing token rather than generic warning.
  - `broker_config.get_configured_provider()` passes the instrument master to broker providers.
  - `data_availability.check_data_availability()` now returns `instrument_master` dict
    with `loaded`, `path`, `record_count`, `error`, and `token_readiness` fields.
  - Dashboard: `[INSTRUMENT MASTER]` status line + `[TOKEN READINESS]` per-index token check.
  - `data/instruments/instrument_master.csv` gitignored (user must supply real tokens).
  - `data/instruments/schema_example.csv` shows required column layout with zero/empty placeholder tokens.
- **Tests/checks:** 245 passed, 2 skipped (pre-existing broker package skips). 41 new
  instrument master tests covering all loading, lookup, token field, normalize, readiness,
  and provider stub scenarios.
- **Remaining limitations:** instrument_master.csv must be downloaded from broker and
  placed locally; option tokens expire and must be refreshed each cycle;
  live `_fetch()` calls not yet implemented.
- **Next step:** Phase 5 — implement live `_fetch()` in AngelOne/Kite providers using
  resolved tokens + SmartConnect / KiteConnect API calls.

---

### 2026-05-22 | S3 — Signal quality analytics and paper collection | `feature/s3-validation-and-next-steps`

- **Branch:** `feature/s3-validation-and-next-steps`
- **Files changed:** `src/analytics/signal_quality.py` (new), `src/validators/premarket_checklist.py` (new),
  `src/alerts/telegram_formatter.py` (new), `src/analytics/__init__.py` (new), `src/validators/__init__.py` (new),
  `src/alerts/__init__.py` (new), `src/orchestrator.py` (S3 wiring: signal_quality, premarket_checklist, telegram_formatter),
  `src/dashboard/app.py` (signal quality expandable rows, opening gap source label, FII prev-day date label),
  `data/instruments/fo_universe.json` (tier updates: HDFCBANK→SECONDARY, TATASTEEL→WATCHLIST),
  `data/backtesting/results_60d.json` (60-day extended results), `.env.example` (Telegram placeholders),
  `docs/project_status.md` (this entry), `tests/test_s3_analytics.py` (26 new tests).
- **Feature added:**
  - Signal quality analytics: per-trade time_to_sl, time_to_t1, best/worst PnL from 5m candles, outcome_quality classification.
  - Premarket checklist validator: 5 checks (trading day, VIX<25, data health, fresh layers, signal consistency). BLOCK vs WARN distinction.
  - Telegram formatter: GO/NO_TRADE/DATA_DEGRADED formatted messages saved to pending_alerts JSON (no bot connected yet).
  - Orchestrator wiring: signal_quality, checklist, and alerts auto-run after morning pipeline (all non-blocking try/except).
  - Dashboard: signal quality expandable rows per journal trade; GIFT Nifty source label with proxy color coding; FII NET shows prev-day date label when using cache.
  - 60-day backtest: 50.2% vs 30-day 45.4%. HDFCBANK and TATASTEEL tier updated (both timeframes agreed). 7 symbols with disagreement unchanged.
  - Memory loop: confirmed working; logs correctly from test file; no post_market data yet (fresh system).
  - Telegram: formatter built, sending pending 10 clean paper trades completion.
- **Tests/checks:** 356 passed, 2 skipped. 26 new tests covering signal_quality, premarket_checklist (all-pass + 7 block/warn cases), telegram_formatter (format + save + build), memory loop (load/miss/skip-future/most-recent).
- **Remaining limitations:** 2 paper trades collected (need 10 closed for tier accuracy stats). FII/DII cache empty until 3:30 PM post-market run. Telegram bot not yet connected. TATAMOTORS delisted from yfinance.
- **Next step:** Accumulate 10 graded paper trades. Then: Phase 5 live intraday wiring.

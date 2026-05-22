# Project Status — Indian F&O AI Trading Agent

> Full history: `docs/project_status.md`

---

## Last Run: 2026-05-22  19:17 IST

**Market:** MIXED  |  VIX zone: NORMAL  |  Risk: MEDIUM
**Gates:** 0 allowed, 2 blocked  |  Penalty: -0.3  |  Status: ACTIONABLE_TRADES_AVAILABLE

## Trades Fired Today

  - WIPRO CALL  conf=7.8  eff=7.5  [TRADE_ALLOWED]
  - HINDALCO CALL  conf=7.5  eff=7.2  [TRADE_ALLOWED]

**Gated out:** ICICIBANK CALL (NO_TRADE), DRREDDY CALL (NO_TRADE), ADANIENT PUT (NO_TRADE)

## Data Health

[DEGRADED]  VIX: FRESH  |  FII_DII: FAILED  |  PARTICIPANT_OI: FAILED  |  OPTION_CHAIN: FRESH  |  GLOBAL_CUES: FRESH  |  TECHNICALS: FRESH  |  NEWS: FRESH

## Running Accuracy

No trades tracked yet

---

## Phase Summary

| Phase | Status |
|-------|--------|
| Phase 1–3 + PR1–PR7 | Done |
| 7 Critical Fixes | Done |
| S2 (journal, health, backtest, fo_universe, memory) | Done |
| S3 (FII fix, instrument master, intraday outcome, backtest run) | Done |

## Next Steps

1. Run backtest: `python -m src.backtesting.backtest_runner --days 30`
2. Phase 5: Wire live AngelOne / Kite `_fetch()` for intraday candles
3. News RSS: Fix ET Markets / Moneycontrol feed URLs
4. FII/DII: Cache seeds after next 3:30 PM post-market run

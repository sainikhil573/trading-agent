# Project Status — Indian F&O AI Trading Agent

> Full history: `docs/project_status.md`

## Phase: S2 Paper Validation + Diagnostics

### S1 Stabilization: COMPLETE
All 7 critical fixes applied. Gates working. FII/DII fallbacks implemented.
Pre-market penalty capped at -0.3. Volume threshold lowered to ≥0.3.

### Backtest Baseline (diagnostic only — 30-day rule-based signals)
- Overall accuracy: 44.9% (83/185 signals) — below 50% random baseline
- Candidate outperformers **(NON-CONCLUSIVE):** KOTAKBANK 78.6%, HINDALCO 61.1%, ADANIENT 56.2%
- Weak performers **(KEPT IN UNIVERSE, labeled WATCHLIST_REVIEW):** AXISBANK 0%, LT/RELIANCE 25%
- **Sample sizes too small for permanent pruning decisions**
- All tiers generate signals and appear on dashboard; WATCHLIST_REVIEW stocks labeled clearly

### Current Paper Trades
- 1 trade open: WIPRO CALL (confidence 7.3 → 7.0 effective, LOW tier, paper_only=true)
- Next milestone: 10 clean graded paper trades to validate gate system

### Out of Scope
Live execution, broker API, ML scoring, auto-threshold changes, aggressive pruning

## Data Health (last 8 AM run)
Updated automatically after each morning analysis run.

## Running Accuracy
Updated automatically after each post-market run.
Only `hypothetical: false` entries counted — pre-S3 stale entries excluded.

## Next Steps
1. Accumulate 10 graded paper trades (gate system validation)
2. Run backtest again at 60 days for statistically meaningful sample
3. Phase 5: Wire live AngelOne/Kite intraday fetch
4. Fix RSS news feeds (ET Markets syntax error, Moneycontrol 503)
5. FII/DII cache seeds automatically after next 3:30 PM post-market run

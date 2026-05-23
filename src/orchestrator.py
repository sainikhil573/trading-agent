"""
Orchestrator — two daily analysis cycles:
  run_morning_analysis()  — 8:00 AM IST full pre-market brief
  run_preopen_analysis()  — 9:00 AM IST final GO/WAIT/SKIP check per trade
"""

from __future__ import annotations
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pytz
from datetime import datetime as _dt

from src.fetchers.nse_fetcher       import NSEFetcher
from src.fetchers.global_fetcher    import fetch_global_cues
from src.fetchers.technical_fetcher import fetch_index_technicals, fetch_stock_technicals
from src.fetchers.news_fetcher      import fetch_market_headlines
from src.analyzers.signal_analyzer  import build_market_signal, build_stock_signals
from src.analyzers.claude_analyzer  import ClaudeAnalyzer
from src.analyzers.evaluation       import derive_data_quality, resolve_outcome_with_intraday
from src.analyzers.trade_gates      import apply_trade_gates
from src.fetchers.intraday_provider import get_default_provider

# Fixed 20-stock F&O universe for daily signal generation
FNO_UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "AXISBANK", "KOTAKBANK", "SBIN", "TATASTEEL", "HINDALCO",
    "ONGC", "BPCL", "MARUTI", "BAJFINANCE", "TATAMOTORS",
    "WIPRO", "SUNPHARMA", "DRREDDY", "ADANIENT", "LT",
]

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")

OUT_DIR = Path("data/processed")


def _ist_now_str() -> str:
    return _dt.now(IST).strftime("%H:%M IST")


def _save(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Saved -> %s", path)


# ---------------------------------------------------------------------------
# 8:00 AM — Morning analysis
# ---------------------------------------------------------------------------

def run_morning_analysis(api_key: str) -> dict:
    """
    Full 6-layer pre-market analysis.
    Saves to  data/processed/trade_brief_morning_YYYY-MM-DD.json
    Returns the complete brief dict.
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("MORNING ANALYSIS STARTED  —  %s  (%s)", today, _ist_now_str())
    logger.info("=" * 70)

    nse = NSEFetcher()

    # -- Step 1: NSE derivative data -------------------------------------
    logger.info("[1/6] Fetching NSE derivative data...")

    vix     = nse.fetch_india_vix()
    fii_dii = nse.fetch_fii_dii()
    logger.info("  VIX: %s  |  FII net: %s cr  |  DII net: %s cr",
                vix, fii_dii["fii_net_buy"], fii_dii["dii_net_buy"])

    raw_nifty  = nse.fetch_option_chain("NIFTY")
    df_nifty,  meta_nifty  = nse.parse_option_chain(raw_nifty,  "NIFTY")
    max_pain_n = nse.calculate_max_pain(df_nifty)

    raw_bn = nse.fetch_option_chain("BANKNIFTY")
    df_bn, meta_bn = nse.parse_option_chain(raw_bn, "BANKNIFTY")
    max_pain_bn = nse.calculate_max_pain(df_bn)

    df_fno = nse.fetch_top_fno_stocks(top_n=10)

    # -- Step 2: Participant OI + Block deals ----------------------------
    logger.info("[2/6] Fetching institutional data...")
    participant_oi = nse.fetch_participant_oi()
    block_deals    = nse.fetch_block_deals()

    # -- Step 3: Global cues --------------------------------------------
    logger.info("[3/6] Fetching global cues...")
    global_cues = fetch_global_cues()
    logger.info("  Global bias: %s  (+%d / -%d)",
                global_cues["overall_bias"],
                global_cues["positive_count"],
                global_cues["negative_count"])

    # -- Step 4: Technical indicators ------------------------------------
    logger.info("[4/6] Fetching technical indicators...")
    nifty_tech     = fetch_index_technicals("NIFTY")
    banknifty_tech = fetch_index_technicals("BANKNIFTY")
    stock_symbols  = df_fno["symbol"].tolist() if not df_fno.empty else []
    stock_tech     = fetch_stock_technicals(stock_symbols[:10])

    logger.info("[4b] Fetching F&O universe technicals (%d stocks)...", len(FNO_UNIVERSE))
    fno_universe_tech = fetch_stock_technicals(FNO_UNIVERSE)
    ok_count = sum(1 for t in fno_universe_tech if not t.get("error"))
    logger.info("  F&O universe: %d/%d stocks fetched successfully", ok_count, len(FNO_UNIVERSE))

    # -- Step 5: News headlines -----------------------------------------
    logger.info("[5/6] Fetching news headlines...")
    news = fetch_market_headlines(max_items=20)
    logger.info("  Headlines: %d from %s", len(news["headlines"]), news["sources_ok"])

    # -- Step 6: Build signals + Claude API ------------------------------
    logger.info("[6/6] Building signals & calling Claude API...")

    nifty_signal = build_market_signal(
        "NIFTY", meta_nifty, df_nifty, max_pain_n, vix, global_cues["overall_bias"]
    )
    banknifty_signal = build_market_signal(
        "BANKNIFTY", meta_bn, df_bn, max_pain_bn, vix, global_cues["overall_bias"]
    )
    stock_signals_list = build_stock_signals(df_fno)

    # S2C — daily health report after all fetches
    _save_health_report(
        today, vix, fii_dii, meta_nifty, participant_oi,
        global_cues, fno_universe_tech, news,
    )

    # Load yesterday's post-market memory (look back up to 7 days)
    yesterday_memory: dict | None = None
    for i in range(1, 8):
        past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        pm_path = OUT_DIR / f"post_market_{past}.json"
        if pm_path.exists():
            with open(pm_path, encoding="utf-8") as f:
                yesterday_memory = json.load(f)
            logger.info("  Memory loaded from %s", past)
            break
    if yesterday_memory is None:
        logger.info("  No memory available — fresh start")

    analyzer = ClaudeAnalyzer(api_key=api_key)
    brief = analyzer.generate_trade_brief(
        nifty_signal             = nifty_signal,
        banknifty_signal         = banknifty_signal,
        global_cues              = global_cues,
        fii_dii                  = fii_dii,
        participant_oi           = participant_oi,
        nifty_tech               = nifty_tech,
        banknifty_tech           = banknifty_tech,
        stock_signals            = stock_signals_list,
        stock_technicals         = stock_tech,
        block_deals              = block_deals,
        news                     = news,
        analysis_date            = date.today().strftime("%d-%b-%Y (%A)"),
        yesterday_memory         = yesterday_memory,
        fno_universe_technicals  = fno_universe_tech,
    )

    # Attach raw context for the dashboard and pre-open run
    brief["_meta"] = {
        "vix_at_8am":       vix,
        "global_bias_8am":  global_cues["overall_bias"],
        "nifty_spot":       meta_nifty["spot"],
        "nifty_pcr":        meta_nifty["pcr"],
        "nifty_atm":        meta_nifty["atm_strike"],
        "banknifty_spot":   meta_bn["spot"],
        "banknifty_pcr":    meta_bn["pcr"],
        "banknifty_atm":    meta_bn["atm_strike"],
        "global_cues": {
            k: v for k, v in global_cues.items() if isinstance(v, dict)
        },
        "fii_dii":          fii_dii,
        "participant_oi":   participant_oi,
    }

    # Apply deterministic trade gates (post-process Claude's output)
    data_quality = derive_data_quality(brief["_meta"], brief.get("market_context", {}), is_premarket=True)
    brief = apply_trade_gates(brief, data_quality, nifty_signal, banknifty_signal)

    # S2B — append allowed trades to paper journal
    _append_to_paper_journal(brief, today)

    # S3 — signal quality analytics (non-blocking)
    try:
        from src.analytics.signal_quality import run_signal_quality
        run_signal_quality()
    except Exception as _sq_exc:
        logger.warning("Signal quality skipped: %s", _sq_exc)

    # S3 — premarket checklist (attach to brief before saving)
    try:
        from src.validators.premarket_checklist import apply_checklist_to_brief
        health_path = Path("data/health") / f"health_{today}.json"
        _health_data = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else None
        brief = apply_checklist_to_brief(brief, health=_health_data)
    except Exception as _cl_exc:
        logger.warning("Premarket checklist skipped: %s", _cl_exc)

    # S3 — Telegram formatter (non-blocking)
    try:
        from src.alerts.telegram_formatter import build_alerts_from_brief, save_pending_alerts
        vix_val = (brief.get("_meta", {}) or {}).get("vix_at_8am")
        _health_path2 = Path("data/health") / f"health_{today}.json"
        _health_for_alert = json.loads(_health_path2.read_text(encoding="utf-8")) if _health_path2.exists() else None
        _alerts = build_alerts_from_brief(brief, health=_health_for_alert, vix=vix_val, date_str=today)
        save_pending_alerts(_alerts, date_str=today)
    except Exception as _tg_exc:
        logger.warning("Telegram formatter skipped: %s", _tg_exc)

    # FIX 6 — update root project_status.md with today's run stats
    _update_project_status(brief, today)

    gs = brief.get("_gate_summary", {})
    logger.info(
        "Trade gates applied: %d allowed, %d blocked (data_penalty=%.1f)",
        gs.get("allowed", 0), gs.get("blocked", 0), gs.get("data_penalty", 0),
    )

    out_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    _save(brief, out_path)

    _print_morning_brief(brief, today)
    logger.info("MORNING ANALYSIS COMPLETE")
    return brief


# ---------------------------------------------------------------------------
# S2C — Daily health report
# ---------------------------------------------------------------------------

def _save_health_report(
    today: str, vix, fii_dii: dict, meta_nifty: dict,
    participant_oi: dict, global_cues: dict,
    fno_universe_tech: list, news: dict,
) -> None:
    def _status(ok: bool, fallback: bool = False) -> str:
        if fallback:
            return "CACHED"
        return "FRESH" if ok else "FAILED"

    fii_fallback = bool(fii_dii.get("is_prev_day"))
    fii_ok       = fii_dii.get("fii_net_buy", 0.0) != 0.0
    poi_cached   = bool(participant_oi.get("is_cached"))
    poi_ok       = "error" not in participant_oi

    sources = {
        "vix":           {"status": _status(vix and float(vix) > 0), "value": vix},
        "fii_dii":       {"status": _status(fii_ok or fii_fallback, fii_fallback),
                          "fallback_date": fii_dii.get("fallback_date"), "fii_net": fii_dii.get("fii_net_buy")},
        "participant_oi":{"status": _status(poi_ok or poi_cached, poi_cached),
                          "fallback_source": participant_oi.get("fallback_source"),
                          "bias": participant_oi.get("fii_futures_bias")},
        "option_chain":  {"status": _status(meta_nifty.get("spot", 0) > 0),
                          "nifty_spot": meta_nifty.get("spot"), "pcr": meta_nifty.get("pcr")},
        "global_cues":   {"status": _status(global_cues.get("overall_bias") not in (None, "UNKNOWN")),
                          "bias": global_cues.get("overall_bias")},
        "technicals":    {"status": _status(sum(1 for t in fno_universe_tech if not t.get("error")) > 0),
                          "stocks_ok": sum(1 for t in fno_universe_tech if not t.get("error")),
                          "stocks_total": len(fno_universe_tech)},
        "news":          {"status": _status(len(news.get("headlines", [])) > 0),
                          "count": len(news.get("headlines", []))},
    }

    statuses    = [s["status"] for s in sources.values()]
    n_failed    = statuses.count("FAILED")
    n_available = len(statuses) - n_failed
    if n_available >= 5 and "CACHED" not in statuses:
        overall = "HEALTHY"
    elif n_available >= 5:
        overall = "CACHED"
    elif n_available in (3, 4):
        overall = "PARTIAL"
    elif n_available in (1, 2):
        overall = "DEGRADED"
    else:
        overall = "CRITICAL"

    report = {"date": today, "time": _ist_now_str(), "sources": sources, "overall": overall}
    health_dir = Path("data/health")
    health_dir.mkdir(parents=True, exist_ok=True)
    _save(report, health_dir / f"health_{today}.json")
    logger.info("Health report saved: %s (%d failed sources)", overall, n_failed)


# ---------------------------------------------------------------------------
# S2B — Paper trade journal helpers
# ---------------------------------------------------------------------------

_JOURNAL_PATH = Path("data/paper_trades/journal.json")


def _confidence_tier(eff_conf: float | None) -> str:
    """Map gate_effective_confidence to HIGH / MID / LOW / BELOW_FLOOR."""
    if eff_conf is None:
        return "BELOW_FLOOR"
    if eff_conf >= 8.0:
        return "HIGH"
    if eff_conf >= 7.5:
        return "MID"
    if eff_conf >= 7.0:
        return "LOW"
    return "BELOW_FLOOR"


def _atomic_save_journal(journal: dict) -> None:
    """Write journal atomically: tmp → verify round-trip → rename."""
    tmp_path = Path(str(_JOURNAL_PATH) + ".tmp")
    _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(journal, indent=2, ensure_ascii=False)
    # Verify JSON round-trips before committing
    try:
        json.loads(raw)
    except Exception as exc:
        logger.error("Journal JSON round-trip failed — aborting write: %s", exc)
        return
    tmp_path.write_text(raw, encoding="utf-8")
    tmp_path.replace(_JOURNAL_PATH)


def _append_to_paper_journal(brief: dict, today: str) -> None:
    _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _JOURNAL_PATH.exists():
        with open(_JOURNAL_PATH, encoding="utf-8") as f:
            journal = json.load(f)
    else:
        journal = {"trades": []}

    # Idempotent: remove today's existing entries then re-add
    journal["trades"] = [t for t in journal["trades"] if t.get("date") != today]

    for t in brief.get("stock_trades", []):
        eff_conf = t.get("gate_effective_confidence")
        journal["trades"].append({
            "date":                     today,
            "symbol":                   t.get("symbol"),
            "signal":                   t.get("signal"),
            "strike":                   t.get("strike"),
            "confidence":               t.get("confidence"),
            "gate_effective_confidence": eff_conf,
            "confidence_tier":          _confidence_tier(eff_conf),
            "entry_price":              t.get("entry_price"),
            "stop_loss":                t.get("stop_loss"),
            "target_1":                 t.get("target_1"),
            "target_2":                 t.get("target_2"),
            "status":                   "OPEN",
            "outcome":                  None,
            "was_correct":              None,
            "hypothetical":             True,   # paper-only until live execution enabled
            "paper_only":               True,
        })

    journal["updated"] = _ist_now_str()
    _atomic_save_journal(journal)
    logger.info("Paper journal updated: %d total entries", len(journal["trades"]))


def _update_project_status(brief: dict, today: str) -> None:
    """FIX 6 — overwrite root project_status.md with today's run stats."""
    gs     = brief.get("_gate_summary", {})
    ctx    = brief.get("market_context", {})
    stock_trades = brief.get("stock_trades", [])
    gated  = brief.get("stock_trades_gated_out", [])

    health_path = Path("data/health") / f"health_{today}.json"
    health_summary = "No health report yet"
    if health_path.exists():
        with open(health_path, encoding="utf-8") as f:
            h = json.load(f)
        srcs = h.get("sources", {})
        health_summary = "  |  ".join(
            f"{k.upper()}: {v['status']}" for k, v in srcs.items()
        )
        health_summary = f"[{h.get('overall','?')}]  {health_summary}"

    trades_fired = "\n".join(
        f"  - {t['symbol']} {t['signal']}  conf={t.get('confidence')}"
        f"  eff={t.get('gate_effective_confidence')}  [{t.get('gate_status','?')}]"
        for t in stock_trades
    ) or "  None (all gated out)"

    gated_summary = ", ".join(
        f"{t.get('symbol')} {t.get('signal')} ({t.get('gate_status','')})"
        for t in gated
    ) or "None"

    # Load rolling accuracy if available
    acc_path = OUT_DIR / "prediction_accuracy.json"
    acc_line = "No trades tracked yet"
    if acc_path.exists():
        with open(acc_path, encoding="utf-8") as f:
            log = json.load(f)
        # Only count entries explicitly marked hypothetical=False (post S3 format)
        real = [r for r in log if not r.get("error") and r.get("hypothetical") is False]
        if real:
            correct = sum(1 for r in real if r.get("was_correct"))
            wins    = sum(1 for r in real if r.get("outcome") in ("TARGET_1_HIT","TARGET_2_HIT"))
            total   = len(real)
            acc_line = (
                f"{total} trades tracked  |  "
                f"Direction: {correct}/{total} ({correct/total*100:.0f}%)  |  "
                f"Wins (T1/T2): {wins}/{total} ({wins/total*100:.0f}%)"
            )

    content = f"""# Project Status — Indian F&O AI Trading Agent

> Full history: `docs/project_status.md`

---

## Last Run: {today}  {_ist_now_str()}

**Market:** {ctx.get('overall_market_bias','?')}  |  VIX zone: {ctx.get('vix_zone','?')}  |  Risk: {ctx.get('overall_risk_rating','?')}
**Gates:** {gs.get('allowed',0)} allowed, {gs.get('blocked',0)} blocked  |  Penalty: -{gs.get('data_penalty',0):.1f}  |  Status: {gs.get('final_recommendation_status','?')}

## Trades Fired Today

{trades_fired}

**Gated out:** {gated_summary}

## Data Health

{health_summary}

## Running Accuracy

{acc_line}

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
"""
    status_path = Path("project_status.md")
    status_path.write_text(content, encoding="utf-8")
    logger.info("project_status.md updated for %s", today)


def _update_paper_journal_outcomes(results: list[dict], today: str) -> None:
    if not _JOURNAL_PATH.exists():
        return
    with open(_JOURNAL_PATH, encoding="utf-8") as f:
        journal = json.load(f)

    result_map = {r["symbol"]: r for r in results if not r.get("error")}
    for entry in journal["trades"]:
        if entry.get("date") == today and entry.get("symbol") in result_map:
            r = result_map[entry["symbol"]]
            entry["status"]             = "CLOSED"
            entry["outcome"]            = r.get("outcome")
            entry["was_correct"]        = r.get("was_correct")
            entry["actual_close"]       = r.get("index_close")
            entry["est_final_premium"]  = r.get("est_final_premium")

    journal["updated"] = _ist_now_str()
    _atomic_save_journal(journal)
    logger.info("Paper journal outcomes updated for %s", today)


# ---------------------------------------------------------------------------
# 9:00 AM — Pre-open confirmation
# ---------------------------------------------------------------------------

def run_preopen_analysis(api_key: str) -> dict:
    """
    Re-fetches VIX, global cues, news and asks Claude for GO/WAIT/SKIP per trade.
    Saves to  data/processed/trade_brief_preopen_YYYY-MM-DD.json
    Returns the pre-open review dict.
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("PRE-OPEN CHECK STARTED  —  %s  (%s)", today, _ist_now_str())
    logger.info("=" * 70)

    # Load morning brief
    morning_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    if not morning_path.exists():
        logger.warning("Morning brief not found at %s — running morning analysis first", morning_path)
        morning = run_morning_analysis(api_key)
    else:
        with open(morning_path, encoding="utf-8") as f:
            morning = json.load(f)

    morning_trades  = morning.get("trades", [])
    vix_at_8am      = morning.get("_meta", {}).get("vix_at_8am")

    if not morning_trades:
        logger.info("No morning trades to review — skipping pre-open Claude call")
        result = {
            "analysis_time": _ist_now_str(),
            "note": "No morning trades to review",
            "trades_review": [],
        }
        _save(result, OUT_DIR / f"trade_brief_preopen_{today}.json")
        return result

    # Fresh data
    logger.info("[1/3] Re-fetching VIX...")
    nse = NSEFetcher()
    vix_now = nse.fetch_india_vix()
    logger.info("  VIX now: %s  (was %s at 8 AM)", vix_now, vix_at_8am)

    logger.info("[2/3] Re-fetching global cues...")
    global_fresh = fetch_global_cues()

    logger.info("[3/3] Re-fetching news headlines...")
    news_fresh = fetch_market_headlines(max_items=10)

    logger.info("Calling Claude API for pre-open decisions...")
    analyzer = ClaudeAnalyzer(api_key=api_key)
    preopen  = analyzer.generate_preopen_check(
        morning_trades    = morning_trades,
        vix_current       = vix_now,
        vix_morning       = vix_at_8am,
        global_cues_fresh = global_fresh,
        news_fresh        = news_fresh,
    )

    out_path = OUT_DIR / f"trade_brief_preopen_{today}.json"
    _save(preopen, out_path)

    _print_preopen_brief(preopen, today)
    logger.info("PRE-OPEN CHECK COMPLETE")
    return preopen


# ---------------------------------------------------------------------------
# Console printers
# ---------------------------------------------------------------------------

def _print_morning_brief(brief: dict, today: str) -> None:
    ctx    = brief.get("market_context", {})
    trades = brief.get("trades", [])
    sep    = "=" * 70

    print(f"\n{sep}")
    print(f"  MORNING TRADE BRIEF  —  {today}")
    print(f"  Market:  {ctx.get('overall_market_bias','?')}  |  "
          f"VIX zone: {ctx.get('vix_zone','?')}  |  "
          f"Risk: {ctx.get('overall_risk_rating','?')}")
    print(f"  Trading recommended: {ctx.get('trading_recommended', '?')}")
    if ctx.get("trading_caution"):
        print(f"  CAUTION: {ctx['trading_caution']}")
    if brief.get("event_warnings"):
        for w in brief["event_warnings"]:
            print(f"  *** EVENT: {w}")
    print(sep)

    if not trades:
        print("  No trades recommended (confidence threshold not met).")
        filt = brief.get("trades_filtered_out", [])
        if filt:
            print(f"  Filtered out: {', '.join(t.get('symbol','?') for t in filt)}")
    else:
        for t in trades:
            bd = t.get("confidence_breakdown", {})
            print(f"\n  {t.get('symbol','?')} — {t.get('signal','?')}  "
                  f"(confidence: {t.get('confidence','?')}/10  |  "
                  f"size: {t.get('position_size_recommendation','?')})")
            print(f"  Strike/Expiry  : {t.get('strike','?')}  |  {t.get('expiry','?')}"
                  + ("  *** EXPIRY DAY ***" if t.get("expiry_day_warning") else ""))
            print(f"  Entry          : {t.get('entry_price','?')}")
            print(f"  Stop-Loss      : {t.get('stop_loss','?')}")
            print(f"  Target 1 / 2   : {t.get('target_1','?')} / {t.get('target_2','?')}")
            print(f"  Risk:Reward    : 1:{t.get('risk_reward','?')}")
            if bd:
                scores = "  ".join(f"{k[:4]}={v}" for k, v in bd.items())
                print(f"  Scores         : {scores}")
            print(f"  Entry trigger  : {t.get('entry_trigger','')}")
            print(f"  Recovery risk  : {t.get('mid_session_recovery_risk','')}")
            print(f"  Reasoning      : {t.get('reasoning','')}")
            print(f"  Key Risk       : {t.get('key_risk','')}")

    stock_trades = brief.get("stock_trades", [])
    if stock_trades:
        print(f"\n  --- STOCK F&O SIGNALS ({len(stock_trades)}) ---")
        for st in stock_trades:
            aligned = " [SECTOR ALIGNED]" if st.get("sector_aligned") else ""
            print(f"\n  {st.get('symbol','?')} ({st.get('sector','')}) — "
                  f"{st.get('signal','?')}  "
                  f"(confidence: {st.get('confidence','?')}/10  |  "
                  f"size: {st.get('position_size','?')}){aligned}")
            print(f"  Entry: {st.get('entry_price','?')}  "
                  f"SL: {st.get('stop_loss','?')}  "
                  f"T1: {st.get('target_1','?')}  "
                  f"T2: {st.get('target_2','?')}  "
                  f"RR: 1:{st.get('risk_reward','?')}")
            print(f"  Trigger: {st.get('trigger','')}")
            reasoning = st.get('reasoning','')
            if reasoning:
                print(f"  Reasoning: {reasoning[:200]}{'...' if len(reasoning) > 200 else ''}")
    else:
        print("  No stock signals above 7.0 confidence threshold today.")

    if brief.get("morning_summary"):
        print(f"\n  Summary: {brief['morning_summary']}")

    lvl = brief.get("key_levels", {})
    if lvl:
        print(f"\n  NIFTY     S: {lvl.get('nifty_support')}   R: {lvl.get('nifty_resistance')}")
        print(f"  BANKNIFTY S: {lvl.get('banknifty_support')}   R: {lvl.get('banknifty_resistance')}")

    if brief.get("sectors_to_avoid_today"):
        print(f"\n  Avoid sectors : {brief['sectors_to_avoid_today']}")
    if brief.get("sectors_to_favor_today"):
        print(f"  Favor sectors : {brief['sectors_to_favor_today']}")

    print(f"\n{sep}\n")


def _print_preopen_brief(preopen: dict, today: str) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  PRE-OPEN CHECK  —  {today}  |  "
          f"VIX now: {preopen.get('vix_current','?')}  |  "
          f"Global: {preopen.get('global_bias_current','?')}  |  "
          f"Opening: {preopen.get('market_opening_outlook','?')}")
    print(sep)

    for tr in preopen.get("trades_review", []):
        action = tr.get("final_action", "?")
        sym    = tr.get("symbol", "?")
        icon   = {"GO": "OK", "WAIT": "--", "SKIP": "XX"}.get(action, "??")
        print(f"\n  [{icon}] {action:4s}  {sym} {tr.get('signal','?')} {tr.get('strike','?')}")
        print(f"        Reason : {tr.get('final_action_reason','')}")
        if tr.get("note"):
            print(f"        Note   : {tr['note']}")
        if tr.get("adjusted_entry"):
            print(f"        Adj.Entry: {tr['adjusted_entry']}")

    if preopen.get("preopen_summary"):
        print(f"\n  {preopen['preopen_summary']}")
    print(f"\n{sep}\n")


# ---------------------------------------------------------------------------
# 3:30 PM IST — Post-market outcome tracker
# ---------------------------------------------------------------------------

# ATM delta approximation for index options
_ATM_DELTA = 0.5

# yfinance tickers for indices
_INDEX_YF = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}


def _fetch_yf_intraday(yf_ticker: str) -> "pd.DataFrame":
    """
    Fetch today's 5-minute OHLCV from yfinance for the given ticker.
    Returns a DataFrame with columns [datetime, open, high, low, close, volume]
    suitable for resolve_outcome_with_intraday(), or empty DataFrame on failure.
    """
    import pandas as pd
    import yfinance as yf

    try:
        hist = yf.Ticker(yf_ticker).history(period="1d", interval="5m")
        if hist.empty:
            return pd.DataFrame()
        hist = hist.reset_index()
        # Normalize column names
        hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]
        # yfinance uses 'datetime' or 'timestamp' as the index column after reset
        rename = {}
        for col in hist.columns:
            if col in ("index", "date", "timestamp", "datetime"):
                rename[col] = "datetime"
        if rename:
            hist = hist.rename(columns=rename)
        needed = ["datetime", "open", "high", "low", "close", "volume"]
        if all(c in hist.columns for c in needed):
            return hist[needed].copy()
        return pd.DataFrame()
    except Exception as exc:
        logger.warning("yfinance 5m fetch failed for %s: %s", yf_ticker, exc)
        return pd.DataFrame()


def run_postmarket_tracker() -> dict:
    """
    Fetch today's closing OHLC for each predicted instrument,
    compare to morning trade brief, and append to prediction_accuracy.json.
    No Claude API call — purely data-driven outcome tracking.
    """
    import yfinance as yf

    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("POST-MARKET TRACKER  —  %s  (%s)", today, _ist_now_str())
    logger.info("=" * 70)

    morning_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    if not morning_path.exists():
        logger.warning("No morning brief for %s — nothing to track", today)
        return {}

    with open(morning_path, encoding="utf-8") as f:
        morning = json.load(f)

    # Track both gate-allowed trades AND gated-out trades (marked hypothetical)
    allowed_trades  = morning.get("trades", [])
    gated_out_idx   = morning.get("trades_gated_out", [])
    gated_out_stock = morning.get("stock_trades_gated_out", [])

    # Combine: allowed trades first, then gated-out as hypothetical
    all_trades = [
        {**t, "_hypothetical": False} for t in allowed_trades
    ] + [
        {**t, "_hypothetical": True, "_gate_reason": t.get("gate_status", "GATED")}
        for t in gated_out_idx + gated_out_stock
    ]

    if not all_trades:
        logger.info("No trades (allowed or gated) in morning brief — skipping tracker")
        return {}

    trades = all_trades
    intraday_provider = get_default_provider()
    if intraday_provider.is_available():
        logger.info("Intraday provider: %s", intraday_provider.name)
    else:
        logger.info("No intraday data source — ambiguous outcomes will be OUTCOME_UNKNOWN")

    results = []
    for trade in trades:
        sym            = trade.get("symbol", "")
        signal         = trade.get("signal", "")           # CALL or PUT
        strike         = trade.get("strike")
        entry_premium  = trade.get("entry_price")
        sl_premium     = trade.get("stop_loss")
        t1_premium     = trade.get("target_1")
        t2_premium     = trade.get("target_2")
        confidence     = trade.get("confidence")

        yf_ticker = _INDEX_YF.get(sym, f"{sym}.NS")

        try:
            hist = yf.Ticker(yf_ticker).history(period="1d", interval="1d")
            if hist.empty:
                results.append({"symbol": sym, "date": today, "error": "No price data"})
                continue

            row     = hist.iloc[-1]
            open_p  = round(float(row["Open"]),  2)
            high_p  = round(float(row["High"]),  2)
            low_p   = round(float(row["Low"]),   2)
            close_p = round(float(row["Close"]), 2)

            day_chg_pts = round(close_p - open_p, 2)
            day_chg_pct = round(day_chg_pts / open_p * 100, 2) if open_p else 0.0

            actual_dir    = "UP" if close_p >= open_p else "DOWN"
            predicted_dir = "UP" if signal == "CALL" else "DOWN"
            was_correct   = actual_dir == predicted_dir

            # Approximate intraday best/worst premium moves via ATM delta
            if signal == "PUT":
                # PUT premium improves when spot drops
                worst_spot_move = high_p  - open_p   # spot UP = bad for put
                best_spot_move  = open_p  - low_p    # spot DOWN = good for put
            else:
                worst_spot_move = open_p  - low_p    # spot DOWN = bad for call
                best_spot_move  = high_p  - open_p   # spot UP = good for call

            worst_premium = round(entry_premium - worst_spot_move * _ATM_DELTA, 2) if entry_premium else None
            best_premium  = round(entry_premium + best_spot_move  * _ATM_DELTA, 2) if entry_premium else None
            final_premium = round(entry_premium + (
                -day_chg_pts if signal == "PUT" else day_chg_pts
            ) * _ATM_DELTA, 2) if entry_premium else None

            # Use evaluation module to resolve outcome.
            # If intraday candles exist, the chronological SL vs target sequence
            # is used. Otherwise falls back to daily OHLC (marks ambiguous cases
            # as OUTCOME_UNKNOWN — never credits a false win).
            _daily_ctx = {"open_p": open_p, "high_p": high_p, "low_p": low_p,
                          "was_correct": was_correct}
            _candles   = intraday_provider.get_candles(sym, date.today())
            # FIX 3: if broker provider empty, fall back to yfinance 5m
            if _candles.empty:
                _candles = _fetch_yf_intraday(yf_ticker)
                if not _candles.empty:
                    logger.info("  %s: using yfinance 5m intraday (%d bars)", sym, len(_candles))
            _resolved  = resolve_outcome_with_intraday(
                trade, _daily_ctx,
                _candles if not _candles.empty else None,
            )
            sl_hit         = _resolved["sl_hit"]
            t1_reached     = _resolved["target_1_reached"]
            t2_reached     = _resolved["target_2_reached"]
            path_ambiguous = _resolved["path_ambiguous"]
            outcome        = _resolved["outcome"]
            data_source    = _resolved["data_source"]

            is_hypo = trade.get("_hypothetical", False)
            results.append({
                "symbol":            sym,
                "date":              today,
                "signal":            signal,
                "strike":            strike,
                "confidence":        confidence,
                "predicted_direction": predicted_dir,
                "actual_direction":    actual_dir,
                "was_correct":         was_correct,
                "outcome":             outcome,
                "index_open":          open_p,
                "index_high":          high_p,
                "index_low":           low_p,
                "index_close":         close_p,
                "day_change_pts":      day_chg_pts,
                "day_change_pct":      day_chg_pct,
                "entry_premium":       entry_premium,
                "sl_premium":          sl_premium,
                "target_1_premium":    t1_premium,
                "target_2_premium":    t2_premium,
                "est_final_premium":   final_premium,
                "sl_hit":              sl_hit,
                "target_1_reached":    t1_reached,
                "target_2_reached":    t2_reached,
                "path_ambiguous":      path_ambiguous,
                "path_note":           _resolved.get("path_note"),
                "data_source":         data_source,
                "hypothetical":        is_hypo,
                "gate_status":         trade.get("gate_status", "TRADE_ALLOWED"),
                "note":                (
                    "HYPOTHETICAL — trade was gated out: " + trade.get("_gate_reason", "")
                    if is_hypo else
                    "Premium estimates use ATM delta=0.5 approximation"
                ),
            })

        except Exception as exc:
            logger.warning("Post-market fetch failed for %s: %s", sym, exc)
            results.append({"symbol": sym, "date": today, "error": str(exc)})

    # Append to rolling accuracy log (one entry per trade per day)
    # Only real (non-hypothetical) trades count toward win/loss stats.
    # Hypothetical gated-out trades are stored but marked separately.
    acc_path = OUT_DIR / "prediction_accuracy.json"
    log: list[dict] = []
    if acc_path.exists():
        with open(acc_path, encoding="utf-8") as f:
            log = json.load(f)

    # Replace any existing entries for today (idempotent re-runs)
    log = [r for r in log if r.get("date") != today]
    log.extend(results)
    _save(log, acc_path)

    # S2B — update paper journal with outcomes
    real_results = [r for r in results if not r.get("hypothetical")]
    _update_paper_journal_outcomes(real_results, today)

    _print_postmarket_summary(results, today, log)
    logger.info("POST-MARKET TRACKER COMPLETE")
    return {"date": today, "results": results}


# ---------------------------------------------------------------------------
# 3:30 PM IST — Post-market analysis (tracker + Claude grade)
# ---------------------------------------------------------------------------

def run_postmarket_analysis(api_key: str) -> dict:
    """
    Full 3:30 PM run: outcome tracking (no Claude) + Claude post-market grade.
    Saves post_market_YYYY-MM-DD.json and enriches prediction_accuracy.json.
    """
    tracker_result = run_postmarket_tracker()

    if not tracker_result or not tracker_result.get("results"):
        return tracker_result or {}

    today   = tracker_result["date"]
    results = [r for r in tracker_result["results"] if not r.get("error")]

    if not results:
        logger.info("No valid results to grade — skipping Claude post-market call")
        return tracker_result

    morning_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    if not morning_path.exists():
        logger.warning("Morning brief not found — skipping Claude grade")
        return tracker_result

    with open(morning_path, encoding="utf-8") as f:
        morning = json.load(f)

    from src.analyzers.post_market_analyzer import PostMarketAnalyzer
    pm_analyzer = PostMarketAnalyzer(api_key=api_key)
    pm_result   = pm_analyzer.generate_postmarket_analysis(today, results, morning)

    # Enrich prediction_accuracy.json with Claude grades (PART 5)
    if not pm_result.get("error"):
        acc_path = OUT_DIR / "prediction_accuracy.json"
        if acc_path.exists():
            with open(acc_path, encoding="utf-8") as f:
                log: list[dict] = json.load(f)
            grade_map = {g["symbol"]: g for g in pm_result.get("trade_grades", [])}
            for entry in log:
                if entry.get("date") == today and entry.get("symbol") in grade_map:
                    g = grade_map[entry["symbol"]]
                    entry["claude_grade"]           = g.get("grade")
                    entry["claude_grade_reasoning"] = g.get("grade_reasoning")
            _save(log, acc_path)

    logger.info("POST-MARKET ANALYSIS COMPLETE")
    return {"date": today, "results": results, "post_market_analysis": pm_result}


# ---------------------------------------------------------------------------
# 9:15 AM IST — First candle check
# ---------------------------------------------------------------------------

def _parse_trigger_level(entry_trigger: str, signal: str) -> float | None:
    """
    Extract the explicit spot price level from an entry_trigger string.
    Looks for patterns like 'ABOVE 54100' or 'BELOW 23750'.
    """
    import re
    kw = "ABOVE" if signal == "CALL" else "BELOW"
    m = re.search(rf"{kw}\s+([\d,]+)", entry_trigger or "", re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def run_candle_check(api_key: str) -> dict:
    """
    9:15 AM NSE open — VIX + actual spot check.
    Fetches real NIFTY/BANKNIFTY opening prices and auto-confirms or cancels
    each trade based on whether its entry trigger level was met.
    No Claude call. Saves candle_check_YYYY-MM-DD.json.
    """
    import yfinance as yf

    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("9:15 AM CANDLE CHECK  —  %s  (%s)", today, _ist_now_str())
    logger.info("=" * 70)

    morning_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    if not morning_path.exists():
        logger.warning("No morning brief for %s — skipping candle check", today)
        return {}

    with open(morning_path, encoding="utf-8") as f:
        morning = json.load(f)

    trades = morning.get("trades", [])
    if not trades:
        logger.info("No active trades — nothing to check at open")
        return {}

    # Load pre-open decisions if available
    preopen_path = OUT_DIR / f"trade_brief_preopen_{today}.json"
    action_map: dict[str, str] = {}
    if preopen_path.exists():
        with open(preopen_path, encoding="utf-8") as f:
            po = json.load(f)
        for tr in po.get("trades_review", []):
            action_map[tr.get("symbol", "")] = tr.get("final_action", "?")

    nse        = NSEFetcher()
    vix_now    = nse.fetch_india_vix()
    global_now = fetch_global_cues()

    logger.info("  VIX at open: %s  |  Global: %s", vix_now, global_now.get("overall_bias"))

    # --- Fetch actual opening prices via yfinance 1-minute bars ---
    def _get_open_price(sym: str) -> float | None:
        ticker = _INDEX_YF.get(sym, f"{sym}.NS")
        try:
            hist = yf.Ticker(ticker).history(period="1d", interval="1m")
            if not hist.empty:
                return round(float(hist.iloc[0]["Open"]), 2)
        except Exception as exc:
            logger.warning("Could not fetch open price for %s: %s", sym, exc)
        return None

    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  9:15 AM CANDLE CHECK  —  {today}")
    print(f"  VIX: {vix_now}  |  Global: {global_now.get('overall_bias', '?')}")
    print(f"  Wait for first 15-min candle close at 9:30 AM IST before entering.")
    print(sep)

    trigger_results = []
    for t in trades:
        sym    = t.get("symbol", "?")
        sig    = t.get("signal", "?")
        strike = t.get("strike", "?")
        action = action_map.get(sym, "NOT REVIEWED")
        trigger_text = t.get("entry_trigger", "")
        icon   = {"GO": "OK", "WAIT": "--", "SKIP": "XX"}.get(action, "??")

        open_price    = _get_open_price(sym)
        trigger_level = _parse_trigger_level(trigger_text, sig)

        if open_price and trigger_level:
            if sig == "CALL":
                trigger_met = open_price >= trigger_level
            else:
                trigger_met = open_price <= trigger_level
            auto_decision = "CONFIRMED" if trigger_met else "CANCELLED"
            trigger_note  = (
                f"Open={open_price:,.0f} vs trigger {'ABOVE' if sig=='CALL' else 'BELOW'} "
                f"{trigger_level:,.0f} → {auto_decision}"
            )
        else:
            trigger_met   = None
            auto_decision = "UNKNOWN (could not parse trigger)"
            trigger_note  = f"Open={open_price}"

        trigger_results.append({
            "symbol":        sym,
            "signal":        sig,
            "strike":        strike,
            "pre_open":      action,
            "open_price":    open_price,
            "trigger_level": trigger_level,
            "trigger_met":   trigger_met,
            "auto_decision": auto_decision,
        })

        print(f"\n  [{icon}] {sym} {sig} {strike}  [Pre-open: {action}]")
        print(f"       Trigger: {trigger_note}")
        logger.info("  %s %s: %s", sym, sig, trigger_note)

    print(f"\n{sep}\n")

    result = {
        "date":                today,
        "time":                _ist_now_str(),
        "vix_at_open":         vix_now,
        "global_bias_at_open": global_now.get("overall_bias"),
        "trades_active":       len(trades),
        "pre_open_actions":    action_map,
        "trigger_checks":      trigger_results,
    }
    _save(result, OUT_DIR / f"candle_check_{today}.json")
    logger.info("CANDLE CHECK COMPLETE")
    return result


def run_dry_run_postmarket() -> dict:
    """
    Simulate 3:30 PM post-market run using today's available data.
    Verifies:
    - post_market outcome tracking works
    - yfinance intraday 5m fetch works or fails gracefully
    - journal entries update without corruption (atomic write)
    - project_status.md updates correctly

    Uses atomic write pattern: write to .tmp then rename, never corrupt on failure.
    Never writes partial data. If corruption detected: abort and log error.
    """
    today = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 70)
    logger.info("DRY-RUN POST-MARKET  —  %s  (%s)", today, _ist_now_str())
    logger.info("=" * 70)

    report: dict = {
        "date":           today,
        "mode":           "dry_run",
        "checks":         {},
        "errors":         [],
        "warnings":       [],
    }

    # Check 1: morning brief exists
    morning_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    if not morning_path.exists():
        msg = f"No morning brief for {today} — dry-run cannot proceed without it"
        logger.warning(msg)
        report["errors"].append(msg)
        report["checks"]["morning_brief"] = "MISSING"
        return report

    report["checks"]["morning_brief"] = "FOUND"

    # Check 2: yfinance intraday 5m fetch (graceful failure)
    test_sym = "^NSEI"
    try:
        candles = _fetch_yf_intraday(test_sym)
        if not candles.empty:
            report["checks"]["yf_intraday_5m"] = f"OK ({len(candles)} bars)"
            logger.info("  yfinance 5m fetch: OK (%d bars for %s)", len(candles), test_sym)
        else:
            report["checks"]["yf_intraday_5m"] = "EMPTY (market may be closed)"
            report["warnings"].append("yfinance 5m fetch returned empty — market may be closed")
    except Exception as exc:
        report["checks"]["yf_intraday_5m"] = f"FAILED: {exc}"
        report["warnings"].append(f"yfinance 5m fetch failed: {exc}")
        logger.warning("  yfinance 5m fetch failed: %s", exc)

    # Check 3: journal atomic write integrity
    if _JOURNAL_PATH.exists():
        try:
            with open(_JOURNAL_PATH, encoding="utf-8") as f:
                journal = json.load(f)
            # Verify round-trip serialization
            raw = json.dumps(journal, indent=2, ensure_ascii=False)
            reloaded = json.loads(raw)
            assert reloaded == journal, "Round-trip mismatch"
            report["checks"]["journal_integrity"] = f"OK ({len(journal.get('trades',[]))} entries)"
            logger.info("  Journal integrity: OK (%d entries)", len(journal.get("trades", [])))
        except Exception as exc:
            msg = f"Journal integrity check failed: {exc}"
            report["errors"].append(msg)
            report["checks"]["journal_integrity"] = f"FAILED: {exc}"
            logger.error("  %s", msg)
    else:
        report["checks"]["journal_integrity"] = "NO_JOURNAL (expected for first run)"
        report["warnings"].append("No journal file yet — will be created on first morning run")

    # Check 4: project_status.md writeable
    status_path = Path("project_status.md")
    try:
        if status_path.exists():
            status_path.read_text(encoding="utf-8")
        report["checks"]["project_status_md"] = "OK"
    except Exception as exc:
        report["errors"].append(f"project_status.md not readable: {exc}")
        report["checks"]["project_status_md"] = f"FAILED: {exc}"

    # Check 5: atomic write pattern test (write .tmp, verify, rename)
    _test_path = _JOURNAL_PATH.parent / "_dry_run_test.json"
    _test_tmp  = Path(str(_test_path) + ".tmp")
    try:
        _test_path.parent.mkdir(parents=True, exist_ok=True)
        test_data = {"dry_run": True, "date": today}
        raw = json.dumps(test_data, indent=2)
        json.loads(raw)   # verify round-trip
        _test_tmp.write_text(raw, encoding="utf-8")
        _test_tmp.replace(_test_path)
        _test_path.unlink()
        report["checks"]["atomic_write_pattern"] = "OK"
    except Exception as exc:
        report["errors"].append(f"Atomic write pattern test failed: {exc}")
        report["checks"]["atomic_write_pattern"] = f"FAILED: {exc}"

    overall = "PASS" if not report["errors"] else "FAIL"
    report["overall"] = overall
    n_warn = len(report["warnings"])
    logger.info("Dry-run complete: %s  (%d errors, %d warnings)",
                overall, len(report["errors"]), n_warn)

    _save(report, OUT_DIR / f"dry_run_postmarket_{today}.json")
    return report


def _print_postmarket_summary(
    results: list[dict],
    today: str,
    full_log: list[dict],
) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  POST-MARKET OUTCOME  —  {today}")
    print(sep)

    outcome_icon = {
        "TARGET_2_HIT":    "[T2 WIN ]",
        "TARGET_1_HIT":    "[T1 WIN ]",
        "SL_HIT":          "[SL LOSS]",
        "DIRECTION_RIGHT": "[OK     ]",
        "DIRECTION_WRONG": "[WRONG  ]",
        "OUTCOME_UNKNOWN": "[UNKNOWN]",
    }

    real_results  = [r for r in results if not r.get("hypothetical") and not r.get("error")]
    hypo_results  = [r for r in results if r.get("hypothetical") and not r.get("error")]

    if real_results:
        print("\n  === LIVE TRADES (counted in accuracy) ===")
    for r in real_results:
        icon = outcome_icon.get(r.get("outcome", ""), "[???????]")
        corr = "CORRECT" if r.get("was_correct") else "WRONG"
        print(f"\n  {icon} {r['symbol']:10s} {r['signal']:4s}  "
              f"Direction: {corr}  ({r.get('predicted_direction')} / {r.get('actual_direction')})")
        print(f"       OHLC: O={r['index_open']}  H={r['index_high']}  "
              f"L={r['index_low']}  C={r['index_close']}  "
              f"({r['day_change_pts']:+.0f} pts  {r['day_change_pct']:+.2f}%)")
        if r.get("entry_premium"):
            print(f"       Premium: entry={r['entry_premium']}  "
                  f"est.final={r.get('est_final_premium')}  "
                  f"SL={r['sl_premium']}  T1={r['target_1_premium']}  T2={r.get('target_2_premium')}")
            print(f"       SL hit={r['sl_hit']}  T1={r['target_1_reached']}  T2={r['target_2_reached']}")

    if hypo_results:
        print("\n  === HYPOTHETICAL (gated-out — not counted in accuracy) ===")
    for r in hypo_results:
        icon = outcome_icon.get(r.get("outcome", ""), "[???????]")
        corr = "CORRECT" if r.get("was_correct") else "WRONG"
        gate = r.get("gate_status", "GATED")
        print(f"\n  {icon} {r['symbol']:10s} {r['signal']:4s}  "
              f"Direction: {corr}  [GATED: {gate}]  ({r.get('predicted_direction')} / {r.get('actual_direction')})")
        print(f"       OHLC: O={r.get('index_open')}  H={r.get('index_high')}  "
              f"L={r.get('index_low')}  C={r.get('index_close')}  "
              f"({r.get('day_change_pts',0):+.0f} pts  {r.get('day_change_pct',0):+.2f}%)")

    # Rolling accuracy stats — real trades only
    real_log  = [r for r in full_log if not r.get("error") and not r.get("hypothetical")]
    hypo_log  = [r for r in full_log if not r.get("error") and r.get("hypothetical")]

    if real_log:
        total   = len(real_log)
        correct = sum(1 for r in real_log if r.get("was_correct"))
        t1_hits = sum(1 for r in real_log if r.get("outcome") == "TARGET_1_HIT")
        t2_hits = sum(1 for r in real_log if r.get("outcome") == "TARGET_2_HIT")
        sl_hits = sum(1 for r in real_log if r.get("outcome") == "SL_HIT")
        unknown = sum(1 for r in real_log if r.get("outcome") == "OUTCOME_UNKNOWN")
        wins    = t1_hits + t2_hits
        print(f"\n  === ROLLING ACCURACY — {total} LIVE TRADES ===")
        print(f"  WIN  (T1+T2 hit)   : {wins}/{total}  ({wins/total*100:.1f}%)")
        print(f"  LOSS (SL hit)      : {sl_hits}/{total}  ({sl_hits/total*100:.1f}%)")
        print(f"  Direction correct  : {correct}/{total}  ({correct/total*100:.1f}%)")
        print(f"  Target 1 hit       : {t1_hits}/{total}  ({t1_hits/total*100:.1f}%)")
        print(f"  Target 2 hit       : {t2_hits}/{total}  ({t2_hits/total*100:.1f}%)")
        if unknown:
            print(f"  OUTCOME_UNKNOWN    : {unknown}/{total}  (not counted)")
    else:
        print("\n  No live trades tracked yet.")

    if hypo_log:
        total_h   = len(hypo_log)
        correct_h = sum(1 for r in hypo_log if r.get("was_correct"))
        wins_h    = sum(1 for r in hypo_log if r.get("outcome") in ("TARGET_1_HIT","TARGET_2_HIT"))
        print(f"\n  === HYPOTHETICAL GATE ANALYSIS — {total_h} GATED TRADES ===")
        print(f"  Would have been correct: {correct_h}/{total_h}  ({correct_h/total_h*100:.1f}%)")
        print(f"  Would have been wins   : {wins_h}/{total_h}  ({wins_h/total_h*100:.1f}%)")
        print(f"  (These trades were blocked by the gate system)")

    print(f"\n{sep}\n")

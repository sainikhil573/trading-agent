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

    # Load yesterday's post-market memory (look back up to 7 days)
    yesterday_memory: dict | None = None
    for i in range(1, 8):
        past = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        pm_path = OUT_DIR / f"post_market_{past}.json"
        if pm_path.exists():
            with open(pm_path, encoding="utf-8") as f:
                yesterday_memory = json.load(f)
            logger.info("  Loaded market memory from %s", pm_path)
            break

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
    }

    out_path = OUT_DIR / f"trade_brief_morning_{today}.json"
    _save(brief, out_path)

    _print_morning_brief(brief, today)
    logger.info("MORNING ANALYSIS COMPLETE")
    return brief


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

    trades = morning.get("trades", [])
    if not trades:
        logger.info("No recommended trades in morning brief — skipping tracker")
        return {}

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

            sl_hit       = bool(worst_premium and sl_premium and worst_premium <= sl_premium)
            t1_reached   = bool(best_premium  and t1_premium  and best_premium  >= t1_premium)
            t2_reached   = bool(best_premium  and t2_premium  and best_premium  >= t2_premium)

            # T1/T2 only credited when overall direction was correct.
            # When direction is wrong, OHLC extremes can show T1 "reached" via a
            # temporary intraday bounce — but the adverse move likely hit SL first.
            # Conservative rule: SL_HIT takes priority over T1 on directionally wrong calls.
            path_ambiguous = bool(sl_hit and (t1_reached or t2_reached) and not was_correct)
            outcome = (
                "TARGET_2_HIT"    if t2_reached and was_correct else
                "TARGET_1_HIT"    if t1_reached and was_correct else
                "SL_HIT"          if sl_hit else
                "DIRECTION_RIGHT" if was_correct else
                "DIRECTION_WRONG"
            )

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
                "path_note": (
                    "OHLC-only tracking: SL and target both triggered via day H/L extremes "
                    "— intraday sequence unknown; SL_HIT assumed (direction was wrong)"
                    if path_ambiguous else None
                ),
                "note":                "Premium estimates use ATM delta=0.5 approximation",
            })

        except Exception as exc:
            logger.warning("Post-market fetch failed for %s: %s", sym, exc)
            results.append({"symbol": sym, "date": today, "error": str(exc)})

    # Append to rolling accuracy log (one entry per trade per day)
    acc_path = OUT_DIR / "prediction_accuracy.json"
    log: list[dict] = []
    if acc_path.exists():
        with open(acc_path, encoding="utf-8") as f:
            log = json.load(f)

    # Replace any existing entries for today (idempotent re-runs)
    log = [r for r in log if r.get("date") != today]
    log.extend(results)
    _save(log, acc_path)

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

def run_candle_check(api_key: str) -> dict:
    """
    9:15 AM NSE open — lightweight VIX + spot check.
    Logs whether entry triggers from morning brief are still valid.
    No new Claude call. Saves candle_check_YYYY-MM-DD.json.
    """
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

    nse     = NSEFetcher()
    vix_now = nse.fetch_india_vix()
    global_now = fetch_global_cues()

    logger.info("  VIX at open: %s  |  Global: %s", vix_now, global_now.get("overall_bias"))

    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  9:15 AM CANDLE CHECK  —  {today}")
    print(f"  VIX: {vix_now}  |  Global: {global_now.get('overall_bias', '?')}")
    print(f"  Wait for first 15-min candle close at 9:30 AM IST before entering.")
    print(sep)

    for t in trades:
        sym    = t.get("symbol", "?")
        sig    = t.get("signal", "?")
        strike = t.get("strike", "?")
        action = action_map.get(sym, "NOT REVIEWED")
        trigger = t.get("entry_trigger", "Check morning brief")
        icon = {"GO": "OK", "WAIT": "--", "SKIP": "XX"}.get(action, "??")
        print(f"\n  [{icon}] {sym} {sig} {strike}  [Pre-open: {action}]")
        print(f"       Trigger: {trigger}")

    print(f"\n{sep}\n")

    result = {
        "date":                  today,
        "time":                  _ist_now_str(),
        "vix_at_open":           vix_now,
        "global_bias_at_open":   global_now.get("overall_bias"),
        "trades_active":         len(trades),
        "pre_open_actions":      action_map,
    }
    _save(result, OUT_DIR / f"candle_check_{today}.json")
    logger.info("CANDLE CHECK COMPLETE")
    return result


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
        "TARGET_2_HIT":    "[T2]",
        "TARGET_1_HIT":    "[T1]",
        "SL_HIT":          "[SL]",
        "DIRECTION_RIGHT": "[OK]",
        "DIRECTION_WRONG": "[XX]",
    }

    for r in results:
        if r.get("error"):
            print(f"\n  {r['symbol']:12s}  ERROR: {r['error']}")
            continue
        icon = outcome_icon.get(r.get("outcome", ""), "[??]")
        corr = "CORRECT" if r.get("was_correct") else "WRONG"
        print(f"\n  {icon} {r['symbol']:10s} {r['signal']:4s}  "
              f"Direction: {corr}  ({r.get('predicted_direction')} predicted / {r.get('actual_direction')} actual)")
        print(f"       Index: O={r['index_open']}  H={r['index_high']}  "
              f"L={r['index_low']}  C={r['index_close']}  "
              f"({r['day_change_pts']:+.0f} pts  {r['day_change_pct']:+.2f}%)")
        if r.get("entry_premium"):
            print(f"       Premium: entry={r['entry_premium']}  "
                  f"est.final={r.get('est_final_premium')}  "
                  f"SL={r['sl_premium']}  T1={r['target_1_premium']}  T2={r.get('target_2_premium')}")
            print(f"       SL hit={r['sl_hit']}  T1 reached={r['target_1_reached']}  "
                  f"T2 reached={r['target_2_reached']}")

    # Rolling accuracy stats across the full log
    valid = [r for r in full_log if not r.get("error")]
    if valid:
        correct  = sum(1 for r in valid if r.get("was_correct"))
        t1_hits  = sum(1 for r in valid if r.get("target_1_reached"))
        t2_hits  = sum(1 for r in valid if r.get("target_2_reached"))
        sl_hits  = sum(1 for r in valid if r.get("sl_hit"))
        total    = len(valid)
        print(f"\n  --- ROLLING ACCURACY ({total} trades tracked) ---")
        print(f"  Direction correct : {correct}/{total}  ({correct/total*100:.1f}%)")
        print(f"  Target 1 hit      : {t1_hits}/{total}  ({t1_hits/total*100:.1f}%)")
        print(f"  Target 2 hit      : {t2_hits}/{total}  ({t2_hits/total*100:.1f}%)")
        print(f"  SL hit            : {sl_hits}/{total}  ({sl_hits/total*100:.1f}%)")

    print(f"\n{sep}\n")

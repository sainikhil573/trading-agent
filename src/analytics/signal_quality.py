"""
Signal quality analytics — computes per-trade quality metrics from paper journal.

For each journal trade:
  - time_to_sl    : minutes from market open to SL hit (if hit)
  - time_to_t1    : minutes from market open to T1 hit (if hit)
  - best_case_pnl : max intraday premium gain (positive = gain)
  - worst_case_pnl: max intraday premium loss (negative = loss)
  - outcome_quality: CLEAN_WIN / SCRATCHED / STOPPED_OUT / PENDING / OUTCOME_UNKNOWN

Uses yfinance 5m candles of the underlying stock + ATM delta (~0.5) to
estimate option premium movement when intraday option data is unavailable.

Output: data/analytics/signal_quality.json
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

JOURNAL_PATH  = Path("data/paper_trades/journal.json")
OUTPUT_PATH   = Path("data/analytics/signal_quality.json")
MARKET_OPEN   = (9, 15)   # NSE open (hour, minute) IST
ATM_DELTA     = 0.5        # crude ATM delta for premium estimation
MIN_WIN_RATIO = 0.10       # <10% of SL distance = scratched exit


def _fetch_5m_candles(symbol: str, trade_date: str) -> "Optional[object]":
    """Return a yfinance 5m DataFrame for the given symbol on trade_date."""
    try:
        import yfinance as yf
        import pandas as pd

        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
        start = d
        end   = d + timedelta(days=1)
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(start=str(start), end=str(end), interval="5m")
        if hist.empty:
            return None
        df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index = pd.to_datetime(df.index)
        df = df.reset_index().rename(columns={"index": "datetime", "Datetime": "datetime"})
        if "datetime" not in df.columns and df.index.name:
            df = df.reset_index()
        return df
    except Exception as exc:
        logger.debug("5m fetch failed for %s on %s: %s", symbol, trade_date, exc)
        return None


def _minutes_from_open(ts: datetime) -> int:
    """Minutes from 9:15 AM to ts (ignoring date)."""
    open_dt = ts.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    delta   = ts - open_dt
    return max(0, int(delta.total_seconds() / 60))


def _compute_quality(trade: dict, candles) -> dict:
    """
    Derive signal quality metrics for one trade using intraday candles.
    candles is a DataFrame or None.
    """
    import pandas as pd

    symbol    = trade.get("symbol", "?")
    signal    = trade.get("signal", "CALL").upper()
    status    = trade.get("status", "OPEN").upper()
    outcome   = trade.get("outcome")
    entry_p   = trade.get("entry_price")
    sl_p      = trade.get("stop_loss")
    t1_p      = trade.get("target_1")
    t2_p      = trade.get("target_2")

    base = {
        "symbol":        symbol,
        "date":          trade.get("date"),
        "signal":        signal,
        "status":        status,
        "confidence":    trade.get("confidence"),
        "confidence_tier": trade.get("confidence_tier"),
        "entry_price":   entry_p,
        "stop_loss":     sl_p,
        "target_1":      t1_p,
        "target_2":      t2_p,
        "time_to_sl":    None,
        "time_to_t1":    None,
        "best_case_pnl": None,
        "worst_case_pnl": None,
        "outcome_quality": "PENDING",
        "intraday_available": False,
    }

    if status == "OPEN" and outcome is None:
        base["outcome_quality"] = "PENDING"
        # Still try to compute best/worst case from today's data
        if candles is not None and not candles.empty and entry_p is not None:
            entry_spot = float(candles.iloc[0]["open"])
            best_pnl = worst_pnl = 0.0
            for _, row in candles.iterrows():
                if signal == "CALL":
                    pnl_high = (float(row["high"]) - entry_spot) * ATM_DELTA
                    pnl_low  = (float(row["low"])  - entry_spot) * ATM_DELTA
                else:
                    pnl_high = (entry_spot - float(row["low"]))  * ATM_DELTA
                    pnl_low  = (entry_spot - float(row["high"])) * ATM_DELTA
                best_pnl  = max(best_pnl,  pnl_high)
                worst_pnl = min(worst_pnl, pnl_low)
            base["best_case_pnl"]  = round(best_pnl,  2)
            base["worst_case_pnl"] = round(worst_pnl, 2)
            base["intraday_available"] = True
        return base

    # For CLOSED trades
    known_outcomes = {"TARGET_1_HIT", "TARGET_2_HIT", "SL_HIT",
                      "DIRECTION_RIGHT", "DIRECTION_WRONG", "OUTCOME_UNKNOWN"}
    outcome_str = str(outcome).upper() if outcome else "OUTCOME_UNKNOWN"

    if outcome_str == "SL_HIT":
        base["outcome_quality"] = "STOPPED_OUT"
    elif outcome_str in ("TARGET_1_HIT", "TARGET_2_HIT"):
        base["outcome_quality"] = "CLEAN_WIN"
    elif outcome_str == "DIRECTION_RIGHT":
        # Closed profitable but didn't formally log T1/T2 — treat as scratched win
        base["outcome_quality"] = "SCRATCHED"
    elif outcome_str in ("DIRECTION_WRONG", "OUTCOME_UNKNOWN"):
        base["outcome_quality"] = "OUTCOME_UNKNOWN"
    else:
        base["outcome_quality"] = "OUTCOME_UNKNOWN"

    if candles is None or candles.empty or entry_p is None:
        return base

    # Walk candles to find time_to_sl, time_to_t1, best/worst pnl
    base["intraday_available"] = True
    entry_spot  = float(candles.iloc[0]["open"])
    best_pnl    = 0.0
    worst_pnl   = 0.0
    sl_time: Optional[datetime] = None
    t1_time: Optional[datetime] = None

    for _, row in candles.iterrows():
        ts_raw = row.get("datetime", row.name)
        try:
            ts = pd.Timestamp(ts_raw).to_pydatetime()
        except Exception:
            continue

        if signal == "CALL":
            pnl_high = (float(row["high"]) - entry_spot) * ATM_DELTA
            pnl_low  = (float(row["low"])  - entry_spot) * ATM_DELTA
        else:
            pnl_high = (entry_spot - float(row["low"]))  * ATM_DELTA
            pnl_low  = (entry_spot - float(row["high"])) * ATM_DELTA

        est_best  = entry_p + pnl_high
        est_worst = entry_p + pnl_low

        if sl_time is None and sl_p is not None and est_worst <= sl_p:
            sl_time = ts
        if t1_time is None and t1_p is not None and est_best >= t1_p:
            t1_time = ts

        best_pnl  = max(best_pnl,  pnl_high)
        worst_pnl = min(worst_pnl, pnl_low)

    base["best_case_pnl"]  = round(best_pnl,  2)
    base["worst_case_pnl"] = round(worst_pnl, 2)

    if sl_time:
        base["time_to_sl"] = _minutes_from_open(sl_time)
    if t1_time:
        base["time_to_t1"] = _minutes_from_open(t1_time)

    # Refine scratched: if pnl is tiny relative to SL distance
    if base["outcome_quality"] == "SCRATCHED" and sl_p and entry_p:
        sl_dist = abs(entry_p - sl_p)
        if sl_dist > 0 and abs(best_pnl) < sl_dist * MIN_WIN_RATIO:
            base["outcome_quality"] = "SCRATCHED"

    return base


def run_signal_quality(
    journal_path: Path = JOURNAL_PATH,
    output_path:  Path = OUTPUT_PATH,
) -> dict:
    """
    Process all journal trades and save signal quality report.
    Returns the full quality dict.
    """
    if not journal_path.exists():
        logger.warning("Journal not found: %s", journal_path)
        return {"trades": [], "error": "Journal not found"}

    with open(journal_path, encoding="utf-8") as f:
        journal = json.load(f)

    trades = journal.get("trades", [])
    quality_records = []

    for trade in trades:
        sym        = trade.get("symbol", "")
        trade_date = trade.get("date", "")
        candles    = None
        if sym and trade_date:
            candles = _fetch_5m_candles(sym, trade_date)

        record = _compute_quality(trade, candles)
        quality_records.append(record)
        logger.info(
            "Signal quality: %s %s %s -> %s (best_pnl=%s, worst_pnl=%s)",
            trade_date, sym, record.get("signal"),
            record.get("outcome_quality"),
            record.get("best_case_pnl"),
            record.get("worst_case_pnl"),
        )

    # Summary stats
    pending  = sum(1 for r in quality_records if r["outcome_quality"] == "PENDING")
    wins     = sum(1 for r in quality_records if r["outcome_quality"] == "CLEAN_WIN")
    stopped  = sum(1 for r in quality_records if r["outcome_quality"] == "STOPPED_OUT")
    scratched= sum(1 for r in quality_records if r["outcome_quality"] == "SCRATCHED")
    unknown  = sum(1 for r in quality_records if r["outcome_quality"] == "OUTCOME_UNKNOWN")
    closed   = wins + stopped + scratched + unknown

    result = {
        "generated": date.today().strftime("%Y-%m-%d"),
        "total_trades": len(quality_records),
        "summary": {
            "PENDING":         pending,
            "CLEAN_WIN":       wins,
            "STOPPED_OUT":     stopped,
            "SCRATCHED":       scratched,
            "OUTCOME_UNKNOWN": unknown,
            "closed_total":    closed,
            "win_rate_pct":    round(wins / closed * 100, 1) if closed else None,
        },
        "trades": quality_records,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("Signal quality saved: %s (%d trades)", output_path, len(quality_records))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_signal_quality()
    print(json.dumps(result["summary"], indent=2))

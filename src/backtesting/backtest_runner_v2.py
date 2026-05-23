"""
Backtest v2 — conservative RSI bounds filter (diagnostic only).

Signal rules vs v1:
  CALL: price > EMA20, RSI in (50, 70), vol_ratio >= 0.4
  PUT:  price < EMA20, RSI in (30, 50), vol_ratio >= 0.4

EMA50 alignment NOT required — wider net to test RSI range impact.
Saves to data/backtesting/results_60d_v2.json.

Usage:
    python -m src.backtesting.backtest_runner_v2
    python -m src.backtesting.backtest_runner_v2 --days 30
"""

from __future__ import annotations
import argparse
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.backtesting.backtest_runner import _ema, _rsi, FNO_UNIVERSE

logger  = logging.getLogger(__name__)
OUT_DIR = Path("data/backtesting")
OUT_V2  = OUT_DIR / "results_60d_v2.json"


def _derive_signal_v2(row: pd.Series) -> str:
    """
    Conservative signal: RSI bounds tighter (no overbought/oversold entries),
    lower volume hurdle (0.4 instead of 0.5), EMA50 alignment dropped.
    CALL: price > EMA20, 50 < RSI < 70, vol_ratio >= 0.4
    PUT:  price < EMA20, 30 < RSI < 50, vol_ratio >= 0.4
    """
    vol_ok   = row.get("vol_ratio", 1.0) >= 0.4
    rsi      = row["rsi"]
    call_sig = row["close"] > row["ema20"] and 50 < rsi < 70 and vol_ok
    put_sig  = row["close"] < row["ema20"] and 30 < rsi < 50 and vol_ok
    if call_sig:
        return "CALL"
    if put_sig:
        return "PUT"
    return "NEUTRAL"


def _backtest_symbol_v2(symbol: str, days: int = 60) -> dict:
    ticker = f"{symbol}.NS"
    try:
        hist = yf.Ticker(ticker).history(period=f"{days + 10}d", interval="1d")
        if hist.empty or len(hist) < 22:
            return {"symbol": symbol, "error": "Insufficient data"}

        df = hist[["Close", "Volume"]].copy()
        df.columns = ["close", "volume"]
        df["ema20"]     = _ema(df["close"], 20)
        df["ema50"]     = _ema(df["close"], 50)
        df["rsi"]       = _rsi(df["close"])
        df["vol_avg20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_avg20"].replace(0, float("nan"))

        df = df.dropna()
        df["signal"]     = df.apply(_derive_signal_v2, axis=1)
        df["next_close"] = df["close"].shift(-1)
        df["next_open"]  = hist["Open"].shift(-1).reindex(df.index)
        df = df[:-1]

        records = []
        for idx, row in df.iterrows():
            actual_dir = "UP" if row["next_close"] >= row["next_open"] else "DOWN"
            pred_dir   = ("UP" if row["signal"] == "CALL" else
                          ("DOWN" if row["signal"] == "PUT" else "NEUTRAL"))
            correct    = (pred_dir != "NEUTRAL") and (pred_dir == actual_dir)
            records.append({
                "date":          idx.strftime("%Y-%m-%d"),
                "signal":        row["signal"],
                "predicted_dir": pred_dir,
                "actual_dir":    actual_dir,
                "was_correct":   correct,
                "rsi":           round(float(row["rsi"]), 1),
                "vol_ratio":     round(float(row["vol_ratio"]), 2),
            })

        active  = [r for r in records if r["signal"] != "NEUTRAL"]
        correct = [r for r in active if r["was_correct"]]
        return {
            "symbol":        symbol,
            "days_analysed": len(records),
            "signals_taken": len(active),
            "correct":       len(correct),
            "accuracy_pct":  round(len(correct) / len(active) * 100, 1) if active else 0.0,
            "records":       records,
        }
    except Exception as exc:
        logger.warning("Backtest v2 failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


def run_backtest_v2(symbols: list[str] | None = None, days: int = 60) -> dict:
    """Run v2 backtest; saves to results_60d_v2.json."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    targets = symbols or FNO_UNIVERSE
    logger.info("Backtest v2: %d symbols, %d days, conservative RSI bounds", len(targets), days)

    results = []
    for sym in targets:
        logger.info("  %s ...", sym)
        results.append(_backtest_symbol_v2(sym, days))

    valid   = [r for r in results if "error" not in r]
    total_s = sum(r["signals_taken"] for r in valid)
    total_c = sum(r["correct"]       for r in valid)

    summary = {
        "run_date":          date.today().strftime("%Y-%m-%d"),
        "version":           "v2",
        "signal_rules":      {
            "CALL": "price > EMA20, 50 < RSI < 70, vol_ratio >= 0.4",
            "PUT":  "price < EMA20, 30 < RSI < 50, vol_ratio >= 0.4",
        },
        "days_requested":    days,
        "symbols_requested": len(targets),
        "symbols_ok":        len(valid),
        "total_signals":     total_s,
        "total_correct":     total_c,
        "overall_accuracy":  round(total_c / total_s * 100, 1) if total_s else 0.0,
        "per_symbol":        results,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_V2, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Backtest v2 complete: %d/%d correct (%.1f%%) -> %s",
                total_c, total_s, summary["overall_accuracy"], OUT_V2)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest v2 — conservative RSI bounds")
    parser.add_argument("--symbol", nargs="*", help="Symbols to test (default: full universe)")
    parser.add_argument("--days",   type=int, default=60, help="Days of history (default: 60)")
    args = parser.parse_args()
    run_backtest_v2(args.symbol, args.days)

"""
Backtesting runner — rule-based only (no Claude API).
Loads 60-day yfinance OHLCV for each F&O universe stock,
derives CALL/PUT/NEUTRAL signal per day, compares to next-day direction.
Saves results to data/backtesting/results.json.

Usage:
    python -m src.backtesting.backtest_runner
    python -m src.backtesting.backtest_runner --symbol WIPRO --days 30
"""

from __future__ import annotations
import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

OUT_DIR = Path("data/backtesting")

FNO_UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "AXISBANK", "KOTAKBANK", "SBIN", "TATASTEEL", "HINDALCO",
    "ONGC", "BPCL", "MARUTI", "BAJFINANCE", "TATAMOTORS",
    "WIPRO", "SUNPHARMA", "DRREDDY", "ADANIENT", "LT",
]


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _derive_signal(row: pd.Series) -> str:
    """
    Rule-based signal using EMA20, EMA50, RSI14, and volume ratio.
    CALL  — price > EMA20 > EMA50, RSI > 55, vol/avg > 0.5
    PUT   — price < EMA20 < EMA50, RSI < 45, vol/avg > 0.5
    NEUTRAL otherwise.
    """
    bullish = (
        row["close"] > row["ema20"] and
        row["ema20"] > row["ema50"] and
        row["rsi"] > 55 and
        row.get("vol_ratio", 1.0) >= 0.5
    )
    bearish = (
        row["close"] < row["ema20"] and
        row["ema20"] < row["ema50"] and
        row["rsi"] < 45 and
        row.get("vol_ratio", 1.0) >= 0.5
    )
    if bullish:
        return "CALL"
    if bearish:
        return "PUT"
    return "NEUTRAL"


def _backtest_symbol(symbol: str, days: int = 60) -> dict:
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

        # Signal on day N → compare to day N+1 direction
        df = df.dropna()
        df["signal"]    = df.apply(_derive_signal, axis=1)
        df["next_close"]= df["close"].shift(-1)
        df["next_open"] = hist["Open"].shift(-1).reindex(df.index)
        df = df[:-1]  # drop last row (no next-day data)

        records = []
        for idx, row in df.iterrows():
            actual_dir = "UP" if row["next_close"] >= row["next_open"] else "DOWN"
            pred_dir   = ("UP" if row["signal"] == "CALL" else
                          ("DOWN" if row["signal"] == "PUT" else "NEUTRAL"))
            correct    = (pred_dir != "NEUTRAL") and (pred_dir == actual_dir)
            records.append({
                "date":           idx.strftime("%Y-%m-%d"),
                "signal":         row["signal"],
                "predicted_dir":  pred_dir,
                "actual_dir":     actual_dir,
                "was_correct":    correct,
                "rsi":            round(float(row["rsi"]), 1),
                "ema20":          round(float(row["ema20"]), 2),
                "ema50":          round(float(row["ema50"]), 2),
                "vol_ratio":      round(float(row["vol_ratio"]), 2),
                "close":          round(float(row["close"]), 2),
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
        logger.warning("Backtest failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


def run_backtest(symbols: list[str] | None = None, days: int = 60) -> dict:
    """
    Run rule-based backtest for the given symbols (default: full FNO_UNIVERSE).
    Saves results to data/backtesting/results.json and returns the summary dict.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    targets = symbols or FNO_UNIVERSE
    logger.info("Backtesting %d symbols over %d days...", len(targets), days)

    results = []
    for sym in targets:
        logger.info("  %s ...", sym)
        results.append(_backtest_symbol(sym, days))

    valid   = [r for r in results if "error" not in r]
    total_s = sum(r["signals_taken"] for r in valid)
    total_c = sum(r["correct"]       for r in valid)

    summary = {
        "run_date":          date.today().strftime("%Y-%m-%d"),
        "days_requested":    days,
        "symbols_requested": len(targets),
        "symbols_ok":        len(valid),
        "total_signals":     total_s,
        "total_correct":     total_c,
        "overall_accuracy":  round(total_c / total_s * 100, 1) if total_s else 0.0,
        "per_symbol":        results,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Backtest complete: %d/%d signals correct (%.1f%%) -> %s",
                total_c, total_s, summary["overall_accuracy"], out_path)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rule-based F&O backtest runner")
    parser.add_argument("--symbol", nargs="*", help="Symbols to backtest (default: full universe)")
    parser.add_argument("--days",   type=int, default=60, help="Days of history (default: 60)")
    args = parser.parse_args()
    run_backtest(args.symbol, args.days)

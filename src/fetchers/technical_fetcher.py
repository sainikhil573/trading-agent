"""
Technical indicators for Nifty, BankNifty, and top F&O stocks.
OHLC and derived indicators (RSI-14, MACD, EMAs, Bollinger) via yfinance.
"""

from __future__ import annotations
import logging
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

INDEX_YF = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}


# ---------------------------------------------------------------------------
# Indicator math
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int = 14) -> float | None:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, float("inf"))
    rsi      = 100 - (100 / (1 + rs))
    v = rsi.iloc[-1]
    return round(float(v), 2) if pd.notna(v) else None


def _macd(close: pd.Series) -> dict:
    ema12   = close.ewm(span=12, adjust=False).mean()
    ema26   = close.ewm(span=26, adjust=False).mean()
    line    = ema12 - ema26
    signal  = line.ewm(span=9, adjust=False).mean()
    hist    = line - signal

    val     = round(float(line.iloc[-1]),   2)
    sig_val = round(float(signal.iloc[-1]), 2)
    h_val   = round(float(hist.iloc[-1]),   2)
    h_prev  = round(float(hist.iloc[-2]),   2) if len(hist) > 1 else 0.0

    crossover = None
    if h_val > 0 and h_prev <= 0:
        crossover = "BULLISH_CROSSOVER"
    elif h_val < 0 and h_prev >= 0:
        crossover = "BEARISH_CROSSOVER"

    return {
        "macd":      val,
        "signal":    sig_val,
        "histogram": h_val,
        "crossover": crossover,
        "bias":      "BULLISH" if val > sig_val else "BEARISH",
    }


def _ema(close: pd.Series, period: int) -> float:
    return round(float(close.ewm(span=period, adjust=False).mean().iloc[-1]), 2)


def _bollinger(close: pd.Series, period: int = 20) -> dict:
    sma   = close.rolling(window=period).mean()
    std   = close.rolling(window=period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std

    c = float(close.iloc[-1])
    u = float(upper.iloc[-1])
    l = float(lower.iloc[-1])
    m = float(sma.iloc[-1])
    bw = (u - l) / m * 100 if m else 0

    pos = "MIDDLE"
    if c >= u * 0.98:
        pos = "NEAR_UPPER"
    elif c <= l * 1.02:
        pos = "NEAR_LOWER"

    return {
        "upper":         round(u, 2),
        "middle":        round(m, 2),
        "lower":         round(l, 2),
        "position":      pos,
        "bandwidth_pct": round(bw, 2),
        "squeezing":     bw < 3.0,
    }


def _rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "UNKNOWN"
    if rsi > 70:
        return "OVERBOUGHT"
    if rsi < 30:
        return "OVERSOLD"
    if rsi > 55:
        return "BULLISH_ZONE"
    if rsi < 45:
        return "BEARISH_ZONE"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_index_technicals(symbol: str) -> dict:
    """Fetch OHLC + full set of technical indicators for a Nifty/BankNifty index."""
    yf_ticker = INDEX_YF.get(symbol, symbol)
    try:
        hist = yf.Ticker(yf_ticker).history(period="60d", interval="1d")
        if hist.empty or len(hist) < 27:
            return {"symbol": symbol, "error": "Insufficient data"}

        close = hist["Close"]
        high  = hist["High"]
        low   = hist["Low"]
        vol   = hist["Volume"]

        prev  = hist.iloc[-2]
        curr_close = round(float(close.iloc[-1]), 2)

        e20   = _ema(close, 20)
        e50   = _ema(close, 50)
        e200  = _ema(close, 200) if len(close) >= 200 else None

        ema_trend = (
            "STRONG_BULL" if curr_close > e20 > e50 else
            "STRONG_BEAR" if curr_close < e20 < e50 else
            "MIXED"
        )

        avg_vol   = float(vol.rolling(20).mean().iloc[-1])
        curr_vol  = float(vol.iloc[-1])
        vol_sig   = ("HIGH" if curr_vol > avg_vol * 1.2 else
                     "LOW"  if curr_vol < avg_vol * 0.8 else "NORMAL")

        rsi_val  = _rsi(close)
        macd_val = _macd(close)
        bb       = _bollinger(close)

        return {
            "symbol":        symbol,
            "current_close": curr_close,
            "prev_day": {
                "open":  round(float(prev["Open"]),  2),
                "high":  round(float(prev["High"]),  2),
                "low":   round(float(prev["Low"]),   2),
                "close": round(float(prev["Close"]), 2),
            },
            "week_high":     round(float(high.iloc[-5:].max()), 2),
            "week_low":      round(float(low.iloc[-5:].min()),  2),
            "ema_20":        e20,
            "ema_50":        e50,
            "ema_200":       e200,
            "ema_trend":     ema_trend,
            "rsi_14":        rsi_val,
            "rsi_zone":      _rsi_zone(rsi_val),
            "macd":          macd_val,
            "bollinger":     bb,
            "volume_signal": vol_sig,
            "error":         None,
        }
    except Exception as exc:
        logger.warning("Technical fetch failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


def fetch_stock_technicals(symbols: list[str]) -> list[dict]:
    """Fetch RSI + MACD + EMA-20 for a list of NSE stock symbols."""
    results = []
    for sym in symbols:
        try:
            hist = yf.Ticker(f"{sym}.NS").history(period="60d", interval="1d")
            if hist.empty or len(hist) < 27:
                results.append({"symbol": sym, "error": "Insufficient data"})
                continue
            close  = hist["Close"]
            rsi_v  = _rsi(close)
            macd_v = _macd(close)
            e20    = _ema(close, 20)
            curr   = round(float(close.iloc[-1]), 2)
            bias   = (
                "BULLISH" if curr > e20 and rsi_v and rsi_v > 50 else
                "BEARISH" if curr < e20 and rsi_v and rsi_v < 50 else
                "NEUTRAL"
            )
            results.append({
                "symbol": sym,
                "close":  curr,
                "ema_20": e20,
                "rsi_14": rsi_v,
                "rsi_zone": _rsi_zone(rsi_v),
                "macd":   macd_v,
                "bias":   bias,
                "error":  None,
            })
        except Exception as exc:
            logger.warning("Stock technical failed for %s: %s", sym, exc)
            results.append({"symbol": sym, "error": str(exc)})
    return results

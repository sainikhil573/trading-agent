"""
Intraday candle provider interface and implementations.

Why this exists:
  Daily OHLC (from yfinance) cannot resolve whether SL or a target was hit first
  when both extremes are reached in the same session. Intraday candles (5m/15m/30m)
  provide a chronological sequence to determine the actual fill order.

Providers (in preference order):
  1. LocalCSVIntradayProvider — reads CSV files from data/intraday/
  2. UnavailableIntradayProvider — graceful no-op when no source is configured

CSV schema (see data/intraday/schema_example.csv):
  symbol,datetime,open,high,low,close,volume,timeframe
  NIFTY,2026-05-20 09:15:00,22500.0,22550.0,22480.0,22520.0,1200000,5m

Future providers to wire when API credentials are available:
  AngelOneIntradayProvider  (Angel One SmartAPI)
  KiteIntradayProvider      (Zerodha Kite Connect)
  NSEIntradayProvider       (Direct NSE bhavcopy with intraday granularity)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

CANDLE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "timeframe"]


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class IntradayProvider(ABC):
    """Abstract interface every intraday data source must implement."""

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        trading_date: date,
        timeframe: str = "5m",
    ) -> pd.DataFrame:
        """
        Return intraday candles for symbol on trading_date at given timeframe.
        Always returns a DataFrame — empty when data is unavailable.
        Columns: symbol, datetime (tz-naive IST), open, high, low, close, volume, timeframe
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """True if this provider has any data configured (not necessarily for today)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name shown in the dashboard."""
        ...


# ---------------------------------------------------------------------------
# Unavailable provider (default — forces OUTCOME_UNKNOWN for ambiguous cases)
# ---------------------------------------------------------------------------

class UnavailableIntradayProvider(IntradayProvider):
    """
    Default when no intraday data source is configured.
    Always returns empty DataFrame — outcome tracker falls back to daily OHLC
    and marks ambiguous cases as OUTCOME_UNKNOWN (never as a false win).
    """

    @property
    def name(self) -> str:
        return "Unavailable (no intraday source configured)"

    def is_available(self) -> bool:
        return False

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        return pd.DataFrame(columns=CANDLE_COLUMNS)


# ---------------------------------------------------------------------------
# Local CSV provider
# ---------------------------------------------------------------------------

class LocalCSVIntradayProvider(IntradayProvider):
    """
    Reads intraday candles from local CSV files placed in data/intraday/.

    File naming convention:
      {SYMBOL}_{YYYYMMDD}_{timeframe}.csv
      e.g.  NIFTY_20260520_5m.csv
            BANKNIFTY_20260520_15m.csv

    CSV schema (header required):
      symbol,datetime,open,high,low,close,volume,timeframe
      NIFTY,2026-05-20 09:15:00,22500.0,22550.0,22480.0,22520.0,1200000,5m

    Rows must be sorted chronologically (oldest first).
    Timestamps should be IST, tz-naive (no UTC offset).
    """

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent / "data" / "intraday"
        self._dir = Path(data_dir)

    @property
    def name(self) -> str:
        return f"LocalCSV ({self._dir.name}/)"

    def is_available(self) -> bool:
        return self._dir.exists()

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        ds   = trading_date.strftime("%Y%m%d")
        path = self._dir / f"{symbol}_{ds}_{timeframe}.csv"

        if not path.exists():
            logger.debug("Intraday CSV not found: %s", path)
            return pd.DataFrame(columns=CANDLE_COLUMNS)

        try:
            df = pd.read_csv(path, parse_dates=["datetime"])
            missing = [c for c in CANDLE_COLUMNS if c not in df.columns]
            if missing:
                logger.warning("Intraday CSV %s missing columns: %s", path.name, missing)
                return pd.DataFrame(columns=CANDLE_COLUMNS)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
            df = df.sort_values("datetime").reset_index(drop=True)
            logger.info("Loaded %d %s candles for %s from %s", len(df), timeframe, symbol, path.name)
            return df
        except Exception as exc:
            logger.warning("Failed to read intraday CSV %s: %s", path, exc)
            return pd.DataFrame(columns=CANDLE_COLUMNS)

    def list_available(self, trading_date: date, timeframe: str = "5m") -> list[str]:
        """Return symbols for which intraday CSVs exist on trading_date."""
        if not self._dir.exists():
            return []
        ds = trading_date.strftime("%Y%m%d")
        return [
            p.stem.split("_")[0]
            for p in self._dir.glob(f"*_{ds}_{timeframe}.csv")
        ]


# ---------------------------------------------------------------------------
# Placeholder stubs for future API providers
# ---------------------------------------------------------------------------

class AngelOneIntradayProvider(IntradayProvider):
    """
    Placeholder for Angel One SmartAPI intraday data.
    Requires: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD env vars.
    Not yet implemented — wire credentials and install smartapi-python.
    """

    @property
    def name(self) -> str:
        return "Angel One SmartAPI (not yet implemented)"

    def is_available(self) -> bool:
        return False

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        logger.warning("AngelOneIntradayProvider is not implemented — returning empty")
        return pd.DataFrame(columns=CANDLE_COLUMNS)


class KiteIntradayProvider(IntradayProvider):
    """
    Placeholder for Zerodha Kite Connect intraday data.
    Requires: KITE_API_KEY, KITE_ACCESS_TOKEN env vars.
    Not yet implemented — wire credentials and install kiteconnect.
    """

    @property
    def name(self) -> str:
        return "Zerodha Kite Connect (not yet implemented)"

    def is_available(self) -> bool:
        return False

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        logger.warning("KiteIntradayProvider is not implemented — returning empty")
        return pd.DataFrame(columns=CANDLE_COLUMNS)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_default_provider() -> IntradayProvider:
    """
    Return the best available intraday provider.
    Priority: LocalCSV → Unavailable.
    Angel One / Kite providers require manual wiring (see stubs above).
    """
    csv_provider = LocalCSVIntradayProvider()
    if csv_provider.is_available():
        return csv_provider
    return UnavailableIntradayProvider()

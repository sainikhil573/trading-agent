"""
Intraday candle provider interface and implementations.

Why this exists:
  Daily OHLC (from yfinance) cannot resolve whether SL or a target was hit first
  when both extremes are reached in the same session. Intraday candles (5m/15m/30m)
  provide a chronological sequence to determine the actual fill order.

Providers (in preference order):
  1. AngelOneIntradayProvider — Angel One SmartAPI (requires credentials + smartapi-python)
  2. KiteIntradayProvider     — Zerodha Kite Connect (requires credentials + kiteconnect)
  3. LocalCSVIntradayProvider — reads CSV files from data/intraday/ (always available)
  4. UnavailableIntradayProvider — graceful no-op when no source is configured

Provider selection is driven by BROKER_PROVIDER env var via broker_config.get_configured_provider().
CSV is always the safe fallback.

CSV schema (see data/intraday/schema_example.csv):
  symbol,datetime,open,high,low,close,volume,timeframe
  NIFTY,2026-05-20 09:15:00,22500.0,22550.0,22480.0,22520.0,1200000,5m
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

CANDLE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "timeframe"]

# Angel One timeframe string map
_ANGELONE_TF_MAP = {
    "1m":  "ONE_MINUTE",
    "3m":  "THREE_MINUTE",
    "5m":  "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "60m": "ONE_HOUR",
    "1h":  "ONE_HOUR",
}

# Kite timeframe string map
_KITE_TF_MAP = {
    "1m":  "minute",
    "3m":  "3minute",
    "5m":  "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
    "1h":  "60minute",
}


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
        """True if this provider has data access configured (not necessarily for today)."""
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
# Response normalisation helpers (module-level, testable without real API calls)
# ---------------------------------------------------------------------------

def normalize_angelone_candles(raw_candles: list, symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Normalise Angel One SmartAPI historical candle response to CANDLE_COLUMNS format.

    Angel One candle format (each element):
        [timestamp_str, open, high, low, close, volume]
    where timestamp_str is ISO 8601 with IST offset, e.g.:
        "2026-05-20T09:15:00+05:30"

    Parameters
    ----------
    raw_candles : list of [timestamp, open, high, low, close, volume] sub-lists
    symbol      : e.g. "NIFTY"
    timeframe   : e.g. "5m"

    Returns
    -------
    DataFrame with CANDLE_COLUMNS, sorted chronologically, tz-naive IST datetimes.
    Empty DataFrame on any error.
    """
    if not raw_candles:
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    try:
        rows = []
        for candle in raw_candles:
            if len(candle) < 6:
                continue
            ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
            dt = pd.to_datetime(ts, utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
            rows.append({
                "symbol":    symbol,
                "datetime":  dt,
                "open":      float(o),
                "high":      float(h),
                "low":       float(l),
                "close":     float(c),
                "volume":    float(v),
                "timeframe": timeframe,
            })

        if not rows:
            return pd.DataFrame(columns=CANDLE_COLUMNS)

        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df

    except Exception as exc:
        logger.warning("Failed to normalise Angel One candles: %s", exc)
        return pd.DataFrame(columns=CANDLE_COLUMNS)


def normalize_kite_candles(raw_candles: list, symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Normalise Zerodha Kite Connect historical candle response to CANDLE_COLUMNS format.

    Kite candle format (each element):
        [datetime_str, open, high, low, close, volume]  — or optionally with OI as 7th element
    where datetime_str is IST without timezone, e.g.:
        "2026-05-20 09:15:00"

    Parameters
    ----------
    raw_candles : list of [datetime, open, high, low, close, volume] sub-lists
    symbol      : e.g. "NIFTY"
    timeframe   : e.g. "5m"

    Returns
    -------
    DataFrame with CANDLE_COLUMNS, sorted chronologically.
    Empty DataFrame on any error.
    """
    if not raw_candles:
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    try:
        rows = []
        for candle in raw_candles:
            if len(candle) < 6:
                continue
            ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
            dt = pd.to_datetime(ts)
            # Strip timezone if present (Kite sometimes returns tz-aware datetimes)
            if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                dt = dt.tz_convert("Asia/Kolkata").tz_localize(None)
            rows.append({
                "symbol":    symbol,
                "datetime":  dt,
                "open":      float(o),
                "high":      float(h),
                "low":       float(l),
                "close":     float(c),
                "volume":    float(v),
                "timeframe": timeframe,
            })

        if not rows:
            return pd.DataFrame(columns=CANDLE_COLUMNS)

        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df

    except Exception as exc:
        logger.warning("Failed to normalise Kite candles: %s", exc)
        return pd.DataFrame(columns=CANDLE_COLUMNS)


# ---------------------------------------------------------------------------
# Angel One SmartAPI provider
# ---------------------------------------------------------------------------

class AngelOneIntradayProvider(IntradayProvider):
    """
    Angel One SmartAPI intraday candle provider.

    Required env vars (or pass creds dict directly):
      ANGELONE_API_KEY      — SmartAPI API key (from developer console)
      ANGELONE_CLIENT_CODE  — Trading client code / user ID
      ANGELONE_TOTP_SECRET  — Base32 TOTP secret for 2FA (optional)

    Required package: pip install smartapi-python pyotp

    Status
    ------
    Credential-aware stub. Normalisation is fully implemented and tested.
    Actual API fetch is not yet wired (returns empty DataFrame with a warning).
    To complete the integration: implement _fetch() with live SmartConnect calls
    and a symbol→token lookup table.
    """

    def __init__(self, creds: dict | None = None):
        if creds is None:
            creds = {
                "api_key":     os.environ.get("ANGELONE_API_KEY", ""),
                "client_code": os.environ.get("ANGELONE_CLIENT_CODE", ""),
                "totp_secret": os.environ.get("ANGELONE_TOTP_SECRET", ""),
            }
        self._creds      = creds
        self._has_creds  = bool(creds.get("api_key") and creds.get("client_code"))
        self._pkg_ok     = self._check_package()

    @staticmethod
    def _check_package() -> bool:
        try:
            import smartapi  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        if not self._has_creds:
            return "Angel One SmartAPI (credentials missing — set ANGELONE_API_KEY, ANGELONE_CLIENT_CODE)"
        if not self._pkg_ok:
            return "Angel One SmartAPI (package missing — pip install smartapi-python pyotp)"
        return "Angel One SmartAPI"

    def is_available(self) -> bool:
        return self._has_creds and self._pkg_ok

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        if not self.is_available():
            logger.warning("AngelOne provider not available: %s", self.name)
            return pd.DataFrame(columns=CANDLE_COLUMNS)

        try:
            return self._fetch(symbol, trading_date, timeframe)
        except Exception as exc:
            logger.warning(
                "AngelOne get_candles failed for %s %s %s: %s", symbol, trading_date, timeframe, exc
            )
            return pd.DataFrame(columns=CANDLE_COLUMNS)

    def _fetch(self, symbol: str, trading_date: date, timeframe: str) -> pd.DataFrame:
        """
        Fetch from Angel One SmartAPI.

        Not yet fully implemented: symbol→token lookup is required before live calls.
        When credentials and package are present this logs a warning and returns empty.
        Replace this body with live SmartConnect calls when symbol tokens are available.
        """
        logger.warning(
            "AngelOne _fetch not yet implemented for %s %s — "
            "symbol→token lookup required. Returning empty candles.",
            symbol, trading_date,
        )
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    @staticmethod
    def normalize_response(raw_candles: list, symbol: str, timeframe: str) -> pd.DataFrame:
        """Normalise a raw SmartAPI candle list. Testable without credentials."""
        return normalize_angelone_candles(raw_candles, symbol, timeframe)


# ---------------------------------------------------------------------------
# Zerodha Kite Connect provider
# ---------------------------------------------------------------------------

class KiteIntradayProvider(IntradayProvider):
    """
    Zerodha Kite Connect intraday candle provider.

    Required env vars (or pass creds dict directly):
      KITE_API_KEY      — Kite Connect API key
      KITE_ACCESS_TOKEN — Session access token (rotates daily — must be refreshed each morning)

    Required package: pip install kiteconnect

    Status
    ------
    Credential-aware stub. Normalisation is fully implemented and tested.
    Actual API fetch is not yet wired (returns empty DataFrame with a warning).
    To complete the integration: implement _fetch() with live KiteConnect calls
    and instrument token lookup for NSE F&O symbols.
    """

    def __init__(self, creds: dict | None = None):
        if creds is None:
            creds = {
                "api_key":      os.environ.get("KITE_API_KEY", ""),
                "access_token": os.environ.get("KITE_ACCESS_TOKEN", ""),
            }
        self._creds     = creds
        self._has_creds = bool(creds.get("api_key") and creds.get("access_token"))
        self._pkg_ok    = self._check_package()

    @staticmethod
    def _check_package() -> bool:
        try:
            from kiteconnect import KiteConnect  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        if not self._has_creds:
            return "Zerodha Kite Connect (credentials missing — set KITE_API_KEY, KITE_ACCESS_TOKEN)"
        if not self._pkg_ok:
            return "Zerodha Kite Connect (package missing — pip install kiteconnect)"
        return "Zerodha Kite Connect"

    def is_available(self) -> bool:
        return self._has_creds and self._pkg_ok

    def get_candles(self, symbol: str, trading_date: date, timeframe: str = "5m") -> pd.DataFrame:
        if not self.is_available():
            logger.warning("Kite provider not available: %s", self.name)
            return pd.DataFrame(columns=CANDLE_COLUMNS)

        try:
            return self._fetch(symbol, trading_date, timeframe)
        except Exception as exc:
            logger.warning(
                "Kite get_candles failed for %s %s %s: %s", symbol, trading_date, timeframe, exc
            )
            return pd.DataFrame(columns=CANDLE_COLUMNS)

    def _fetch(self, symbol: str, trading_date: date, timeframe: str) -> pd.DataFrame:
        """
        Fetch from Zerodha Kite Connect.

        Not yet fully implemented: instrument token lookup is required before live calls.
        When credentials and package are present this logs a warning and returns empty.
        Replace this body with live KiteConnect calls when instrument tokens are available.
        """
        logger.warning(
            "Kite _fetch not yet implemented for %s %s — "
            "instrument token lookup required. Returning empty candles.",
            symbol, trading_date,
        )
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    @staticmethod
    def normalize_response(raw_candles: list, symbol: str, timeframe: str) -> pd.DataFrame:
        """Normalise a raw Kite candle list. Testable without credentials."""
        return normalize_kite_candles(raw_candles, symbol, timeframe)


# ---------------------------------------------------------------------------
# Factory — backward-compatible; delegates to broker_config
# ---------------------------------------------------------------------------

def get_default_provider() -> IntradayProvider:
    """
    Return the configured intraday provider (backward-compatible entry point).
    Delegates to broker_config.get_configured_provider() which reads BROKER_PROVIDER env var.
    CSV is always the safe fallback.
    """
    # Lazy import to avoid circular dependency
    from src.fetchers.broker_config import get_configured_provider
    return get_configured_provider()

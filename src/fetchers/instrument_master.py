"""
Instrument master / token mapping for broker-specific candle fetching.

Why tokens are required
-----------------------
Broker APIs (Angel One SmartAPI, Zerodha Kite Connect) identify instruments by
opaque numeric or string tokens, not by the human-readable symbols we use in
analysis (e.g. "NIFTY", "BANKNIFTY", "RELIANCE"). Before calling any historical
candle endpoint you must translate the symbol to the broker-specific token.

Providers
---------
- Kite Connect   : uses instrument_token (positive integer)
- Angel One      : uses symbol_token (string, often numeric like "26000")

Token master file
-----------------
Expected at: data/instruments/instrument_master.csv
See data/instruments/schema_example.csv for the required column layout.
Download real token files from your broker (see docs/instrument_master.md).

WARNING: Do not hardcode or guess real broker tokens. Wrong tokens silently
         return incorrect candle data or raise 400 errors at the broker.

Usage
-----
    from src.fetchers.instrument_master import InstrumentMaster, normalize_symbol_input

    master = InstrumentMaster()          # loads data/instruments/instrument_master.csv
    inst   = master.lookup_index_symbol("kite", "NIFTY")
    if inst and inst.has_token("kite"):
        token = inst.get_token("kite")   # int
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS: tuple[str, ...] = (
    "provider", "exchange", "symbol", "trading_symbol",
    "instrument_token", "symbol_token", "instrument_type",
    "expiry", "strike", "option_type", "lot_size", "tick_size",
)

# instrument_type values treated as index candle sources (preference order)
_INDEX_TYPES = ("INDEX", "FUTIDX")
# instrument_type values for equity cash (NSE EQ)
_EQUITY_TYPES = ("EQ",)
# instrument_type values for option contracts
_OPTION_TYPES = ("OPTIDX", "OPTSTK", "CE", "PE")

# Canonical aliases for common symbols that arrive with exchange prefixes
_SYMBOL_ALIASES: dict[str, str] = {
    "^NSEI":    "NIFTY",
    "^NSEBANK": "BANKNIFTY",
    "NIFTY50":  "NIFTY",
    "NIFTY 50": "NIFTY",
}

_DEFAULT_MASTER_PATH = (
    Path(__file__).parent.parent.parent / "data" / "instruments" / "instrument_master.csv"
)

# ---------------------------------------------------------------------------
# Instrument dataclass
# ---------------------------------------------------------------------------

@dataclass
class Instrument:
    """Normalised representation of a single row from the instrument master."""

    provider:         str
    exchange:         str
    symbol:           str
    trading_symbol:   str
    instrument_token: Optional[int]   # Kite Connect — positive integer, None if absent
    symbol_token:     str             # Angel One — string (may be empty)
    instrument_type:  str
    expiry:           str             # ISO date string e.g. "2026-05-29" or ""
    strike:           float           # 0.0 for non-option rows
    option_type:      str             # "CE", "PE", or ""
    lot_size:         int
    tick_size:        float

    def get_token(self, provider: str) -> "int | str | None":
        """
        Return the broker-specific token for the given provider, or None if absent.

        - "kite"     : returns instrument_token (int > 0) or None
        - "angelone" : returns symbol_token (non-empty str) or None
        """
        p = provider.lower().replace(" ", "").replace("_", "").replace("-", "")
        if p == "kite":
            if self.instrument_token is not None and self.instrument_token > 0:
                return self.instrument_token
            return None
        if p == "angelone":
            return self.symbol_token if self.symbol_token else None
        return None

    def has_token(self, provider: str) -> bool:
        """True when a usable token exists for the given provider."""
        return self.get_token(provider) is not None


# ---------------------------------------------------------------------------
# InstrumentMaster
# ---------------------------------------------------------------------------

class InstrumentMaster:
    """
    Loads and queries an instrument master CSV file.

    Safe defaults: if the file is missing or malformed, all lookups return None
    and is_loaded is False. The provider stubs call has_token() before any
    fetch attempt, so a missing master file degrades gracefully to empty candles
    (same as the current stub behavior) with a clear diagnostic log message.

    Parameters
    ----------
    path : path to a CSV file. Defaults to data/instruments/instrument_master.csv.
    """

    def __init__(self, path: "Path | str | None" = None):
        self._path  = Path(path) if path is not None else _DEFAULT_MASTER_PATH
        self._df: pd.DataFrame = pd.DataFrame()
        self._loaded = False
        self._error:  Optional[str] = None
        self._load()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def record_count(self) -> int:
        return len(self._df)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._error = (
                f"Instrument master file not found: {self._path}. "
                "Download from your broker and place at that path. "
                "See docs/instrument_master.md."
            )
            logger.debug("InstrumentMaster: %s", self._error)
            return

        try:
            df = pd.read_csv(self._path, dtype=str)
        except Exception as exc:
            self._error = f"Failed to read instrument master CSV: {exc}"
            logger.warning("InstrumentMaster: %s", self._error)
            return

        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            self._error = (
                f"Instrument master CSV is missing required columns: {missing_cols}. "
                "See data/instruments/schema_example.csv for the expected layout."
            )
            logger.warning("InstrumentMaster: %s", self._error)
            return

        # Normalise string columns — strip whitespace + uppercase keys
        for col in ("provider", "exchange", "symbol", "instrument_type", "option_type"):
            df[col] = df[col].fillna("").str.strip().str.upper()
        for col in ("trading_symbol", "symbol_token", "expiry"):
            df[col] = df[col].fillna("").str.strip()

        # Convert numeric columns safely
        df["instrument_token"] = (
            pd.to_numeric(df["instrument_token"], errors="coerce")
            .fillna(0)
            .astype(int)
        )
        df["strike"]   = pd.to_numeric(df["strike"],   errors="coerce").fillna(0.0)
        df["lot_size"] = (
            pd.to_numeric(df["lot_size"], errors="coerce").fillna(0).astype(int)
        )
        df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce").fillna(0.05)

        self._df     = df.reset_index(drop=True)
        self._loaded = True
        logger.info(
            "InstrumentMaster loaded: %d records from %s",
            len(self._df), self._path.name,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter_provider(self, provider: str) -> pd.DataFrame:
        """Return rows matching the given provider (case-insensitive)."""
        p = provider.lower().replace(" ", "").replace("_", "").replace("-", "")
        return self._df[self._df["provider"].str.lower().str.replace(" ", "")
                        .str.replace("_", "").str.replace("-", "") == p]

    @staticmethod
    def _row_to_instrument(row: pd.Series) -> Instrument:
        token = int(row["instrument_token"])
        return Instrument(
            provider=str(row["provider"]),
            exchange=str(row["exchange"]),
            symbol=str(row["symbol"]),
            trading_symbol=str(row["trading_symbol"]),
            instrument_token=token if token > 0 else None,
            symbol_token=str(row["symbol_token"]),
            instrument_type=str(row["instrument_type"]),
            expiry=str(row["expiry"]),
            strike=float(row["strike"]),
            option_type=str(row["option_type"]),
            lot_size=int(row["lot_size"]),
            tick_size=float(row["tick_size"]),
        )

    # ------------------------------------------------------------------
    # Public lookup API
    # ------------------------------------------------------------------

    def lookup_index_symbol(self, provider: str, symbol: str) -> Optional[Instrument]:
        """
        Look up an index instrument (e.g. NIFTY, BANKNIFTY) for candle fetching.

        Preference order: INDEX > FUTIDX (spot index candle before futures).
        Returns None when not found or master is not loaded.
        """
        if not self._loaded:
            return None

        sym  = normalize_symbol_input(symbol)
        prov = self._filter_provider(provider)
        if prov.empty:
            return None

        mask = prov["symbol"].str.upper() == sym

        for itype in _INDEX_TYPES:
            rows = prov[mask & (prov["instrument_type"] == itype)]
            if not rows.empty:
                return self._row_to_instrument(rows.iloc[0])

        return None

    def lookup_equity_symbol(self, provider: str, symbol: str) -> Optional[Instrument]:
        """
        Look up a cash-equity instrument (EQ segment on NSE).
        Returns None when not found or master is not loaded.
        """
        if not self._loaded:
            return None

        sym  = normalize_symbol_input(symbol)
        prov = self._filter_provider(provider)
        if prov.empty:
            return None

        rows = prov[
            (prov["symbol"].str.upper() == sym)
            & (prov["instrument_type"].isin(_EQUITY_TYPES))
        ]
        if rows.empty:
            return None
        return self._row_to_instrument(rows.iloc[0])

    def lookup_option_contract(
        self,
        provider: str,
        underlying: str,
        expiry: str,
        strike: float,
        option_type: str,
    ) -> Optional[Instrument]:
        """
        Look up a specific option contract.

        Parameters
        ----------
        provider    : "kite" or "angelone"
        underlying  : underlying symbol, e.g. "NIFTY"
        expiry      : ISO date string, e.g. "2026-05-29"
        strike      : float, e.g. 25000.0
        option_type : "CE" or "PE"

        Returns None when no exact match found or master is not loaded.
        """
        if not self._loaded:
            return None

        sym  = normalize_symbol_input(underlying)
        prov = self._filter_provider(provider)
        if prov.empty:
            return None

        ot = option_type.strip().upper()

        rows = prov[
            (prov["symbol"].str.upper() == sym)
            & (prov["instrument_type"].isin(_OPTION_TYPES))
            & (prov["expiry"] == expiry.strip())
            & ((prov["strike"] - strike).abs() < 0.01)
            & (prov["option_type"] == ot)
        ]
        if rows.empty:
            return None
        return self._row_to_instrument(rows.iloc[0])

    def token_readiness(self, provider: str, symbols: list[str]) -> dict[str, bool]:
        """
        Return a mapping of symbol → has_usable_token for the given provider.

        Checks both INDEX and EQUITY types. Useful for dashboard warnings.
        """
        result: dict[str, bool] = {}
        for raw_sym in symbols:
            sym = normalize_symbol_input(raw_sym)
            inst = self.lookup_index_symbol(provider, sym) or self.lookup_equity_symbol(provider, sym)
            result[sym] = inst.has_token(provider) if inst else False
        return result


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def normalize_symbol_input(raw_symbol: str) -> str:
    """
    Translate a raw symbol string into a canonical uppercase form used in
    the instrument master.

    Rules applied (in order):
    1. Strip whitespace.
    2. Apply known aliases (^NSEI → NIFTY, etc.).
    3. Strip trailing `.NS` suffix added by yfinance.
    4. Uppercase.

    Parameters
    ----------
    raw_symbol : e.g. "NIFTY", "^NSEI", "RELIANCE.NS", "nifty 50"

    Returns
    -------
    Canonical symbol string e.g. "NIFTY", "RELIANCE"
    """
    s = str(raw_symbol).strip()

    # Alias map (check before stripping suffix — alias keys may include spaces)
    upper_s = s.upper()
    if upper_s in _SYMBOL_ALIASES:
        return _SYMBOL_ALIASES[upper_s]

    # Strip yfinance .NS / .BO suffix
    if s.upper().endswith(".NS"):
        s = s[:-3]
    elif s.upper().endswith(".BO"):
        s = s[:-3]

    return s.upper()


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-loaded)
# ---------------------------------------------------------------------------

_default_master: Optional[InstrumentMaster] = None


def get_instrument_master(path: "Path | str | None" = None) -> InstrumentMaster:
    """
    Return the module-level InstrumentMaster singleton, loading it on first call.

    Pass a custom path to override the default (useful in tests).
    Passing a non-None path always creates a fresh instance (no caching).
    """
    global _default_master
    if path is not None:
        return InstrumentMaster(path=path)
    if _default_master is None:
        _default_master = InstrumentMaster()
    return _default_master

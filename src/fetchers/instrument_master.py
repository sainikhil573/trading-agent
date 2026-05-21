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
Default canonical path: data/instruments/instrument_master.csv
Alternative native files:
  data/instruments/kite_instruments.csv   — Kite's own instruments CSV format
  data/instruments/angelone_instruments.csv — Angel One scrip master CSV format

See data/instruments/schema_example.csv for the canonical column layout.
See docs/instrument_master.md for full setup instructions.

WARNING: Do not hardcode or guess real broker tokens. Wrong tokens silently
         return incorrect candle data or raise 400 errors at the broker.

Usage
-----
    from src.fetchers.instrument_master import get_instrument_master

    master = get_instrument_master()
    inst   = master.lookup_index_symbol("kite", "NIFTY")
    if inst and inst.has_token("kite"):
        token = inst.get_token("kite")   # int

    report = master.validate()
    cov    = master.coverage_report("kite")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
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

SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"KITE", "ANGELONE"})

# instrument_type values treated as index candle sources (preference order)
_INDEX_TYPES = ("INDEX", "FUTIDX")
# instrument_type values for equity cash (NSE EQ)
_EQUITY_TYPES = ("EQ",)
# instrument_type values for option contracts
_OPTION_TYPES = ("OPTIDX", "OPTSTK", "CE", "PE")

# NSE index symbols — used to distinguish FUTIDX/OPTIDX from FUTSTK/OPTSTK
_NSE_INDICES: frozenset[str] = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "SENSEX",
})

# Default index symbols checked in coverage/readiness reports
_DEFAULT_INDEX_SYMBOLS: list[str] = ["NIFTY", "BANKNIFTY"]

# Default F&O stock symbols checked in coverage report
_DEFAULT_FO_SYMBOLS: list[str] = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "AXISBANK", "SBIN", "TATASTEEL", "BAJFINANCE",
]

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
_INSTRUMENTS_DIR = Path(__file__).parent.parent.parent / "data" / "instruments"

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    """
    Result of InstrumentMaster.validate().

    Separates structural errors (prevent lookup) from data-quality warnings
    (missing tokens, expired contracts) so the caller can display them differently.
    """
    total_rows:               int
    valid_rows:               int
    errors:                   list[str] = field(default_factory=list)
    warnings:                 list[str] = field(default_factory=list)
    missing_token_rows:       int = 0
    expired_option_count:     int = 0
    unsupported_provider_count: int = 0
    missing_token_symbols:    list[str] = field(default_factory=list)
    expired_option_symbols:   list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    @property
    def is_clean(self) -> bool:
        return not self.has_errors and not self.has_warnings


@dataclass
class CoverageReport:
    """
    Result of InstrumentMaster.coverage_report().

    Shows how many instruments are loaded per type/exchange and which
    symbols are token-ready for the given provider.
    """
    provider:               str
    total_count:            int
    by_type:                dict[str, int] = field(default_factory=dict)
    by_exchange:            dict[str, int] = field(default_factory=dict)
    index_ready:            dict[str, bool] = field(default_factory=dict)
    equity_ready:           dict[str, bool] = field(default_factory=dict)
    missing_token_symbols:  list[str] = field(default_factory=list)
    expired_option_symbols: list[str] = field(default_factory=list)

    @property
    def indices_all_ready(self) -> bool:
        return bool(self.index_ready) and all(self.index_ready.values())

    @property
    def missing_index_symbols(self) -> list[str]:
        return [s for s, ok in self.index_ready.items() if not ok]


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
        p = _norm_provider(provider)
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

    def is_expired(self, today: date | None = None) -> bool:
        """True when expiry is set and is before today."""
        if not self.expiry:
            return False
        if today is None:
            today = date.today()
        try:
            return date.fromisoformat(self.expiry) < today
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# InstrumentMaster
# ---------------------------------------------------------------------------

class InstrumentMaster:
    """
    Loads and queries an instrument master CSV file.

    Safe defaults: if the file is missing or malformed, all lookups return None
    and is_loaded is False. Provider stubs call has_token() before any fetch
    attempt, so a missing master degrades gracefully to empty candles with a
    clear diagnostic log.

    Parameters
    ----------
    path       : path to a canonical CSV file.
                 Defaults to data/instruments/instrument_master.csv.
    _skip_load : internal flag used by from_dataframe() factory.
    """

    def __init__(self, path: "Path | str | None" = None, _skip_load: bool = False):
        self._path         = Path(path) if path is not None else _DEFAULT_MASTER_PATH
        self._df: pd.DataFrame = pd.DataFrame()
        self._loaded       = False
        self._error:       Optional[str] = None
        self._source_name: Optional[str] = None   # set by from_dataframe
        if not _skip_load:
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

    @property
    def source_name(self) -> str:
        """Human-readable source identifier (file name or merged source list)."""
        return self._source_name or self._path.name

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._error = (
                f"Instrument master file not found: {self._path}. "
                "Download from your broker and place at that path, "
                "or use scripts/check_instrument_master.py to validate. "
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

        self._process_df(df)

    def _process_df(self, df: pd.DataFrame) -> None:
        """Validate, normalise, and store a canonical-schema DataFrame."""
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            self._error = (
                f"Instrument master CSV is missing required columns: {missing_cols}. "
                "See data/instruments/schema_example.csv for the expected layout."
            )
            logger.warning("InstrumentMaster: %s", self._error)
            return

        df = df.copy()

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
            len(self._df), self.source_name,
        )

    # ------------------------------------------------------------------
    # Factory: build from an already-normalised DataFrame
    # ------------------------------------------------------------------

    @classmethod
    def from_dataframe(
        cls,
        df: pd.DataFrame,
        source_name: str = "merged",
    ) -> "InstrumentMaster":
        """
        Create an InstrumentMaster from an already-canonical DataFrame.

        Used internally by load_best_available_master() when merging
        normalised provider-specific files.
        """
        inst = cls(_skip_load=True)
        inst._source_name = source_name
        inst._process_df(df)
        return inst

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter_provider(self, provider: str) -> pd.DataFrame:
        """Return rows matching the given provider (normalised comparison)."""
        p = _norm_provider(provider)
        return self._df[
            self._df["provider"].str.lower()
            .str.replace(" ", "", regex=False)
            .str.replace("_", "", regex=False)
            .str.replace("-", "", regex=False) == p
        ]

    @staticmethod
    def _row_to_instrument(row: pd.Series) -> Instrument:
        token = int(row["instrument_token"])
        return Instrument(
            provider        = str(row["provider"]),
            exchange        = str(row["exchange"]),
            symbol          = str(row["symbol"]),
            trading_symbol  = str(row["trading_symbol"]),
            instrument_token= token if token > 0 else None,
            symbol_token    = str(row["symbol_token"]),
            instrument_type = str(row["instrument_type"]),
            expiry          = str(row["expiry"]),
            strike          = float(row["strike"]),
            option_type     = str(row["option_type"]),
            lot_size        = int(row["lot_size"]),
            tick_size       = float(row["tick_size"]),
        )

    # ------------------------------------------------------------------
    # Public lookup API (unchanged from PR6)
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
        Look up a specific option contract by exact expiry / strike / type match.
        Returns None when no match found or master is not loaded.
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
        Checks INDEX then EQUITY types. Useful for dashboard status line.
        """
        result: dict[str, bool] = {}
        for raw_sym in symbols:
            sym = normalize_symbol_input(raw_sym)
            inst = (
                self.lookup_index_symbol(provider, sym)
                or self.lookup_equity_symbol(provider, sym)
            )
            result[sym] = inst.has_token(provider) if inst else False
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, today: date | None = None) -> ValidationReport:
        """
        Validate all rows in the instrument master.

        Separates:
        - Errors  : structural problems that prevent lookup (empty symbol, bad option
                    row, unsupported provider). Up to 20 reported.
        - Warnings: data-quality issues (missing token, expired option contract).
                    Up to 20 reported.

        Parameters
        ----------
        today : reference date for expiry checks. Defaults to date.today().
        """
        if today is None:
            today = date.today()

        if not self._loaded:
            return ValidationReport(
                total_rows=0,
                valid_rows=0,
                errors=[self._error or "Instrument master not loaded"],
                warnings=[],
            )

        errors:   list[str] = []
        warnings: list[str] = []
        valid_rows                = 0
        missing_token_rows        = 0
        expired_option_count      = 0
        unsupported_provider_count= 0
        missing_token_syms:  list[str] = []
        expired_option_syms: list[str] = []

        def _add_err(msg: str) -> None:
            if len(errors) < 20:
                errors.append(msg)

        def _add_warn(msg: str) -> None:
            if len(warnings) < 20:
                warnings.append(msg)

        for i, row in self._df.iterrows():
            row_ok = True

            # --- Provider check ---
            p = str(row["provider"]).upper()
            if p not in SUPPORTED_PROVIDERS:
                unsupported_provider_count += 1
                _add_err(f"Row {i}: unsupported provider '{row['provider']}' (expected kite or angelone)")
                continue  # can't validate further without a known provider

            # --- Required fields ---
            sym  = str(row["symbol"]).strip()
            tsym = str(row["trading_symbol"]).strip()
            exch = str(row["exchange"]).strip()

            if not sym:
                _add_err(f"Row {i}: symbol is empty")
                row_ok = False
            if not tsym:
                _add_err(f"Row {i}: trading_symbol is empty")
                row_ok = False
            if not exch:
                _add_err(f"Row {i}: exchange is empty")
                row_ok = False

            # --- Token check (warnings only — token may be added later) ---
            display_sym = tsym or sym or f"row {i}"
            if p == "KITE":
                if int(row["instrument_token"]) <= 0:
                    missing_token_rows += 1
                    if len(missing_token_syms) < 10:
                        missing_token_syms.append(display_sym)
                    _add_warn(
                        f"Kite ({display_sym}): instrument_token is 0 — "
                        "replace with real token from kite.trade/instruments"
                    )
            elif p == "ANGELONE":
                if not str(row["symbol_token"]).strip():
                    missing_token_rows += 1
                    if len(missing_token_syms) < 10:
                        missing_token_syms.append(display_sym)
                    _add_warn(
                        f"Angel One ({display_sym}): symbol_token is empty — "
                        "replace with real token from broker scrip master"
                    )

            # --- Option-specific checks ---
            itype = str(row["instrument_type"]).upper()
            if itype in _OPTION_TYPES:
                expiry_str = str(row["expiry"]).strip()
                if not expiry_str:
                    _add_err(f"Row {i} ({display_sym}): option row is missing expiry date")
                    row_ok = False
                else:
                    try:
                        exp_dt = date.fromisoformat(expiry_str)
                        if exp_dt < today:
                            expired_option_count += 1
                            if len(expired_option_syms) < 10:
                                expired_option_syms.append(display_sym)
                            _add_warn(
                                f"Option ({display_sym}): expired on {expiry_str} — "
                                "refresh instrument master with current expiry week"
                            )
                    except ValueError:
                        _add_err(f"Row {i} ({display_sym}): invalid expiry date '{expiry_str}'")
                        row_ok = False

                if float(row["strike"]) <= 0:
                    _add_err(
                        f"Row {i} ({display_sym}): option row has zero/invalid strike {row['strike']}"
                    )
                    row_ok = False

                ot = str(row["option_type"]).upper()
                if ot not in ("CE", "PE"):
                    _add_err(
                        f"Row {i} ({display_sym}): option row has invalid option_type '{row['option_type']}'"
                    )
                    row_ok = False

            if row_ok:
                valid_rows += 1

        # Truncation note if cap was hit
        if len(errors) == 20:
            errors.append("... (truncated — only first 20 errors shown)")
        if len(warnings) == 20:
            warnings.append("... (truncated — only first 20 warnings shown)")

        return ValidationReport(
            total_rows               = len(self._df),
            valid_rows               = valid_rows,
            errors                   = errors,
            warnings                 = warnings,
            missing_token_rows       = missing_token_rows,
            expired_option_count     = expired_option_count,
            unsupported_provider_count = unsupported_provider_count,
            missing_token_symbols    = missing_token_syms,
            expired_option_symbols   = expired_option_syms,
        )

    # ------------------------------------------------------------------
    # Coverage report
    # ------------------------------------------------------------------

    def coverage_report(
        self,
        provider: str,
        fo_symbols: list[str] | None = None,
    ) -> CoverageReport:
        """
        Build a coverage report for the given provider.

        Parameters
        ----------
        provider   : "kite" or "angelone"
        fo_symbols : stock symbols to check for token readiness.
                     Defaults to the 9 key F&O stocks in _DEFAULT_FO_SYMBOLS.

        Returns
        -------
        CoverageReport dataclass with counts and readiness flags.
        """
        if fo_symbols is None:
            fo_symbols = list(_DEFAULT_FO_SYMBOLS)

        _today = date.today()

        if not self._loaded:
            return CoverageReport(
                provider      = provider,
                total_count   = 0,
                by_type       = {},
                by_exchange   = {},
                index_ready   = {s: False for s in _DEFAULT_INDEX_SYMBOLS},
                equity_ready  = {s: False for s in fo_symbols},
                missing_token_symbols  = [],
                expired_option_symbols = [],
            )

        prov_df = self._filter_provider(provider)

        by_type:    dict[str, int] = {}
        by_exchange: dict[str, int] = {}
        expired_syms: list[str] = []

        for _, row in prov_df.iterrows():
            itype = str(row["instrument_type"])
            exch  = str(row["exchange"])
            by_type[itype]    = by_type.get(itype, 0) + 1
            by_exchange[exch] = by_exchange.get(exch, 0) + 1

            if itype in _OPTION_TYPES:
                exp = str(row["expiry"]).strip()
                if exp:
                    try:
                        if date.fromisoformat(exp) < _today:
                            if len(expired_syms) < 10:
                                expired_syms.append(str(row["trading_symbol"]))
                    except ValueError:
                        pass

        # Index token readiness
        index_ready: dict[str, bool] = {}
        for sym in _DEFAULT_INDEX_SYMBOLS:
            inst = self.lookup_index_symbol(provider, sym)
            index_ready[sym] = inst.has_token(provider) if inst else False

        # Equity token readiness
        equity_ready: dict[str, bool] = {}
        missing_syms: list[str] = []
        for sym in fo_symbols:
            inst = self.lookup_equity_symbol(provider, sym)
            has_tok = inst.has_token(provider) if inst else False
            equity_ready[sym] = has_tok
            if not has_tok:
                missing_syms.append(sym)

        return CoverageReport(
            provider               = provider,
            total_count            = len(prov_df),
            by_type                = by_type,
            by_exchange            = by_exchange,
            index_ready            = index_ready,
            equity_ready           = equity_ready,
            missing_token_symbols  = missing_syms,
            expired_option_symbols = expired_syms,
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def _norm_provider(provider: str) -> str:
    """Normalise provider string to lowercase, removing separators."""
    return provider.lower().replace(" ", "").replace("_", "").replace("-", "")


def normalize_symbol_input(raw_symbol: str) -> str:
    """
    Translate a raw symbol string into a canonical uppercase form.

    Rules (in order):
    1. Strip whitespace.
    2. Apply known aliases (^NSEI → NIFTY, etc.).
    3. Strip trailing `.NS` / `.BO` suffix (yfinance convention).
    4. Uppercase.
    """
    s = str(raw_symbol).strip()
    upper_s = s.upper()
    if upper_s in _SYMBOL_ALIASES:
        return _SYMBOL_ALIASES[upper_s]
    if s.upper().endswith(".NS"):
        s = s[:-3]
    elif s.upper().endswith(".BO"):
        s = s[:-3]
    return s.upper()


def _extract_underlying_from_trading_symbol(ts: str) -> str:
    """
    Extract the underlying symbol from a derivative trading symbol.

    E.g. "NIFTY24MAY25000CE" → "NIFTY",  "RELIANCE24MAY3200PE" → "RELIANCE".
    Matches leading alpha characters before any digit.
    """
    m = re.match(r'^([A-Z]+)', ts.strip().upper())
    return m.group(1) if m else ts.strip().upper()


# ---------------------------------------------------------------------------
# Native-format normalisers
# ---------------------------------------------------------------------------

_KITE_NATIVE_REQUIRED: frozenset[str] = frozenset({
    "instrument_token", "tradingsymbol", "instrument_type", "exchange",
})

_ANGELONE_NATIVE_REQUIRED: frozenset[str] = frozenset({
    "token", "symbol", "exch_seg",
})


def normalize_kite_native_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise a Kite Connect native instruments DataFrame to the canonical schema.

    Input: DataFrame from https://api.kite.trade/instruments
    Expected columns: instrument_token, exchange_token, tradingsymbol, name,
                      last_price, expiry, strike, tick_size, lot_size,
                      instrument_type, segment, exchange

    Returns a DataFrame with REQUIRED_COLUMNS. Empty on error.

    WARNING: Kite's instruments file has ~2M rows. Filter to NSE/NFO and
             required symbols before calling this function.
    """
    missing = [c for c in _KITE_NATIVE_REQUIRED if c not in df.columns]
    if missing:
        logger.warning("normalize_kite_native_df: missing required columns: %s", missing)
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    if df.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    df = df.copy().fillna("")

    def _symbol(row: pd.Series) -> str:
        itype = str(row.get("instrument_type", "")).strip().upper()
        name  = str(row.get("name", "")).strip()
        ts    = str(row.get("tradingsymbol", "")).strip()
        if itype in ("FUT", "CE", "PE") and name:
            return normalize_symbol_input(name)
        return normalize_symbol_input(ts)

    def _itype(row: pd.Series) -> str:
        itype   = str(row.get("instrument_type", "")).strip().upper()
        segment = str(row.get("segment", "")).strip().upper()
        name    = str(row.get("name", "")).strip()

        # INDICES segment = spot index (e.g. NIFTY 50 cash)
        if "INDICES" in segment:
            return "INDEX"
        if itype == "INDEX":
            return "INDEX"
        if itype == "FUT":
            underlying = normalize_symbol_input(name) if name else ""
            return "FUTIDX" if underlying in _NSE_INDICES else "FUTSTK"
        if itype in ("CE", "PE"):
            underlying = normalize_symbol_input(name) if name else ""
            return "OPTIDX" if underlying in _NSE_INDICES else "OPTSTK"
        return itype  # EQ, etc.

    def _option_type(itype_raw: str) -> str:
        t = str(itype_raw).strip().upper()
        return t if t in ("CE", "PE") else ""

    def _expiry(raw: str) -> str:
        val = str(raw).strip()
        if not val or val.lower() in ("", "nan", "none"):
            return ""
        try:
            date.fromisoformat(val)
            return val  # Already ISO
        except ValueError:
            pass
        try:
            return pd.to_datetime(val).strftime("%Y-%m-%d")
        except Exception:
            return ""

    rows = []
    for _, row in df.iterrows():
        raw_itype = str(row.get("instrument_type", "")).strip()
        rows.append({
            "provider":         "KITE",
            "exchange":         str(row.get("exchange", "")).strip().upper(),
            "symbol":           _symbol(row),
            "trading_symbol":   str(row.get("tradingsymbol", "")).strip(),
            "instrument_token": str(row.get("instrument_token", "0")).strip() or "0",
            "symbol_token":     "",
            "instrument_type":  _itype(row),
            "expiry":           _expiry(str(row.get("expiry", ""))),
            "strike":           str(row.get("strike", "0")).strip() or "0",
            "option_type":      _option_type(raw_itype),
            "lot_size":         str(row.get("lot_size", "0")).strip() or "0",
            "tick_size":        str(row.get("tick_size", "0.05")).strip() or "0.05",
        })

    if not rows:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    return pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))


def normalize_angelone_native_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise an Angel One scrip master DataFrame to the canonical schema.

    Expected columns (from Angel One developer portal / getAllScrip API):
      token      — symbol_token (required)
      symbol     — trading symbol, e.g. "NIFTY24MAY25000CE", "RELIANCE-EQ"  (required)
      exch_seg   — exchange segment, e.g. "NSE", "NFO"  (required)
      name       — underlying / company name (optional)
      expiry     — expiry date string, e.g. "29MAY2024" or "2024-05-29" (optional)
      strike     — strike price, "-1.0" for non-options (optional)
      lotsize    — lot size (optional)
      tick_size  — tick size (optional)
      instrumenttype — e.g. "OPTIDX", "FUTSTK", "EQ" (optional)

    Alternate column names accepted: symboltoken→token, lot_size→lotsize,
    instrument_type→instrumenttype, exchange→exch_seg.

    Returns a DataFrame with REQUIRED_COLUMNS. Empty on error.
    """
    # Accept alternate column names
    col_map = {
        "symboltoken": "token",
        "lot_size":    "lotsize",
        "instrument_type": "instrumenttype",
        "exchange":    "exch_seg",
    }
    df = df.copy().rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df = df.fillna("")

    missing = [c for c in _ANGELONE_NATIVE_REQUIRED if c not in df.columns]
    if missing:
        logger.warning("normalize_angelone_native_df: missing required columns: %s", missing)
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    if df.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    def _ao_symbol(row: pd.Series) -> str:
        itype = str(row.get("instrumenttype", "")).strip().upper()
        name  = str(row.get("name", "")).strip()
        ts    = str(row.get("symbol", "")).strip()

        if itype in ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
            # Prefer explicit name field if available
            if name:
                return normalize_symbol_input(name)
            return _extract_underlying_from_trading_symbol(ts)

        # EQ or INDEX: strip exchange suffix if present (e.g. "RELIANCE-EQ" → "RELIANCE")
        clean = re.sub(r'-(EQ|BL|BE|SM|MT)$', '', ts.upper())
        return normalize_symbol_input(clean)

    def _ao_itype(row: pd.Series) -> str:
        it  = str(row.get("instrumenttype", "")).strip().upper()
        seg = str(row.get("exch_seg", "")).strip().upper()
        ts  = str(row.get("symbol", "")).strip().upper()

        if it in ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK", "EQ"):
            return it
        if it == "INDEX":
            return "INDEX"
        # Try to infer from trading symbol suffix
        if ts.endswith("CE") or ts.endswith("PE"):
            underlying = _extract_underlying_from_trading_symbol(ts[:-2])
            return "OPTIDX" if underlying in _NSE_INDICES else "OPTSTK"
        if "FUT" in ts:
            return "FUTIDX"   # simplified; refine if needed
        if seg in ("NSE", "BSE") and not it:
            return "INDEX" if ts in _NSE_INDICES else "EQ"
        return it or "EQ"

    def _ao_option_type(row: pd.Series) -> str:
        it = str(row.get("instrumenttype", "")).strip().upper()
        ts = str(row.get("symbol", "")).strip().upper()
        if ts.endswith("CE"):
            return "CE"
        if ts.endswith("PE"):
            return "PE"
        return ""

    def _ao_expiry(raw: str) -> str:
        val = str(raw).strip()
        if not val or val.lower() in ("", "nan", "none", "-1"):
            return ""
        try:
            date.fromisoformat(val)
            return val
        except ValueError:
            pass
        # Try Angel One's DDMMMYYYY or DDMMYYYY formats
        for fmt in ("%d%b%Y", "%d%m%Y", "%Y%m%d"):
            try:
                return pd.to_datetime(val, format=fmt).strftime("%Y-%m-%d")
            except Exception:
                pass
        try:
            return pd.to_datetime(val).strftime("%Y-%m-%d")
        except Exception:
            return ""

    def _ao_strike(raw: str) -> str:
        val = str(raw).strip()
        try:
            s = float(val)
            return str(s) if s > 0 else "0"
        except (ValueError, TypeError):
            return "0"

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "provider":         "ANGELONE",
            "exchange":         str(row.get("exch_seg", "")).strip().upper(),
            "symbol":           _ao_symbol(row),
            "trading_symbol":   str(row.get("symbol", "")).strip(),
            "instrument_token": "0",  # Angel One doesn't use Kite-style integer token
            "symbol_token":     str(row.get("token", "")).strip(),
            "instrument_type":  _ao_itype(row),
            "expiry":           _ao_expiry(str(row.get("expiry", ""))),
            "strike":           _ao_strike(str(row.get("strike", ""))),
            "option_type":      _ao_option_type(row),
            "lot_size":         str(row.get("lotsize", "0")).strip() or "0",
            "tick_size":        str(row.get("tick_size", "0.05")).strip() or "0.05",
        })

    if not rows:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    return pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))


# ---------------------------------------------------------------------------
# Best-available loader — tries canonical then native provider files
# ---------------------------------------------------------------------------

def load_best_available_master(
    instruments_dir: "Path | str | None" = None,
) -> InstrumentMaster:
    """
    Load the best available instrument master from local files.

    Priority:
    1. data/instruments/instrument_master.csv  (canonical unified format)
    2. data/instruments/kite_instruments.csv   (Kite native format, auto-normalised)
       + data/instruments/angelone_instruments.csv (Angel One native, auto-normalised)
       Both are merged if both are present.
    3. Returns a not-loaded InstrumentMaster pointing at the canonical path if
       nothing is found.

    Parameters
    ----------
    instruments_dir : directory to search. Defaults to data/instruments/.
    """
    d = Path(instruments_dir) if instruments_dir else _INSTRUMENTS_DIR

    canonical   = d / "instrument_master.csv"
    kite_native = d / "kite_instruments.csv"
    ao_native   = d / "angelone_instruments.csv"

    # Canonical file takes priority
    if canonical.exists():
        return InstrumentMaster(path=canonical)

    # Try provider-specific native files
    frames: list[pd.DataFrame] = []
    sources: list[str] = []

    if kite_native.exists():
        try:
            raw = pd.read_csv(kite_native, dtype=str)
            norm = normalize_kite_native_df(raw)
            if not norm.empty:
                frames.append(norm)
                sources.append("kite_instruments.csv")
                logger.info(
                    "load_best_available_master: normalised %d Kite rows from %s",
                    len(norm), kite_native.name,
                )
        except Exception as exc:
            logger.warning("load_best_available_master: failed to load %s: %s", kite_native.name, exc)

    if ao_native.exists():
        try:
            raw = pd.read_csv(ao_native, dtype=str)
            norm = normalize_angelone_native_df(raw)
            if not norm.empty:
                frames.append(norm)
                sources.append("angelone_instruments.csv")
                logger.info(
                    "load_best_available_master: normalised %d Angel One rows from %s",
                    len(norm), ao_native.name,
                )
        except Exception as exc:
            logger.warning("load_best_available_master: failed to load %s: %s", ao_native.name, exc)

    if frames:
        merged = pd.concat(frames, ignore_index=True)
        return InstrumentMaster.from_dataframe(merged, source_name=" + ".join(sources))

    # Nothing found
    return InstrumentMaster(path=canonical)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-loaded)
# ---------------------------------------------------------------------------

_default_master: Optional[InstrumentMaster] = None


def get_instrument_master(path: "Path | str | None" = None) -> InstrumentMaster:
    """
    Return the module-level InstrumentMaster singleton.

    - First call: uses load_best_available_master() to find the best local file.
    - Subsequent calls: returns the cached singleton.
    - Pass a non-None path to bypass the cache and load a specific file directly
      (useful in tests; creates a fresh InstrumentMaster, no caching).
    """
    global _default_master
    if path is not None:
        return InstrumentMaster(path=path)
    if _default_master is None:
        _default_master = load_best_available_master()
    return _default_master

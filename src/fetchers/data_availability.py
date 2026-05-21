"""
Data availability checker — inspects which data layers are live for a given session.

Does NOT call any external APIs or Claude.
Inspects the _meta dict stored in the morning brief and local file presence.
Called by the dashboard to build the DATA AVAILABILITY section.

Usage:
    from src.fetchers.data_availability import check_data_availability
    report = check_data_availability(brief["_meta"], trading_date=date.today())
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.fetchers.intraday_provider import LocalCSVIntradayProvider, UnavailableIntradayProvider
from src.fetchers.broker_config import get_provider_config, get_configured_provider
from src.fetchers.instrument_master import get_instrument_master


# All data layers the system uses or could use, with metadata for the dashboard
DATA_LAYERS: list[dict] = [
    {
        "name":        "NSE Option Chain",
        "key":         "option_chain",
        "weight":      "Derivatives layer (25% confidence weight)",
        "required":    True,
        "description": "OI, PCR, max pain, ATM strike — backbone of derivatives analysis",
        "source":      "jugaad-data NSELive.index_option_chain()",
        "what_breaks": "Cannot compute PCR, max pain, OI levels — gate checks cannot run",
    },
    {
        "name":        "India VIX",
        "key":         "vix",
        "weight":      "VIX zone → stop-loss sizing",
        "required":    True,
        "description": "Determines SL width: VIX <17 = 40%, 17-22 = 50%, >22 = 60% of entry premium",
        "source":      "jugaad-data NSELive.all_indices()",
        "what_breaks": "SL defaults to 50% (elevated zone assumed) — position sizing is less accurate",
    },
    {
        "name":        "FII / DII Cash Market",
        "key":         "fii_dii",
        "weight":      "Institutional Flow layer (20% confidence weight)",
        "required":    True,
        "description": "Net cash-market buying/selling by Foreign and Domestic Institutional Investors",
        "source":      "NSE API /api/fiidiiTradeReact (unreliable pre-market; often returns 0)",
        "what_breaks": "Institutional layer (20%) unverifiable — confidence penalty of 0.5 pts applied",
    },
    {
        "name":        "Participant OI (Derivatives)",
        "key":         "participant_oi",
        "weight":      "Smart Money direction (~10% confidence weight)",
        "required":    True,
        "description": "FII/DII/PRO/Client futures & options positioning from NSE archives",
        "source":      "NSE archives CSV fao_participant_oi_{date}.csv (previous trading day only)",
        "what_breaks": "Smart money direction unconfirmed — confidence penalty of 0.5 pts applied",
    },
    {
        "name":        "Global Cues",
        "key":         "global_cues",
        "weight":      "Global trend layer (15% confidence weight)",
        "required":    False,
        "description": "S&P 500 futures, Nikkei, Hang Seng, USD/INR, crude oil overnight moves",
        "source":      "yfinance (ES=F, ^N225, ^HSI, CL=F, USDINR=X, GC=F)",
        "what_breaks": "Analysis proceeds with reduced global context; crude/FX signals absent",
    },
    {
        "name":        "Technical Indicators (Daily)",
        "key":         "technicals",
        "weight":      "Technical layer (20% confidence weight)",
        "required":    False,
        "description": "EMA-20/50/200, RSI-14, MACD, Bollinger Bands computed from 60-day daily bars",
        "source":      "yfinance 60d daily history (^NSEI, ^NSEBANK, .NS suffix for stocks)",
        "what_breaks": "Technical layer reduced in confidence; EMA trends and RSI unavailable",
    },
    {
        "name":        "Market News / Sentiment",
        "key":         "news",
        "weight":      "Sentiment layer (10% confidence weight)",
        "required":    False,
        "description": "ET Markets and Moneycontrol RSS headlines — Claude classifies sentiment",
        "source":      "RSS: economictimes.indiatimes.com, moneycontrol.com",
        "what_breaks": "Sentiment layer absent; Claude uses only quantitative signals",
    },
    {
        "name":        "Intraday Candles (5m / 15m / 30m)",
        "key":         "intraday",
        "weight":      "Outcome resolution only (not used for morning brief confidence)",
        "required":    False,
        "description": "Resolves whether SL or target was hit first when daily OHLC extremes are ambiguous",
        "source":      "Local CSV (data/intraday/) — Angel One SmartAPI / Kite Connect (not yet wired)",
        "what_breaks": "Ambiguous outcomes logged as OUTCOME_UNKNOWN — not counted in win rate. "
                       "Cannot confirm actual fill sequence without tick-level data.",
    },
    {
        "name":        "GIFT Nifty Futures",
        "key":         "gift_nifty",
        "weight":      "Pre-market gap indicator",
        "required":    False,
        "description": "Overnight GIFT Nifty level predicts NSE opening gap direction and magnitude",
        "source":      "Not available — GIFT Nifty is not on Yahoo Finance; requires dedicated API",
        "what_breaks": "S&P 500 futures used as partial proxy; opening-gap estimates are less precise",
    },
]


def check_data_availability(meta: dict, trading_date: date | None = None) -> dict:
    """
    Inspect a morning brief's _meta dict and local file presence to determine
    which data layers are available for the given trading session.

    Parameters
    ----------
    meta          : brief["_meta"] dict (or {} if no brief exists yet)
    trading_date  : date to check intraday CSVs for (defaults to today)

    Returns
    -------
    {
      "layers":             [{"name", "key", "available", "source", "what_breaks", ...}],
      "missing_required":   [str],   # names of required-but-unavailable layers
      "actionable":         bool,    # False if any required layer missing
      "intraday_provider":  str,     # human-readable name of the active provider
      "intraday_available": bool,
      "intraday_symbols":   [str],   # which symbols have CSV data today
      "checked_date":       str,     # ISO date string
      "provider_config":    dict,    # raw output of get_provider_config()
    }
    """
    if trading_date is None:
        trading_date = date.today()

    # --- Detect layer status from meta ---
    fii = meta.get("fii_dii", {})
    fii_ok = not (fii.get("fii_net_buy") == 0.0 and fii.get("dii_net_buy") == 0.0)

    poi = meta.get("participant_oi", {})
    poi_ok = (
        bool(poi)
        and not poi.get("error")
        and poi.get("fii_futures_bias", "UNKNOWN") != "UNKNOWN"
    )

    vix_ok = meta.get("vix_at_8am") is not None
    oc_ok  = meta.get("nifty_spot") is not None

    gc     = meta.get("global_cues", {})
    gc_ok  = bool(gc) and any(isinstance(v, dict) for v in gc.values())

    layer_status: dict[str, bool] = {
        "option_chain":   oc_ok,
        "vix":            vix_ok,
        "fii_dii":        fii_ok,
        "participant_oi": poi_ok,
        "global_cues":    gc_ok,
        "technicals":     True,   # yfinance rarely fails entirely; treated as available
        "news":           True,   # RSS — cannot check from meta; treated as available
        "gift_nifty":     False,  # never available (no API)
    }

    # --- Intraday check ---
    # File listing always uses LocalCSV (broker APIs cannot enumerate past-date files).
    # Active provider name comes from broker_config to show what will be used at runtime.
    prov_config    = get_provider_config()
    active_provider = get_configured_provider(prov_config)

    csv_provider    = LocalCSVIntradayProvider()
    intraday_symbols: list[str] = []
    if csv_provider.is_available():
        intraday_symbols = csv_provider.list_available(trading_date)
    intraday_available    = len(intraday_symbols) > 0
    layer_status["intraday"] = intraday_available

    intraday_provider_name = active_provider.name

    # --- Build per-layer result list ---
    layers_out = []
    for layer in DATA_LAYERS:
        key   = layer["key"]
        avail = layer_status.get(key, False)
        layers_out.append({
            **layer,
            "available": avail,
        })

    missing_required = [r["name"] for r in layers_out if r["required"] and not r["available"]]

    # --- Instrument master status ---
    master    = get_instrument_master()
    _provider = prov_config.get("provider", "csv")

    if master.is_loaded:
        _validation  = master.validate()
        _coverage    = master.coverage_report(_provider)
        _token_ready = {**_coverage.index_ready, **_coverage.equity_ready}
    else:
        _validation  = None
        _coverage    = None
        _token_ready = {"NIFTY": False, "BANKNIFTY": False}

    instrument_master_info = {
        "loaded":        master.is_loaded,
        "path":          str(master.path),
        "source_name":   master.source_name,
        "record_count":  master.record_count,
        "error":         master.error,
        "token_readiness":  _token_ready,
        "validation":    _validation,    # ValidationReport | None
        "coverage":      _coverage,      # CoverageReport   | None
    }

    return {
        "layers":             layers_out,
        "missing_required":   missing_required,
        "actionable":         len(missing_required) == 0,
        "intraday_provider":  intraday_provider_name,
        "intraday_available": intraday_available,
        "intraday_symbols":   intraday_symbols,
        "checked_date":       trading_date.isoformat(),
        "provider_config":    prov_config,
        "instrument_master":  instrument_master_info,
    }

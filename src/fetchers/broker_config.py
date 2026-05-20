"""
Broker data provider configuration.

Reads environment variables to determine which intraday provider to use.
CSV upload is always the safe fallback — broker providers require credentials.

Environment variables
---------------------
BROKER_PROVIDER       : csv | angelone | kite  (default: csv)
ANGELONE_API_KEY      : Angel One SmartAPI API key (from Smart API developer console)
ANGELONE_CLIENT_CODE  : Trading client code / user ID
ANGELONE_TOTP_SECRET  : Base32 TOTP secret for 2FA (optional if TOTP not enabled)
KITE_API_KEY          : Zerodha Kite Connect API key
KITE_ACCESS_TOKEN     : Kite session access token (rotates daily — must be refreshed)

WARNING: Never commit credentials to source control.
         Use a .env file (add to .gitignore) or OS-level environment variables.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = frozenset({"csv", "angelone", "kite"})

# Required credentials for each broker provider
_ANGELONE_REQUIRED_VARS = ("ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE")
_KITE_REQUIRED_VARS     = ("KITE_API_KEY", "KITE_ACCESS_TOKEN")


def get_provider_config() -> dict:
    """
    Read broker provider configuration from environment variables. Never raises.

    Returns
    -------
    {
      "provider":        str,   # normalised provider name ("csv"|"angelone"|"kite")
      "angelone_creds":  dict,  # {api_key, client_code, totp_secret}
      "kite_creds":      dict,  # {api_key, access_token}
      "angelone_ready":  bool,  # True when all required Angel One creds are set
      "kite_ready":      bool,  # True when all required Kite creds are set
      "missing_creds":   [str], # env var names missing for the chosen provider
      "fallback_active": bool,  # True when chosen provider is not ready → CSV fallback
    }
    """
    raw = os.environ.get("BROKER_PROVIDER", "csv").lower().strip()
    if raw not in SUPPORTED_PROVIDERS:
        logger.warning(
            "BROKER_PROVIDER=%r is not a supported value %s — falling back to csv.",
            raw, sorted(SUPPORTED_PROVIDERS),
        )
        raw = "csv"
    provider = raw

    angelone_creds = {
        "api_key":     os.environ.get("ANGELONE_API_KEY", ""),
        "client_code": os.environ.get("ANGELONE_CLIENT_CODE", ""),
        "totp_secret": os.environ.get("ANGELONE_TOTP_SECRET", ""),
    }
    kite_creds = {
        "api_key":      os.environ.get("KITE_API_KEY", ""),
        "access_token": os.environ.get("KITE_ACCESS_TOKEN", ""),
    }

    angelone_ready = bool(angelone_creds["api_key"] and angelone_creds["client_code"])
    kite_ready     = bool(kite_creds["api_key"] and kite_creds["access_token"])

    missing_creds: list[str] = []
    if provider == "angelone":
        for var in _ANGELONE_REQUIRED_VARS:
            if not os.environ.get(var, ""):
                missing_creds.append(var)
    elif provider == "kite":
        for var in _KITE_REQUIRED_VARS:
            if not os.environ.get(var, ""):
                missing_creds.append(var)

    fallback_active = (
        (provider == "angelone" and not angelone_ready)
        or (provider == "kite" and not kite_ready)
    )

    return {
        "provider":        provider,
        "angelone_creds":  angelone_creds,
        "kite_creds":      kite_creds,
        "angelone_ready":  angelone_ready,
        "kite_ready":      kite_ready,
        "missing_creds":   missing_creds,
        "fallback_active": fallback_active,
    }


def get_configured_provider(config: dict | None = None):
    """
    Return the IntradayProvider instance matching BROKER_PROVIDER env var.

    Selection logic (in priority order):
      1. angelone — if BROKER_PROVIDER=angelone AND credentials present AND package installed
      2. kite     — if BROKER_PROVIDER=kite AND credentials present AND package installed
      3. csv      — LocalCSVIntradayProvider (always available when data/intraday/ exists)
      4. unavailable — UnavailableIntradayProvider (never crashes; returns empty DataFrame)

    Parameters
    ----------
    config : optional pre-fetched dict from get_provider_config() — avoids re-reading env

    Returns
    -------
    IntradayProvider — never raises
    """
    # Lazy imports to avoid circular dependency at module level
    from src.fetchers.intraday_provider import (
        AngelOneIntradayProvider,
        KiteIntradayProvider,
        LocalCSVIntradayProvider,
        UnavailableIntradayProvider,
    )

    if config is None:
        config = get_provider_config()

    provider_name = config.get("provider", "csv")

    try:
        if provider_name == "angelone":
            if not config.get("angelone_ready"):
                logger.warning(
                    "BROKER_PROVIDER=angelone but missing credentials: %s — using CSV fallback.",
                    config.get("missing_creds", []),
                )
            else:
                p = AngelOneIntradayProvider(creds=config["angelone_creds"])
                if p.is_available():
                    logger.info("Active intraday provider: %s", p.name)
                    return p
                logger.warning(
                    "Angel One credentials present but provider unavailable (%s) — "
                    "install: pip install smartapi-python pyotp",
                    p.name,
                )

        elif provider_name == "kite":
            if not config.get("kite_ready"):
                logger.warning(
                    "BROKER_PROVIDER=kite but missing credentials: %s — using CSV fallback.",
                    config.get("missing_creds", []),
                )
            else:
                p = KiteIntradayProvider(creds=config["kite_creds"])
                if p.is_available():
                    logger.info("Active intraday provider: %s", p.name)
                    return p
                logger.warning(
                    "Kite credentials present but provider unavailable (%s) — "
                    "install: pip install kiteconnect",
                    p.name,
                )

    except Exception as exc:
        logger.warning(
            "Provider %r initialisation failed: %s — falling back to CSV.", provider_name, exc
        )

    # CSV fallback
    csv = LocalCSVIntradayProvider()
    if csv.is_available():
        logger.info("Active intraday provider: %s (CSV fallback)", csv.name)
        return csv

    return UnavailableIntradayProvider()

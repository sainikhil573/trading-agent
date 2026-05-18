"""
NSE data fetcher built on jugaad-data (handles NSE session auth internally).
"""

import logging
import pandas as pd
from jugaad_data.nse import NSELive

logger = logging.getLogger(__name__)


class NSEFetcher:
    def __init__(self):
        self._nse = NSELive()

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------

    def fetch_option_chain(self, symbol: str) -> dict:
        """
        Fetch raw option chain JSON.
        symbol: 'NIFTY' | 'BANKNIFTY' | any F&O stock ticker
        """
        if symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            raw = self._nse.index_option_chain(symbol)
        else:
            raw = self._nse.equities_option_chain(symbol)
        logger.info("Fetched option chain for %s", symbol)
        return raw

    def parse_option_chain(self, raw: dict, symbol: str) -> tuple[pd.DataFrame, dict]:
        """
        Parse raw option-chain JSON into a tidy DataFrame + metadata dict.

        DataFrame columns: strike, type, expiry, OI, chng_OI, volume,
                           IV, LTP, bid, ask
        Metadata keys: symbol, spot, atm_strike, nearest_expiry,
                       total_ce_oi, total_pe_oi, pcr
        """
        records  = raw.get("records", {})
        filtered = raw.get("filtered", {})

        spot          = records.get("underlyingValue", 0.0)
        expiry_dates  = records.get("expiryDates", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else None

        rows = []
        for entry in filtered.get("data", records.get("data", [])):
            strike = entry.get("strikePrice")
            expiry = entry.get("expiryDates") or entry.get("expiryDate")

            for opt_type in ("CE", "PE"):
                opt = entry.get(opt_type)
                if not opt:
                    continue
                rows.append({
                    "strike":  strike,
                    "type":    opt_type,
                    "expiry":  expiry,
                    "OI":      opt.get("openInterest", 0),
                    "chng_OI": opt.get("changeinOpenInterest", 0),
                    "volume":  opt.get("totalTradedVolume", 0),
                    "IV":      opt.get("impliedVolatility", 0.0),
                    "LTP":     opt.get("lastPrice", 0.0),
                    "bid":     opt.get("buyPrice1", 0.0),
                    "ask":     opt.get("sellPrice1", 0.0),
                })

        df = pd.DataFrame(rows)

        total_ce_oi = int(df[df["type"] == "CE"]["OI"].sum()) if not df.empty else 0
        total_pe_oi = int(df[df["type"] == "PE"]["OI"].sum()) if not df.empty else 0
        pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi else 0.0

        atm_strike = None
        if not df.empty and spot:
            strikes = df["strike"].unique()
            atm_strike = float(min(strikes, key=lambda s: abs(s - spot)))

        meta = {
            "symbol":          symbol,
            "spot":            spot,
            "atm_strike":      atm_strike,
            "nearest_expiry":  nearest_expiry,
            "total_ce_oi":     total_ce_oi,
            "total_pe_oi":     total_pe_oi,
            "pcr":             pcr,
        }

        return df, meta

    # ------------------------------------------------------------------
    # Max Pain
    # ------------------------------------------------------------------

    def calculate_max_pain(self, df: pd.DataFrame) -> float | None:
        """
        Max pain = strike at which total option writers' loss is minimised.
        """
        if df.empty:
            return None

        strikes = sorted(df["strike"].unique())
        ce_oi = df[df["type"] == "CE"].set_index("strike")["OI"]
        pe_oi = df[df["type"] == "PE"].set_index("strike")["OI"]

        min_pain, max_pain_strike = float("inf"), strikes[0]
        for s_exp in strikes:
            pain = sum(max(0, s_exp - s) * ce_oi.get(s, 0) for s in strikes) + \
                   sum(max(0, s - s_exp) * pe_oi.get(s, 0) for s in strikes)
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = s_exp

        return float(max_pain_strike)

    # ------------------------------------------------------------------
    # India VIX
    # ------------------------------------------------------------------

    def fetch_india_vix(self) -> float | None:
        """Return the latest India VIX value."""
        try:
            indices = self._nse.all_indices()
            for item in indices.get("data", []):
                if item.get("index") == "INDIA VIX":
                    return float(item["last"])
        except Exception as exc:
            logger.error("Failed to fetch India VIX: %s", exc)
        return None

    # ------------------------------------------------------------------
    # FII / DII  (cash-market via NSE archive API)
    # ------------------------------------------------------------------

    def fetch_fii_dii(self) -> dict:
        """
        Fetch latest FII/DII cash market net activity (in crores).
        Falls back to zeros when NSE does not publish today's data yet.
        """
        try:
            import requests
            url = "https://www.nseindia.com/api/fiidiiTradeReact"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.nseindia.com/",
                "Accept": "application/json",
            }
            # Reuse the jugaad session so cookies are already set
            resp = self._nse.s.get(url, headers=headers, timeout=15)
            rows = resp.json() if resp.ok else []
            if rows:
                row = rows[0]
                return {
                    "date":        row.get("date", ""),
                    "fii_net_buy": float(row.get("fiiNetDeal", 0)),
                    "dii_net_buy": float(row.get("diiNetDeal", 0)),
                }
        except Exception as exc:
            logger.error("Failed to fetch FII/DII data: %s", exc)
        return {"date": "", "fii_net_buy": 0.0, "dii_net_buy": 0.0}

    # ------------------------------------------------------------------
    # Top F&O stocks (by OI change)
    # ------------------------------------------------------------------

    def fetch_top_fno_stocks(self, top_n: int = 10) -> pd.DataFrame:
        """
        Return top F&O stocks ranked by traded volume (highest activity).
        live_fno returns equity-level data; OI per stock requires individual calls.
        """
        try:
            fno_data = self._nse.live_fno()
            rows = []
            for item in fno_data.get("data", []):
                try:
                    ltp = float(item.get("lastPrice", 0) or 0)
                    vol = int(item.get("totalTradedVolume", 0) or 0)
                    pct = float(item.get("pChange", 0) or 0)
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "symbol":     item.get("symbol", ""),
                    "LTP":        ltp,
                    "pct_change": pct,
                    "volume":     vol,
                })
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            return df.nlargest(top_n, "volume").reset_index(drop=True)
        except Exception as exc:
            logger.error("Failed to fetch top F&O stocks: %s", exc)
            return pd.DataFrame()

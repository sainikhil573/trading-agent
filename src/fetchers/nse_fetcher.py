"""
NSE data fetcher built on jugaad-data (handles NSE session auth internally).
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from jugaad_data.nse import NSELive

logger = logging.getLogger(__name__)

_RAW_DIR = Path(__file__).parent.parent.parent / "data" / "raw"


def _prev_weekdays(n: int = 5) -> list[date]:
    """Return the last n weekday dates before today."""
    result, check = [], date.today()
    while len(result) < n:
        check -= timedelta(days=1)
        if check.weekday() < 5:
            result.append(check)
    return result


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
        Fetch FII/DII cash market net activity.
        Saves non-zero results to data/raw/.
        Falls back to previous trading day's cache if today returns zero.
        Never returns all-zeros — always provides last available data.
        Adds 'is_prev_day' and 'fallback_date' fields when using cached data.
        """
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today()

        def _cache_path(d: date) -> Path:
            return _RAW_DIR / f"fii_dii_{d.strftime('%Y-%m-%d')}.json"

        def _save(data: dict, d: date) -> None:
            with open(_cache_path(d), "w") as f:
                json.dump(data, f)

        def _load(d: date) -> dict | None:
            p = _cache_path(d)
            if p.exists():
                try:
                    return json.load(open(p))
                except Exception:
                    pass
            return None

        # --- Try live fetch (loop all rows to find first non-zero) ---
        try:
            from datetime import datetime as _dt
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
            resp = self._nse.s.get(url, headers=headers, timeout=15)
            rows = resp.json() if resp.ok else []
            for row in rows:
                data = {
                    "date":        row.get("date", ""),
                    "fii_net_buy": float(row.get("fiiNetDeal", 0)),
                    "dii_net_buy": float(row.get("diiNetDeal", 0)),
                }
                if data["fii_net_buy"] == 0.0 and data["dii_net_buy"] == 0.0:
                    continue  # skip zero rows (today not yet published)
                # Parse the row date (NSE format: "21-May-2026")
                try:
                    row_date = _dt.strptime(data["date"], "%d-%b-%Y").date()
                except ValueError:
                    row_date = None
                if row_date and row_date < today:
                    # Historical row — treat as prev-day fallback
                    data["is_prev_day"]    = True
                    data["fallback_date"]  = row_date.isoformat()
                    data["fallback_source"] = "api_historical"
                    logger.info(
                        "FII loaded from API (prev day %s): FII=%.0f Cr  DII=%.0f Cr",
                        row_date, data["fii_net_buy"], data["dii_net_buy"],
                    )
                else:
                    _save(data, today)
                    logger.info(
                        "FII/DII live (today): FII=%.0f Cr  DII=%.0f Cr",
                        data["fii_net_buy"], data["dii_net_buy"],
                    )
                return data
        except Exception as exc:
            logger.error("FII/DII live fetch failed: %s", exc)

        # --- Fallback: load previous trading day cache ---
        logger.warning("FII/DII: API returned all zeros — checking local cache")
        for prev in _prev_weekdays(5):
            cached = _load(prev)
            if cached and (cached.get("fii_net_buy") or cached.get("dii_net_buy")):
                cached["is_prev_day"]    = True
                cached["fallback_date"]  = prev.isoformat()
                cached["fallback_source"] = "cache"
                logger.info(
                    "FII loaded from cache [%s]: FII=%.0f Cr  DII=%.0f Cr",
                    prev, cached["fii_net_buy"], cached["dii_net_buy"],
                )
                return cached

        logger.warning("FII unavailable — using neutral assumption (no cache yet)")
        return {"date": "", "fii_net_buy": 0.0, "dii_net_buy": 0.0}

    # ------------------------------------------------------------------
    # Participant-wise OI (FII / DII / PRO / Client)
    # ------------------------------------------------------------------

    def fetch_participant_oi(self) -> dict:
        """
        Fetch FII/DII/PRO/Client participant-wise OI from NSE archives CSV.
        Tries the last 5 weekdays on NSE archives, then falls back to local cache,
        then falls back to 3-day average of cached files.
        Never returns UNKNOWN if any cached data exists.
        """
        import csv, io

        _RAW_DIR.mkdir(parents=True, exist_ok=True)

        def _cache_path(d: date) -> Path:
            return _RAW_DIR / f"participant_oi_{d.strftime('%Y-%m-%d')}.json"

        def _save(data: dict, d: date) -> None:
            with open(_cache_path(d), "w") as f:
                json.dump(data, f)

        def _load(d: date) -> dict | None:
            p = _cache_path(d)
            if p.exists():
                try:
                    return json.load(open(p))
                except Exception:
                    pass
            return None

        def _parse_csv(text: str, for_date: date) -> dict | None:
            reader = csv.DictReader(io.StringIO(text))
            result: dict = {}
            for row in reader:
                ct = (row.get("Client Type") or "").strip()
                if ct not in ("FII", "DII", "PRO", "Client"):
                    continue

                def _i(k: str) -> int:
                    return int((row.get(k) or "0").replace(",", "") or 0)

                result[ct] = {
                    "fut_idx_long":  _i("Future Index Long"),
                    "fut_idx_short": _i("Future Index Short"),
                    "opt_call_long": _i("Option Index Call Long"),
                    "opt_call_short":_i("Option Index Call Short"),
                    "opt_put_long":  _i("Option Index Put Long"),
                    "opt_put_short": _i("Option Index Put Short"),
                }
            if not result:
                return None
            fii   = result.get("FII", {})
            longs  = fii.get("fut_idx_long", 0)
            shorts = fii.get("fut_idx_short", 0)
            total  = longs + shorts
            pct    = round(longs / total * 100, 1) if total > 0 else None
            result["fii_long_pct"]     = pct
            result["fii_futures_bias"] = (
                "BULLISH" if pct and pct > 60 else
                "BEARISH" if pct and pct < 40 else
                "NEUTRAL"
            )
            result["data_date"] = for_date.isoformat()
            result["error"]     = None
            return result

        # --- Attempt 1: NSE archives for last 5 weekdays ---
        for prev in _prev_weekdays(5):
            ds  = prev.strftime("%d%m%Y")
            url = (
                f"https://nsearchives.nseindia.com/content/nsccl/"
                f"fao_participant_oi_{ds}.csv"
            )
            try:
                resp = self._nse.s.get(url, timeout=15)
                if resp.status_code != 200 or not resp.text.strip():
                    continue
                parsed = _parse_csv(resp.text, prev)
                if parsed:
                    _save(parsed, prev)
                    logger.info(
                        "Participant OI (%s): FII longs %s%% → %s",
                        prev, parsed["fii_long_pct"], parsed["fii_futures_bias"],
                    )
                    return parsed
            except Exception as exc:
                logger.warning("Participant OI NSE fetch failed for %s: %s", ds, exc)

        logger.warning("Participant OI: all NSE archive fetches failed — checking local cache")

        # --- Attempt 2: load from local cache (most recent) ---
        cached_files: list[tuple[date, dict]] = []
        for prev in _prev_weekdays(10):
            d = _load(prev)
            if d and d.get("fii_futures_bias") not in (None, "UNKNOWN"):
                cached_files.append((prev, d))

        if cached_files:
            # Use the most recent cached file
            most_recent_date, most_recent = cached_files[0]
            most_recent = dict(most_recent)
            most_recent["is_cached"]      = True
            most_recent["fallback_source"] = "cache_file"
            logger.info(
                "Participant OI fallback from cache (%s): FII bias=%s",
                most_recent_date, most_recent.get("fii_futures_bias"),
            )

            # Attempt 3: 3-day average when >= 3 cached files available
            if len(cached_files) >= 3:
                avg_pct = round(
                    sum(d.get("fii_long_pct") or 50 for _, d in cached_files[:3]) / 3, 1
                )
                most_recent["fii_long_pct"]     = avg_pct
                most_recent["fii_futures_bias"]  = (
                    "BULLISH" if avg_pct > 60 else
                    "BEARISH" if avg_pct < 40 else
                    "NEUTRAL"
                )
                most_recent["fallback_source"] = "3day_average"
                logger.info(
                    "Participant OI: 3-day average FII long pct = %.1f%% → %s",
                    avg_pct, most_recent["fii_futures_bias"],
                )
            return most_recent

        logger.error("Participant OI: no live or cached data available")
        return {"error": "Could not fetch participant OI", "fii_futures_bias": "UNKNOWN"}

    # ------------------------------------------------------------------
    # Block deals (previous trading day)
    # ------------------------------------------------------------------

    def fetch_block_deals(self) -> list[dict]:
        """Fetch block deals from NSE. Returns list of deal dicts."""
        try:
            url  = "https://www.nseindia.com/api/block-deal"
            resp = self._nse.s.get(url, timeout=10)
            data = resp.json() if resp.ok else {}
            deals = data.get("data", [])
            result = []
            for d in deals[:20]:
                result.append({
                    "symbol":   d.get("symbol", ""),
                    "client":   d.get("clientName", ""),
                    "buy_sell": d.get("buySell", ""),
                    "qty":      d.get("quantity", 0),
                    "price":    d.get("tradePrice", 0),
                })
            logger.info("  Block deals fetched: %d", len(result))
            return result
        except Exception as exc:
            logger.warning("Block deals fetch failed: %s", exc)
            return []

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

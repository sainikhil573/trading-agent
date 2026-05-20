# Broker Data Provider Integration

Intraday candle data is used to resolve trade outcomes chronologically (SL vs target hit order).
Three providers are supported; CSV upload is always the safe fallback.

---

## Providers at a Glance

| Provider      | Source              | Status       | Credentials required          |
|---------------|---------------------|--------------|-------------------------------|
| `csv`         | Local files         | Fully working | None                         |
| `angelone`    | Angel One SmartAPI  | Stub ready   | `ANGELONE_API_KEY`, `ANGELONE_CLIENT_CODE` |
| `kite`        | Zerodha Kite Connect | Stub ready  | `KITE_API_KEY`, `KITE_ACCESS_TOKEN`       |

**CSV upload is always active as a fallback.** If broker credentials are missing or the
required package is not installed, the system silently uses CSV (or marks ambiguous
outcomes as OUTCOME_UNKNOWN if no CSV is present).

---

## Environment Variables

Set these in your OS environment or in a `.env` file (never commit `.env` to source control).

### Selecting the provider

```
BROKER_PROVIDER=csv          # default — local CSV files in data/intraday/
BROKER_PROVIDER=angelone     # Angel One SmartAPI (requires credentials below)
BROKER_PROVIDER=kite         # Zerodha Kite Connect (requires credentials below)
```

### Angel One SmartAPI

```
ANGELONE_API_KEY=<your-smartapi-api-key>
ANGELONE_CLIENT_CODE=<your-client-code>
ANGELONE_TOTP_SECRET=<base32-totp-secret>   # optional — only if TOTP is enabled
```

Get your API key from the Angel One Smart API developer console:
https://smartapi.angelbroking.com/

### Zerodha Kite Connect

```
KITE_API_KEY=<your-kite-api-key>
KITE_ACCESS_TOKEN=<your-session-access-token>   # rotates daily — must be refreshed each morning
```

Get your API key from the Kite Connect developer console:
https://kite.trade/

The access token must be regenerated each trading day using the Kite login flow.

---

## Setup Steps

### Option 1 — CSV Upload (default, no credentials needed)

1. Run the dashboard: `streamlit run main.py`
2. Open the **INTRADAY DATA** expander
3. Upload a CSV file following the schema in `data/intraday/schema_example.csv`
4. The file is saved automatically and used for outcome resolution

### Option 2 — Angel One SmartAPI

1. Create a SmartAPI developer account and generate an API key
2. Install the required package: `pip install smartapi-python pyotp`
3. Set environment variables (in `.env` or OS):
   ```
   BROKER_PROVIDER=angelone
   ANGELONE_API_KEY=...
   ANGELONE_CLIENT_CODE=...
   ANGELONE_TOTP_SECRET=...   # if 2FA is enabled on your account
   ```
4. The provider will appear as active in the dashboard's `[PROVIDER]` line
5. **Current limitation**: symbol→token lookup is not yet wired. The provider is
   credential-aware and the normalisation layer is fully tested, but live API calls
   require completing `AngelOneIntradayProvider._fetch()` with a token table.

### Option 3 — Zerodha Kite Connect

1. Create a Kite Connect developer account and generate an API key
2. Install the required package: `pip install kiteconnect`
3. Set environment variables:
   ```
   BROKER_PROVIDER=kite
   KITE_API_KEY=...
   KITE_ACCESS_TOKEN=...   # must be refreshed daily via Kite login flow
   ```
4. **Current limitation**: instrument token lookup is not yet wired. Same status as Angel One above.

---

## Credential Safety

- **Never commit secrets** to source control (`.env`, `.py` files, Jupyter notebooks)
- Add `.env` to `.gitignore` before creating it
- Rotate API keys immediately if accidentally exposed
- Access tokens (Kite) expire daily — this is a feature, not a bug
- TOTP secrets (Angel One) are long-lived — treat them like passwords

---

## Fallback Behaviour

If a broker provider is configured but unavailable, the system **always** falls back
rather than crashing:

```
BROKER_PROVIDER=angelone, no package installed
  → logs warning "provider unavailable"
  → falls back to LocalCSV → or UnavailableProvider if no CSV files exist

BROKER_PROVIDER=angelone, package installed, credentials present
  → _fetch() called → logs "not yet implemented" → returns empty DataFrame
  → evaluation uses DAILY_OHLC → ambiguous outcomes marked OUTCOME_UNKNOWN
```

The dashboard's `[PROVIDER]` line and `[CRED WARN]` banner make the fallback visible.

---

## Response Normalisation

Both broker providers normalise their raw API responses to the standard candle schema:

```
symbol, datetime, open, high, low, close, volume, timeframe
```

The normalisation functions are module-level and fully tested without real credentials:

```python
from src.fetchers.intraday_provider import normalize_angelone_candles, normalize_kite_candles

# Angel One format: [[timestamp_iso8601, open, high, low, close, volume], ...]
df = normalize_angelone_candles(raw_candles, symbol="NIFTY", timeframe="5m")

# Kite format: [[datetime_str, open, high, low, close, volume], ...]
df = normalize_kite_candles(raw_candles, symbol="NIFTY", timeframe="5m")
```

---

## Limitations

| Limitation | Impact |
|---|---|
| Angel One `_fetch()` not wired | Returns empty DataFrame; use CSV upload instead |
| Kite `_fetch()` not wired | Same as above |
| Kite access token rotates daily | Requires daily manual refresh or automation |
| Angel One symbol→token lookup not implemented | Cannot resolve NSE F&O symbols to API tokens |
| Kite instrument token lookup not implemented | Same — needs an instrument CSV download |
| No rate-limit handling | API quotas are not yet respected |
| No retry logic | Transient failures return empty DataFrame immediately |

These limitations are by design — the architecture is ready; the final wiring requires
live API access and is deferred until credentials are available for testing.

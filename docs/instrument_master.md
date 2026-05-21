# Instrument Master — Token Mapping Guide

## Why tokens are required

Broker candle APIs (Angel One SmartAPI, Zerodha Kite Connect) do not accept
human-readable symbols like "NIFTY" or "RELIANCE" as API parameters. Each
exchange instrument is identified by an opaque numeric or string token that
the broker assigns. You must resolve the symbol to its token before calling
any historical candle endpoint.

- **Angel One** uses `symbol_token` (a string, typically a numeric code like `"26000"`)
- **Kite Connect** uses `instrument_token` (a positive integer, e.g. `256265`)

Wrong or guessed tokens will either silently return incorrect data or cause
the API to return a 400 / `InvalidInstrumentToken` error.

---

## Expected file location

```
data/instruments/instrument_master.csv
```

This file is **not committed to the repository** (it is gitignored). You must
download it from your broker and place it here.

---

## Required CSV schema

```
provider,exchange,symbol,trading_symbol,instrument_token,symbol_token,
instrument_type,expiry,strike,option_type,lot_size,tick_size
```

See `data/instruments/schema_example.csv` for a template showing the exact
column layout with placeholder (zero / empty) tokens.

### Column definitions

| Column           | Type   | Description |
|------------------|--------|-------------|
| provider         | string | `kite` or `angelone` |
| exchange         | string | `NSE`, `NFO`, `BSE`, `INDICES` |
| symbol           | string | Underlying symbol, e.g. `NIFTY`, `RELIANCE` |
| trading_symbol   | string | Broker-specific symbol string, e.g. `NIFTY 50`, `RELIANCE` |
| instrument_token | int    | **Kite only** — positive integer. Leave 0 or blank for Angel One rows |
| symbol_token     | string | **Angel One only** — numeric string e.g. `"26000"`. Leave blank for Kite rows |
| instrument_type  | string | `INDEX`, `FUTIDX`, `OPTSTK`, `OPTIDX`, `EQ`, `FUTSTK` |
| expiry           | string | ISO date `YYYY-MM-DD` for futures/options, blank for equity/index |
| strike           | float  | Strike price for options (e.g. `25000.0`), blank or `0` for others |
| option_type      | string | `CE` or `PE` for options, blank for others |
| lot_size         | int    | Contract lot size (e.g. `75` for NIFTY, `35` for BANKNIFTY) |
| tick_size        | float  | Minimum price tick (typically `0.05` for F&O) |

---

## Kite Connect vs Angel One token differences

### Kite Connect

- Token field: `instrument_token` (integer)
- Download the complete instrument list as a CSV from:
  `https://api.kite.trade/instruments` (unauthenticated — public endpoint)
- The file has ~2M rows. Filter to `NSE` / `NFO` exchange and required symbols.
- Tokens are stable across sessions but may change on corporate actions / expiry rollovers.
- Example: NIFTY 50 index = `256265`

### Angel One SmartAPI

- Token field: `symbol_token` (string)
- Download from the Angel One developer portal or use the
  `getInstrumentsBySymbol()` API call (requires API key).
- Angel One also calls this field `"symboltoken"` or `"token"` depending on the endpoint.
- Example: NIFTY = `"26000"`, BANKNIFTY = `"26009"`

---

## How to build the instrument master file

### Kite

1. Download `https://api.kite.trade/instruments` (no auth needed).
2. Filter rows to `exchange IN (NSE, NFO)` and your required symbols.
3. Select columns: `tradingsymbol`, `instrument_token`, `exchange`, `instrument_type`,
   `expiry`, `strike`, `option_type`, `lot_size`, `tick_size`.
4. Rename to match the schema above; add `provider=kite` and `symbol` columns.
5. For option rows, `symbol` = the underlying name (e.g. `NIFTY`).

### Angel One

1. Use `SmartConnect.getInstrumentsBySymbol(exchange, symbol)` or download the
   Angel One scrip master CSV from the developer console.
2. Map `symboltoken` → `symbol_token`; add `provider=angelone`.
3. The instrument file is refreshed periodically — re-download monthly or after
   each expiry cycle.

---

## Limitations

- **Tokens change on expiry** — option contract tokens change every week/month.
  You must rebuild the option rows in `instrument_master.csv` before each expiry.
- **Index candle tokens** — for Kite, the NIFTY 50 index candle token (`256265`)
  is for the *cash index*. For option chain purposes you need NFO tokens.
- **Partial master** — the app looks up only the symbols it needs. You do not
  need to include every NSE instrument, only the ones in your F&O universe and
  the indices you want candles for.
- **No automatic refresh** — this system does not auto-download or rotate tokens.
  Stale tokens cause silent fetch failures. Check expiry dates regularly.

---

## WARNING: Do not fake or hardcode tokens

Guessed or invented tokens will cause one of:
- A silent wrong-instrument fetch (data from a completely different contract)
- A 400 / `InvalidInstrumentToken` error from the broker
- Data that passes schema validation but is factually incorrect

The system will return an empty DataFrame and log a warning rather than
fetch with a missing token. An empty DataFrame causes the outcome to be
classified as `OUTCOME_UNKNOWN`, which is honest and safe.

---

## Dashboard display

The dashboard DATA AVAILABILITY section shows:

- **[INSTRUMENT MASTER]** — loaded / not loaded, record count, file path
- **[TOKEN READINESS]** — whether NIFTY and BANKNIFTY tokens exist for the active provider

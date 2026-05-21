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

## File locations

The system looks for instrument files in `data/instruments/`. Three files are supported:

| File | Format | Priority |
|------|--------|----------|
| `data/instruments/instrument_master.csv` | Canonical (this project's schema) | **Highest** — used if present |
| `data/instruments/kite_instruments.csv` | Kite Connect native format | Auto-normalised if canonical absent |
| `data/instruments/angelone_instruments.csv` | Angel One scrip master format | Auto-normalised if canonical absent |

All three files are gitignored. You must download from your broker and place locally.

---

## Canonical CSV schema

```
provider,exchange,symbol,trading_symbol,instrument_token,symbol_token,
instrument_type,expiry,strike,option_type,lot_size,tick_size
```

See `data/instruments/schema_example.csv` for a column layout template with
placeholder (zero / empty) tokens.

### Column definitions

| Column           | Type   | Description |
|------------------|--------|-------------|
| provider         | string | `kite` or `angelone` |
| exchange         | string | `NSE`, `NFO`, `BSE`, `INDICES` |
| symbol           | string | Underlying symbol, e.g. `NIFTY`, `RELIANCE` |
| trading_symbol   | string | Broker-specific display symbol |
| instrument_token | int    | **Kite only** — positive integer. Leave 0 for Angel One rows |
| symbol_token     | string | **Angel One only** — numeric string e.g. `"26000"`. Leave blank for Kite rows |
| instrument_type  | string | `INDEX`, `FUTIDX`, `OPTIDX`, `OPTSTK`, `EQ`, `FUTSTK` |
| expiry           | string | ISO date `YYYY-MM-DD` for futures/options; blank for equity/index |
| strike           | float  | Strike price for options (e.g. `25000.0`); blank or `0` otherwise |
| option_type      | string | `CE` or `PE` for options; blank otherwise |
| lot_size         | int    | Contract lot size (e.g. `75` for NIFTY, `35` for BANKNIFTY) |
| tick_size        | float  | Minimum price tick (typically `0.05` for F&O) |

---

## Kite Connect vs Angel One token differences

### Kite Connect

- Token field: `instrument_token` (positive integer)
- Download: `https://api.kite.trade/instruments` (public, no auth needed)
- The file has ~2M rows — filter to NSE/NFO and your symbols before using
- Tokens are stable within a session; may change after corporate actions or expiry rollovers
- NIFTY 50 index token example: `256265`
- Native format columns: `instrument_token, exchange_token, tradingsymbol, name, last_price, expiry, strike, tick_size, lot_size, instrument_type, segment, exchange`

### Angel One SmartAPI

- Token field: `symbol_token` (string, often a numeric code)
- Download: Angel One developer portal scrip master CSV, or `SmartConnect.getInstrumentsBySymbol()`
- The field is also referred to as `"token"` or `"symboltoken"` in different API responses
- NIFTY example: `"26000"`, BANKNIFTY example: `"26009"`
- Native format columns: `token, symbol, name, expiry, strike, lotsize, instrumenttype, exch_seg, tick_size`

---

## How to set up the instrument master

### Option A — Build the canonical file manually

1. Copy `data/instruments/schema_example.csv` as `instrument_master.csv`.
2. Add one row per instrument you need (NIFTY index, BANKNIFTY index, active option expiry, key stocks).
3. Fill in the real token from your broker for each row.
4. Validate:

```
python scripts/check_instrument_master.py
```

### Option B — Convert Kite's native instruments CSV

1. Download `https://api.kite.trade/instruments` and save as `data/instruments/kite_instruments.csv`.
2. Run:

```
python scripts/check_instrument_master.py \
    --kite-file data/instruments/kite_instruments.csv \
    --output data/instruments/instrument_master.csv
```

> **Note**: Kite's file is ~2M rows. The normaliser processes all of them but the
> resulting canonical file will be large. Consider pre-filtering to NSE/NFO rows for
> your symbols before converting.

### Option C — Convert Angel One scrip master

```
python scripts/check_instrument_master.py \
    --angelone-file data/instruments/angelone_instruments.csv \
    --output data/instruments/instrument_master.csv
```

### Option D — Merge both providers

```
python scripts/check_instrument_master.py \
    --kite-file   data/instruments/kite_instruments.csv \
    --angelone-file data/instruments/angelone_instruments.csv \
    --output data/instruments/instrument_master.csv
```

---

## Validation helper script

```
python scripts/check_instrument_master.py [options]
```

| Flag | Effect |
|------|--------|
| (none) | Validate auto-detected instrument master, print coverage report |
| `--file FILE` | Validate a specific canonical CSV |
| `--kite-file FILE` | Normalise Kite native CSV (use with `--output` to save) |
| `--angelone-file FILE` | Normalise Angel One CSV |
| `--output FILE` | Write normalised output to this path |
| `--provider kite\|angelone` | Restrict coverage report to one provider |
| `--verbose` | Show all individual warnings (missing tokens, expired contracts) |
| `--no-color` | Disable ANSI colour output |

The script reports:
- Total rows, valid rows, structural errors
- Missing token count and symbols
- Expired option contract count
- Per-provider index and F&O stock token readiness
- Coverage breakdown by instrument type and exchange

Exit codes: `0` = clean, `1` = warnings/errors, `2` = file not found.

---

## Weekly/monthly option expiry refresh

Option contract tokens expire every week (weekly expiry — NIFTY, BANKNIFTY) or month.
The token for a May-29 NIFTY 25000CE is **different** from the June-5 token.

**Before each expiry:**
1. Determine the new active expiry date.
2. Add or replace option rows in `instrument_master.csv` with the new expiry and tokens.
3. Run `python scripts/check_instrument_master.py` to confirm no expired rows remain.

The validator flags expired contracts with `[EXPIRED OPTIONS]` in the dashboard and
`[WARN]` messages in the CLI output.

---

## Auto-loading behaviour at runtime

When the application starts, `get_instrument_master()` calls `load_best_available_master()`:

1. If `data/instruments/instrument_master.csv` exists → load it directly
2. If not, try `kite_instruments.csv` + `angelone_instruments.csv` → normalise and merge
3. If nothing found → return a not-loaded master (broker API fetch returns empty DataFrame)

Restarting Streamlit picks up any newly placed files (singleton is created at import time).

---

## Limitations

- **Tokens change on expiry** — must refresh option rows before each expiry cycle
- **Kite INDICES segment** — NIFTY 50 spot-index candles use a different token than NFO futures/options
- **Angel One format variation** — column names differ between API versions; the normaliser handles the most common variant
- **No automatic download** — tokens are never fetched automatically; you must place files manually
- **No singleton refresh** — placing a new file after Streamlit starts requires a restart

---

## WARNING: Do not fake or hardcode tokens

Guessed or invented tokens will cause one of:
- Silent wrong-instrument fetch (data from a completely different contract)
- A 400 / `InvalidInstrumentToken` error from the broker
- Data that passes schema validation but is factually incorrect

The system returns an empty DataFrame and logs a warning rather than
fetch with a missing token. An empty DataFrame causes the outcome to be
classified as `OUTCOME_UNKNOWN` — honest and safe.

---

## Dashboard display

The DATA AVAILABILITY section shows:

| Line | Description |
|------|-------------|
| `[INSTRUMENT MASTER]` | Loaded / not loaded, record count, source file name |
| `[TOKEN READINESS]` | Per-symbol token status for indices and key F&O stocks |
| `[IM COVERAGE]` | Row count and breakdown by instrument type for active provider |
| `[EXPIRED OPTIONS]` | Warning if any option contracts in master have passed expiry |
| `[IM VALIDATION]` | Error / missing-token / expired-option counts with CLI hint |

# Candle Management Reference

This reference covers candle import and management operations in Jesse.

## Data Requirements

Historical candle data is required for backtesting strategies. Import data for all route exchanges, symbols, and timeframes before running backtests.

## Import Process

DO NOT pre-check candle availability before running a backtest. Run the
backtest first and only import on a missing-data error. Pre-checking with
`get_existing_candles()` wastes time and tokens, and the backtest engine
itself is the authoritative source of whether the required data is present
for a given route, timeframe, and date range.

Correct flow:

1. Run the backtest (see `jesse://backtest_management`).
2. If — and only if — it fails with a missing-candle error, call
   `import_candles()` starting ~2 months before the user's `start_date`.
3. Poll `get_candle_import_status(import_id)` until `"finished"`, `"failed"`, or `"cancelled"`. After `"finished"`,
   retry the backtest.
4. Use `get_existing_candles()` only for explicit user-driven inspection
   (e.g. "what data do I have?"), never as a pre-flight gate.

## Tool Reference

### get_existing_candles()

Checks what candle data is currently available in the database.

**Returns:** List of available candle datasets with exchange, symbol, timeframe, and date ranges.

### search_symbols()

Finds importable symbols on one candle source. Use it when the user names an instrument
("Microsoft", "crude oil", "the S&P 500 index") instead of an exact Jesse symbol, or when an
import fails with a symbol-not-found error.

**Parameters:**
- `exchange`: Candle source name exactly as Jesse lists it (e.g., "Massive Stocks")
- `query`: Ticker prefix or part of the instrument name (e.g., "MSFT", "microsoft")
- `limit` (optional): Maximum matches, default 20, maximum 200

**Ranking:** ticker prefixes first, then symbols whose provider name contains the query.

**Returns:** `matches`, each with `symbol` plus any provider details:

```python
search_symbols(exchange="Massive Stocks", query="microsoft")
# {"status": "success", "match_count": 3, "catalog_size": 13152, "matches": [
#   {"symbol": "MSFT-USD", "name": "Microsoft Corp", "kind": "Common Stock", "venue": "NASDAQ"},
#   {"symbol": "MSFX-USD", "name": "T-Rex 2X Long Microsoft Daily Target ETF", "kind": "ETF", "venue": "Cboe BZX"},
#   ...
# ]}
```

Source-specific behavior:
- Crypto exchanges (e.g., "Binance Perpetual Futures") match tickers only and return bare
  `{"symbol": ...}` entries. Searching "bitcoin" there returns nothing; search "BTC".
- "Massive Stocks" (stocks and ETFs), "Massive Currencies" (forex and crypto pairs),
  "Massive Indices", and "Massive Futures" also match names, and entries carry `name`,
  `kind`, `venue`, and for futures `expiry`.
- Every source has its own catalogue. Microsoft the company is `MSFT-USD` on Massive Stocks;
  Massive Futures instead lists CME stock futures on Microsoft such as `SMSFTU6-USD`
  ("Microsoft Corp Stock Futures", expires 2026-09-18). Choose the source that matches the
  user's intent and pass the returned `symbol` to `import_candles()` verbatim.
- Massive sources require a stored Massive API key; see `jesse://credentials`.

### import_candles()

Imports historical candle data from exchanges.

**Parameters:**
- `exchange`: Exchange name (e.g., "Binance Perpetual Futures")
- `symbol`: Trading pair (e.g., "BTC-USDT")
- `start_date`: Start date in YYYY-MM-DD format
- `import_id` (optional): Import ID for retrying failed imports

**Supported Timeframes:**
1m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 3h, 4h, 6h, 8h, 12h, 1D, 3D, 1W, 1M

**Returns:** Import result with status and import ID

## Usage Examples

### Basic Import
```python
result = import_candles(
    exchange="Binance Spot",
    symbol="BTC-USDT",
    start_date="2024-01-01"
)
```

### Retry Failed Import
```python
# First attempt
result = import_candles(
    exchange="Binance Spot",
    symbol="ETH-USDT",
    start_date="2024-01-01"
)

# If failed, retry with same import_id
if result.get("status") != "success":
    import_id = result.get("import_id")
    retry_result = import_candles(
        exchange="Binance Spot",
        symbol="ETH-USDT",
        start_date="2024-01-01",
        import_id=import_id
    )
```

## Retry Behavior

When retrying imports with the same `import_id`:

- Previous events are automatically cleared
- Import resumes from the failure point
- Already-imported candles are skipped
- Progress monitoring starts fresh but continues efficiently
- WebSocket events are isolated per retry

## Success Response Format

```
"Successfully imported candles since '2024-01-01' until today (2.1 days imported, 1.2 days already existed in the database)."
```

The message shows both newly imported data and pre-existing data that was skipped.

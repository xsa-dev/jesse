# Credential Management Reference

Jesse stores two kinds of credentials locally, both editable from the Dashboard and from MCP:

- **Exchange API keys** — used by Jesse Live to trade on a crypto exchange.
- **Data provider credentials** — used for historical imports; today this is the single
  Massive API key shared by Massive Stocks, Massive Currencies, Massive Indices, and
  Massive Futures.

No tool ever returns a stored secret. Read tools return masked values or configuration status.

## Conduct

- Only store a secret the user explicitly pasted in this conversation. Never invent, reuse, or
  infer one, and never repeat a full key or secret back to the user; responses are masked.
- Massive keeps one key: if `store_data_provider_credentials()` reports an existing key, ask
  the user before deleting it and storing the new one, then validate and report the outcome.
- Confirm before deleting any exchange API key; a live session cannot restart without it.

## Exchange API keys

### get_exchange_api_keys()

**Returns:** `data`, a list of `{id, exchange, name, api_key, api_secret, created_at, ...}` with
masked secrets (`abcd***...***wxyz`). Use `id` to delete a key.

### store_exchange_api_key(exchange, name, api_key, api_secret, additional_fields=None)

- `exchange` must be a live-trading exchange name exactly as Jesse lists it, e.g.
  "Binance Perpetual Futures", "Bybit USDT Perpetual", "Hyperliquid Perpetual".
  Historical-only sources such as "Massive Stocks" are rejected; use the data provider tools.
- `name` must be unique among stored keys.
- `additional_fields` is only needed by exchanges with extra secrets:

| Exchange | Required additional fields |
|----------|----------------------------|
| Apex     | `api_passphrase`, `wallet_address`, `stark_private_key` |
| KuCoin   | `api_passphrase` |
| Lighter  | `api_key_index` |

**Returns:** the stored entry with masked values.

### delete_exchange_api_key(id)

Deletes one stored key. Confirm with the user first; a live session using that key cannot be
started again until a replacement is stored.

## Data provider credentials

### get_data_provider_credentials()

**Returns:** `data`, one status per provider: `{provider_id, name, configured, created_at,
updated_at, credential_fields}`, plus `configured_providers` for convenience.

### store_data_provider_credentials(api_key, provider_id="Massive")

Saves the provider key. Jesse keeps exactly one key per provider and refuses to overwrite it
(HTTP 409): delete the existing key first. Follow up with `validate_data_provider_credentials()`.

### delete_data_provider_credentials(provider_id="Massive")

Removes the key. Candles already imported stay in the database; new Massive imports and
`search_symbols()` on Massive sources fail with an authentication error until a key is stored.

### validate_data_provider_credentials(provider_id="Massive")

Performs a live check against the provider using a fixed free-plan endpoint. Reports
`status: "success"` or the provider's rejection (invalid key, missing entitlement, rate limit,
provider unavailable) without returning the key or the raw provider response.

## Workflow: "my Massive imports fail with an authentication error"

1. `get_data_provider_credentials()` — is Massive configured?
2. If not, ask the user for their key, call `store_data_provider_credentials(api_key)`.
3. If it is configured, `validate_data_provider_credentials()` and report the result.
4. If the user supplies a new key: `delete_data_provider_credentials()`, then store, then validate.

"""
Jesse Credential Management Tools

MCP tools for the credentials the Dashboard manages under "Exchange API Keys" and
"Data Providers":

- get_exchange_api_keys: List stored exchange API keys (masked)
- store_exchange_api_key: Add an exchange API key for Jesse Live
- delete_exchange_api_key: Remove an exchange API key
- get_data_provider_credentials: Show which data providers (e.g. Massive) are configured
- store_data_provider_credentials: Save a data provider API key
- delete_data_provider_credentials: Remove a data provider API key
- validate_data_provider_credentials: Test the stored data provider key against the provider

Secrets are write-only: no tool ever returns a stored key.
All tools require authentication via Jesse admin password.
"""

from typing import Optional

from jesse.mcp.tools.services.credentials import (
    DEFAULT_DATA_PROVIDER,
    delete_data_provider_credentials_service,
    delete_exchange_api_key_service,
    get_data_provider_credentials_service,
    get_exchange_api_keys_service,
    store_data_provider_credentials_service,
    store_exchange_api_key_service,
    validate_data_provider_credentials_service,
)


def register_credentials_tools(mcp):
    """Register the credential management tools with the MCP server."""

    @mcp.tool()
    def get_exchange_api_keys() -> dict:
        """List the exchange API keys stored for Jesse Live.

        Each entry has `id`, `exchange`, `name`, and masked `api_key`/`api_secret` values.
        Use the `id` with delete_exchange_api_key(). Stored secrets are never returned in full.
        """
        return get_exchange_api_keys_service()

    @mcp.tool()
    def store_exchange_api_key(
        exchange: str,
        name: str,
        api_key: str,
        api_secret: str,
        additional_fields: Optional[dict[str, str]] = None,
    ) -> dict:
        """Store an exchange API key so Jesse Live can trade on that exchange.

        - `exchange`: a live-trading exchange name exactly as Jesse lists it,
          e.g. "Binance Perpetual Futures", "Bybit USDT Perpetual", "Hyperliquid Perpetual".
          Historical-only sources such as "Massive Stocks" do not take exchange keys; use
          store_data_provider_credentials() for those.
        - `name`: a unique label the user will recognize, e.g. "Main Binance account".
        - `additional_fields`: only for exchanges that require extra secrets:
          Apex needs `api_passphrase`, `wallet_address`, `stark_private_key`;
          KuCoin needs `api_passphrase`; Lighter needs `api_key_index`.

        Only pass secrets the user has explicitly provided in this conversation. The response
        contains masked values only; never repeat the full key or secret back to the user.
        """
        return store_exchange_api_key_service(
            exchange=exchange,
            name=name,
            api_key=api_key,
            api_secret=api_secret,
            additional_fields=additional_fields,
        )

    @mcp.tool()
    def delete_exchange_api_key(id: str) -> dict:
        """Delete one stored exchange API key by the `id` from get_exchange_api_keys().

        Confirm with the user first: a live session using this key cannot be started again
        until a new key is stored.
        """
        return delete_exchange_api_key_service(id)

    @mcp.tool()
    def get_data_provider_credentials() -> dict:
        """Show which historical data providers have an API key configured.

        Currently the only provider is "Massive", whose single key unlocks Massive Stocks,
        Massive Currencies, Massive Indices, and Massive Futures imports. Returns configuration
        status and timestamps only, never the key itself.
        """
        return get_data_provider_credentials_service()

    @mcp.tool()
    def store_data_provider_credentials(api_key: str, provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
        """Save a data provider API key (default provider: "Massive") for historical imports.

        Jesse keeps one key per provider and refuses to overwrite it: if a key already exists,
        call delete_data_provider_credentials() first. Only pass a key the user explicitly
        provided in this conversation, and never repeat it back. After storing, call
        validate_data_provider_credentials() to confirm the provider accepts it.
        """
        return store_data_provider_credentials_service(api_key=api_key, provider_id=provider_id)

    @mcp.tool()
    def delete_data_provider_credentials(provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
        """Remove a data provider's stored API key (default provider: "Massive").

        Candles already imported from that provider stay in the database; only new imports and
        symbol searches for its sources stop working until a key is stored again.
        """
        return delete_data_provider_credentials_service(provider_id=provider_id)

    @mcp.tool()
    def validate_data_provider_credentials(provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
        """Test the stored data provider key against the provider (default provider: "Massive").

        Use this after store_data_provider_credentials() or when Massive imports fail with an
        authentication or entitlement error. Reports success or the provider's rejection reason
        without returning the key or the provider's raw response.
        """
        return validate_data_provider_credentials_service(provider_id=provider_id)

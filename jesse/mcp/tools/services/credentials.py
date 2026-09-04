"""
Jesse Credential Service Functions

These functions let MCP tools manage the same two credential stores the Dashboard
manages: exchange API keys used by Jesse Live, and data-provider credentials such
as the Massive API key used for historical imports.

Secrets only travel from the caller to Jesse's local HTTP API. Every read path
returns masked values or configuration status; nothing here ever echoes a key back.
"""

from typing import Any, Optional

import requests

import jesse.mcp.mcp_config as mcp_config

from .auth import hash_password


REQUEST_TIMEOUT_SECONDS = 30
# Credential validation performs a live provider round-trip, which can be slow on free plans.
VALIDATION_TIMEOUT_SECONDS = 90
DEFAULT_DATA_PROVIDER = 'Massive'


def _request(method: str, path: str, action: str, json_body: dict | None = None, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict:
    """Call one authenticated Jesse endpoint and normalize its outcome into the MCP envelope."""
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        headers = {'Authorization': hash_password(password)}
        response = requests.request(method, f'{api_url}{path}', headers=headers, json=json_body, timeout=timeout)
    except ValueError as exc:
        return {'status': 'error', 'action': 'config_error', 'message': str(exc)}
    except requests.RequestException as exc:
        return {
            'status': 'error',
            'action': f'{action}_failed',
            'error_type': 'network_error',
            'message': f'Network error while contacting Jesse: {exc}',
        }

    try:
        payload: Any = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {'data': payload}

    if response.status_code == 200:
        return {'status': 'success', 'action': action, **payload}
    return {
        'status': 'error',
        'action': f'{action}_failed',
        'error_type': 'api_error',
        'http_status': response.status_code,
        'message': payload.get('message') or payload.get('error') or response.text,
    }


def get_exchange_api_keys_service() -> dict:
    """List stored exchange API keys with masked secrets."""
    result = _request('GET', '/exchange/api-keys', 'exchange_api_keys_retrieved')
    if result['status'] == 'success':
        keys = result.get('data', [])
        result['api_key_count'] = len(keys)
        result['message'] = f'Found {len(keys)} stored exchange API key(s)'
    return result


def store_exchange_api_key_service(
    exchange: str,
    name: str,
    api_key: str,
    api_secret: str,
    additional_fields: Optional[dict[str, str]] = None,
) -> dict:
    """Store one exchange API key for Jesse Live. The response only contains masked values."""
    if not name.strip():
        return {'status': 'error', 'action': 'exchange_api_key_store_failed', 'message': 'name must not be empty'}
    if not api_key.strip() or not api_secret.strip():
        return {
            'status': 'error',
            'action': 'exchange_api_key_store_failed',
            'message': 'api_key and api_secret must not be empty',
        }
    body = {
        'exchange': exchange,
        'name': name.strip(),
        'api_key': api_key.strip(),
        'api_secret': api_secret.strip(),
        'additional_fields': {key: value.strip() for key, value in (additional_fields or {}).items()},
    }
    return _request('POST', '/exchange/api-keys/store', 'exchange_api_key_stored', body)


def delete_exchange_api_key_service(exchange_api_key_id: str) -> dict:
    """Delete one stored exchange API key by the id returned from get_exchange_api_keys()."""
    if not exchange_api_key_id.strip():
        return {'status': 'error', 'action': 'exchange_api_key_delete_failed', 'message': 'id must not be empty'}
    return _request('POST', '/exchange/api-keys/delete', 'exchange_api_key_deleted', {'id': exchange_api_key_id.strip()})


def get_data_provider_credentials_service() -> dict:
    """Report which data providers have credentials configured, without exposing any value."""
    result = _request('GET', '/data-providers/credentials', 'data_provider_credentials_retrieved')
    if result['status'] == 'success':
        providers = result.get('data', [])
        configured = [provider['provider_id'] for provider in providers if provider.get('configured')]
        result['configured_providers'] = configured
        result['message'] = (
            f'Configured data providers: {", ".join(configured)}' if configured else 'No data provider credentials are configured'
        )
    return result


def store_data_provider_credentials_service(api_key: str, provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
    """Store a data provider API key. Jesse rejects a second key until the existing one is deleted."""
    if not api_key.strip():
        return {'status': 'error', 'action': 'data_provider_credentials_store_failed', 'message': 'api_key must not be empty'}
    body = {'provider_id': provider_id, 'api_key': api_key.strip()}
    return _request('POST', '/data-providers/credentials/store', 'data_provider_credentials_stored', body)


def delete_data_provider_credentials_service(provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
    """Delete a data provider's stored credentials. Imported candles are kept."""
    return _request(
        'POST', '/data-providers/credentials/delete', 'data_provider_credentials_deleted', {'provider_id': provider_id}
    )


def validate_data_provider_credentials_service(provider_id: str = DEFAULT_DATA_PROVIDER) -> dict:
    """Check the stored data provider key against the provider without returning the key."""
    return _request(
        'POST',
        '/data-providers/credentials/validate',
        'data_provider_credentials_validated',
        {'provider_id': provider_id},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )

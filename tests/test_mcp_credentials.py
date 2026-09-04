from types import SimpleNamespace

import pytest

import jesse.mcp.tools.services.candles as candles_service
import jesse.mcp.tools.services.credentials as credentials_service
from jesse.mcp.tools import register_tools


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, uri):
        def decorator(func):
            return func

        return decorator


def _fake_response(status_code, payload):
    import json

    return SimpleNamespace(status_code=status_code, json=lambda: payload, text=json.dumps(payload))


@pytest.fixture
def mcp_backend(monkeypatch):
    calls = []
    queued = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append({'method': method, 'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
        return queued.pop(0)

    def fake_post(url, headers=None, json=None, timeout=None):
        return fake_request('POST', url, headers=headers, json=json, timeout=timeout)

    monkeypatch.setattr(credentials_service.mcp_config, 'JESSE_API_URL', 'http://jesse.test')
    monkeypatch.setattr(credentials_service.mcp_config, 'JESSE_PASSWORD', 'test-password')
    monkeypatch.setattr('requests.request', fake_request)
    monkeypatch.setattr('requests.post', fake_post)
    return calls, queued


def test_credential_and_symbol_tools_are_registered():
    mcp = FakeMCP()

    register_tools(mcp)

    for name in (
        'search_symbols',
        'get_exchange_api_keys',
        'store_exchange_api_key',
        'delete_exchange_api_key',
        'get_data_provider_credentials',
        'store_data_provider_credentials',
        'delete_data_provider_credentials',
        'validate_data_provider_credentials',
    ):
        assert name in mcp.tools, name
    assert 'never repeat the full key' in mcp.tools['store_exchange_api_key'].__doc__
    assert 'Massive Futures lists CME stock futures' in mcp.tools['search_symbols'].__doc__


def test_search_symbols_service_uses_the_shared_search_endpoint(mcp_backend):
    calls, queued = mcp_backend
    queued.append(_fake_response(200, {
        'data': [{'symbol': 'MSFT-USD', 'name': 'Microsoft Corp', 'kind': 'Common Stock', 'venue': 'NASDAQ'}],
        'catalog_size': 13152,
    }))

    result = candles_service.search_symbols_service('Massive Stocks', 'microsoft', limit=5)

    assert calls[0]['url'] == 'http://jesse.test/exchange/search-symbols'
    assert calls[0]['json'] == {'exchange': 'Massive Stocks', 'query': 'microsoft', 'limit': 5}
    assert calls[0]['headers']['Authorization'] != 'test-password'
    assert result['status'] == 'success'
    assert result['match_count'] == 1
    assert result['catalog_size'] == 13152
    assert result['matches'][0]['symbol'] == 'MSFT-USD'

    queued.append(_fake_response(200, {'data': [], 'catalog_size': 47961}))
    miss = candles_service.search_symbols_service('Massive Futures', 'AAPL')
    assert miss['match_count'] == 0
    assert 'No symbol on Massive Futures matches' in miss['message']

    queued.append(_fake_response(401, {'error': 'Massive API credentials are not configured'}))
    failure = candles_service.search_symbols_service('Massive Stocks', 'msft')
    assert failure['status'] == 'error'
    assert failure['http_status'] == 401
    assert 'not configured' in failure['message']


def test_exchange_api_key_services_never_return_secrets(mcp_backend):
    calls, queued = mcp_backend
    masked = {'id': 'key-1', 'exchange': 'Binance Perpetual Futures', 'name': 'Main', 'api_key': 'abcd***...***wxyz', 'api_secret': 'abcd***...***wxyz'}
    queued.append(_fake_response(200, {'status': 'success', 'message': 'stored', 'data': masked}))

    stored = credentials_service.store_exchange_api_key_service(
        'Binance Perpetual Futures', ' Main ', ' abcdKEYwxyz ', 'abcdSECRETwxyz', {'api_passphrase': ' pass '}
    )

    assert calls[0]['method'] == 'POST'
    assert calls[0]['url'] == 'http://jesse.test/exchange/api-keys/store'
    assert calls[0]['json'] == {
        'exchange': 'Binance Perpetual Futures',
        'name': 'Main',
        'api_key': 'abcdKEYwxyz',
        'api_secret': 'abcdSECRETwxyz',
        'additional_fields': {'api_passphrase': 'pass'},
    }
    assert stored['status'] == 'success'
    assert stored['data'] == masked
    assert 'abcdKEYwxyz' not in str(stored)

    queued.append(_fake_response(200, {'data': [masked]}))
    listed = credentials_service.get_exchange_api_keys_service()
    assert calls[1]['method'] == 'GET'
    assert listed['api_key_count'] == 1

    queued.append(_fake_response(200, {'status': 'success', 'message': 'deleted'}))
    deleted = credentials_service.delete_exchange_api_key_service('key-1')
    assert calls[2]['json'] == {'id': 'key-1'}
    assert deleted['status'] == 'success'

    assert credentials_service.store_exchange_api_key_service('Binance Spot', 'x', '', 'secret')['status'] == 'error'
    assert credentials_service.delete_exchange_api_key_service('  ')['status'] == 'error'
    assert len(calls) == 3


def test_data_provider_credential_services_report_status_and_conflicts(mcp_backend):
    calls, queued = mcp_backend
    status = {'provider_id': 'Massive', 'name': 'Massive', 'configured': True, 'created_at': 1, 'updated_at': 1, 'credential_fields': []}
    queued.append(_fake_response(200, {'data': [status]}))

    listed = credentials_service.get_data_provider_credentials_service()

    assert calls[0]['url'] == 'http://jesse.test/data-providers/credentials'
    assert listed['configured_providers'] == ['Massive']

    queued.append(_fake_response(409, {'message': 'Delete the existing data provider credentials before adding new ones'}))
    conflict = credentials_service.store_data_provider_credentials_service(' massive-key ')
    assert calls[1]['json'] == {'provider_id': 'Massive', 'api_key': 'massive-key'}
    assert conflict['status'] == 'error'
    assert conflict['http_status'] == 409
    assert 'Delete the existing' in conflict['message']

    queued.append(_fake_response(200, {'status': 'success', 'message': 'deleted', 'data': {**status, 'configured': False}}))
    assert credentials_service.delete_data_provider_credentials_service()['status'] == 'success'
    assert calls[2]['json'] == {'provider_id': 'Massive'}

    queued.append(_fake_response(200, {'status': 'success', 'message': 'The Massive API key is valid.'}))
    validated = credentials_service.validate_data_provider_credentials_service()
    assert calls[3]['url'] == 'http://jesse.test/data-providers/credentials/validate'
    assert calls[3]['timeout'] == credentials_service.VALIDATION_TIMEOUT_SECONDS
    assert validated['message'] == 'The Massive API key is valid.'

    assert credentials_service.store_data_provider_credentials_service('   ')['status'] == 'error'
    assert len(calls) == 4

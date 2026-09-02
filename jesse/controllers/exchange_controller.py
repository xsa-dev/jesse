import ast
import json

from fastapi import APIRouter, Depends
from redis.exceptions import LockError
from starlette.responses import JSONResponse

from jesse.modes.import_candles_mode.drivers import build_historical_provider_registry
from jesse.services.historical_data.errors import (
    HistoricalDataProviderError,
    ProviderAuthenticationError,
    ProviderEntitlementError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from jesse.services.auth import require_auth
from jesse.services.redis import sync_redis
from jesse.services.web import ExchangeSupportedSymbolsRequestJson, StoreExchangeApiKeyRequestJson, DeleteExchangeApiKeyRequestJson
from jesse.enums import exchanges


router = APIRouter(prefix="/exchange", tags=["Exchange"], dependencies=[Depends(require_auth)])

# The free-tier catalog may cross several one-minute quota windows; the lock expires after ten.
SYMBOL_CATALOG_LOCK_TTL_SECONDS = 600
# Waiting one quota window lets concurrent callers reuse the completed cache without hanging indefinitely.
SYMBOL_CATALOG_LOCK_WAIT_SECONDS = 60
# Versioned once for every historical source because all symbol discovery follows the same contract.
HISTORICAL_SYMBOL_CATALOG_CACHE_VERSION = 1


def _deserialize_symbol_catalog(value: str | bytes) -> list[str]:
    """Read JSON catalogs while safely accepting legacy Python-list cache entries."""
    decoded_value = value.decode() if isinstance(value, bytes) else value
    try:
        symbols = json.loads(decoded_value)
    except json.JSONDecodeError:
        symbols = ast.literal_eval(decoded_value)
    if not isinstance(symbols, list) or any(not isinstance(symbol, str) for symbol in symbols):
        raise ValueError('Cached exchange symbol catalog is invalid')
    return symbols


@router.post('/supported-symbols')
def exchange_supported_symbols(request_json: ExchangeSupportedSymbolsRequestJson) -> JSONResponse:
    
    # if is_dev_env():
    #     return JSONResponse({
    #         'data': [    
    #             'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'DOGE-USDT'
    #         ]
    #     }, status_code=200)

    return get_exchange_supported_symbols(request_json.exchange)


@router.get('/api-keys')
def get_exchange_api_keys_endpoint() -> JSONResponse:

    from jesse.modes.exchange_api_keys import get_exchange_api_keys
    return get_exchange_api_keys()


@router.post('/api-keys/store')
def store_exchange_api_keys_endpoint(json_request: StoreExchangeApiKeyRequestJson) -> JSONResponse:

    from jesse.modes.exchange_api_keys import store_exchange_api_keys
    return store_exchange_api_keys(
        json_request.exchange, json_request.name, json_request.api_key, json_request.api_secret,
        json_request.additional_fields, json_request.general_notifications_id, json_request.error_notifications_id
    )


@router.post('/api-keys/delete')
def delete_exchange_api_keys_endpoint(json_request: DeleteExchangeApiKeyRequestJson) -> JSONResponse:

    from jesse.modes.exchange_api_keys import delete_exchange_api_keys
    return delete_exchange_api_keys(json_request.id)


def get_exchange_supported_symbols(exchange: str) -> JSONResponse:
    if exchange == exchanges.CUSTOM_DATA:
        from jesse.repositories.candle_repository import get_stored_symbols
        return JSONResponse({'data': get_stored_symbols(exchange)}, status_code=200)

    cache_key = f'historical-symbols:v{HISTORICAL_SYMBOL_CATALOG_CACHE_VERSION}:{exchange}'
    cached_result = sync_redis.get(cache_key)
    if cached_result is not None:
        return JSONResponse({
            'data': _deserialize_symbol_catalog(cached_result)
        }, status_code=200)

    try:
        # One shared loader protects remote source quotas and avoids duplicate legacy exchange calls.
        with sync_redis.lock(
            f'{cache_key}:load-lock',
            timeout=SYMBOL_CATALOG_LOCK_TTL_SECONDS,
            blocking_timeout=SYMBOL_CATALOG_LOCK_WAIT_SECONDS,
        ):
            cached_result = sync_redis.get(cache_key)
            if cached_result is not None:
                return JSONResponse({'data': _deserialize_symbol_catalog(cached_result)}, status_code=200)
            provider = build_historical_provider_registry((exchange,)).get(exchange)
            symbols = list(provider.list_symbols())
            sync_redis.setex(cache_key, 300, json.dumps(symbols))
    except LockError:
        return JSONResponse({'error': 'The symbol catalog is still loading'}, status_code=503)
    except ProviderAuthenticationError as exc:
        return JSONResponse({'error': str(exc)}, status_code=401)
    except ProviderEntitlementError as exc:
        return JSONResponse({'error': str(exc)}, status_code=403)
    except (ProviderRateLimitError, ProviderQuotaError) as exc:
        return JSONResponse({'error': str(exc)}, status_code=429)
    except ProviderUnavailableError as exc:
        return JSONResponse({'error': str(exc)}, status_code=503)
    except ProviderRequestError as exc:
        return JSONResponse({'error': str(exc)}, status_code=502)
    except HistoricalDataProviderError as exc:
        return JSONResponse({'error': str(exc)}, status_code=502)
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)
    return JSONResponse({'data': symbols}, status_code=200)

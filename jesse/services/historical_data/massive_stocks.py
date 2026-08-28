import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

import requests

from jesse.enums import data_providers, exchanges
from jesse.repositories import data_provider_credentials_repository

from .contracts import (
    AdjustmentMode,
    HistoricalCandle,
    HistoricalCandleBatch,
    HistoricalCandleProvider,
    HistoricalCandleRequest,
    ProviderCapabilities,
)
from .errors import (
    HistoricalCandleValidationError,
    HistoricalDataRequestError,
    ProviderAuthenticationError,
    ProviderEntitlementError,
    ProviderPaginationError,
    ProviderQuotaError,
    ProviderRangeError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderSchemaError,
    ProviderSymbolNotFoundError,
    ProviderUnavailableError,
)


MASSIVE_API_HOST = 'api.massive.com'
MASSIVE_AGGREGATES_URL = f'https://{MASSIVE_API_HOST}/v2/aggs/ticker'
# Massive documents 50,000 as the custom-aggregate endpoint's maximum base-bar page size.
MASSIVE_PAGE_LIMIT = 50_000
# Three attempts cover short network/server failures without making a synchronous import stall indefinitely.
MASSIVE_REQUEST_ATTEMPTS = 3
# Thirty seconds accommodates large aggregate pages while bounding an unavailable provider call.
MASSIVE_REQUEST_TIMEOUT_SECONDS = 30
# Massive's advertised entry-level quota refills each minute, so longer server delays are not useful here.
MASSIVE_MAX_RETRY_DELAY_SECONDS = 60.0
# At 50,000 base bars per page, 1,000 pages exceed the provider's complete stock-minute history.
MASSIVE_MAX_PAGES = 1_000


def _load_massive_api_key() -> str:
    try:
        credentials = data_provider_credentials_repository.get_data_provider_credentials(data_providers.MASSIVE)
    except ValueError as exc:
        raise ProviderAuthenticationError('Stored Massive API credentials are invalid') from exc
    api_key = credentials.get('api_key') if credentials is not None else None
    if not api_key:
        raise ProviderAuthenticationError('Massive API credentials are not configured')
    return api_key


class MassiveStocksProvider(HistoricalCandleProvider):
    """Fetch Massive stock aggregates without exposing it as a live execution exchange."""

    provider_id = exchanges.MASSIVE_STOCKS
    capabilities = ProviderCapabilities(
        credential_validation=True,
        native_timeframes=('1m',),
        adjustment_modes=(AdjustmentMode.SPLIT_ADJUSTED,),
    )

    def __init__(
        self,
        credential_loader: Callable[[], str] | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._credential_loader = credential_loader or _load_massive_api_key
        self._session = session or requests.Session()
        self._sleep = sleep

    def _fetch_candles(self, request: HistoricalCandleRequest) -> HistoricalCandleBatch:
        if request.adjustment_mode is not AdjustmentMode.SPLIT_ADJUSTED:
            # The single-table phase permits one canonical revision and intentionally chooses split-adjusted bars.
            raise HistoricalDataRequestError('Massive Stocks currently requires split-adjusted candles')
        provider_symbol = _massive_symbol(request.symbol)
        api_key = self._credential_loader()
        url = _aggregate_url(provider_symbol, request)
        params: dict[str, str | int] | None = {
            'adjusted': 'true',
            'sort': 'asc',
            'limit': MASSIVE_PAGE_LIMIT,
        }
        candles_by_timestamp: dict[int, HistoricalCandle] = {}
        visited_urls: set[str] = set()

        for _ in range(MASSIVE_MAX_PAGES):
            if url in visited_urls:
                raise ProviderPaginationError('Massive returned a repeated pagination URL')
            visited_urls.add(url)

            payload = self._request_json(url, api_key, params)
            page_candles = _normalize_page(payload, request, provider_symbol)
            for candle in page_candles:
                previous = candles_by_timestamp.get(candle.timestamp)
                if previous is not None and previous != candle:
                    raise ProviderPaginationError(
                        f'Massive returned conflicting candles for timestamp {candle.timestamp}'
                    )
                candles_by_timestamp[candle.timestamp] = candle

            next_url = payload.get('next_url')
            if next_url is None:
                candles = tuple(candles_by_timestamp[timestamp] for timestamp in sorted(candles_by_timestamp))
                return HistoricalCandleBatch(request, candles)
            url = _validated_next_url(next_url)
            params = None

        raise ProviderPaginationError('Massive pagination exceeded the safety limit')

    def _request_json(
        self,
        url: str,
        api_key: str,
        params: dict[str, str | int] | None,
    ) -> Mapping[str, Any]:
        for attempt in range(MASSIVE_REQUEST_ATTEMPTS):
            try:
                response = self._session.get(
                    url,
                    headers={'Authorization': f'Bearer {api_key}'},
                    params=params,
                    timeout=MASSIVE_REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt + 1 == MASSIVE_REQUEST_ATTEMPTS:
                    raise ProviderUnavailableError('Massive is currently unavailable') from exc
                self._sleep(2 ** attempt)
                continue

            try:
                payload = _response_payload(response)
                error = _response_error(response.status_code, payload)
                if error is None:
                    return payload
                if isinstance(error, ProviderQuotaError):
                    raise error
                if isinstance(error, ProviderRateLimitError) or response.status_code >= 500:
                    if attempt + 1 < MASSIVE_REQUEST_ATTEMPTS:
                        self._sleep(_retry_delay(response.headers.get('Retry-After'), attempt))
                        continue
                raise error
            finally:
                response.close()

        raise ProviderUnavailableError('Massive is currently unavailable')


def _massive_symbol(symbol: str) -> str:
    normalized_symbol = symbol.strip()
    if normalized_symbol.endswith('-USD'):
        normalized_symbol = normalized_symbol[:-4]
    elif '-' in normalized_symbol:
        raise HistoricalDataRequestError('Massive stock symbols must be direct tickers or use the -USD quote')
    if not normalized_symbol:
        raise HistoricalDataRequestError('Massive stock ticker must not be empty')
    return normalized_symbol


def _aggregate_url(provider_symbol: str, request: HistoricalCandleRequest) -> str:
    # Massive's `to` boundary is inclusive; subtracting one millisecond preserves Jesse's half-open range.
    start = request.requested_range.start_timestamp
    end = request.requested_range.end_timestamp - 1
    return f'{MASSIVE_AGGREGATES_URL}/{quote(provider_symbol, safe="")}/range/1/minute/{start}/{end}'


def _validated_next_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderPaginationError('Massive returned an invalid pagination URL')
    parsed = urlparse(value)
    if (
        parsed.scheme != 'https'
        or parsed.netloc != MASSIVE_API_HOST
        or not parsed.path.startswith('/')
        or parsed.fragment
    ):
        raise ProviderPaginationError('Massive returned an untrusted pagination URL')
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if 'apikey' in query_keys or 'api_key' in query_keys:
        raise ProviderPaginationError('Massive returned credentials in a pagination URL')
    return value


def _response_payload(response: requests.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        if 200 <= response.status_code < 300:
            raise ProviderSchemaError('Massive returned invalid JSON') from exc
        return {}
    if not isinstance(payload, Mapping):
        if 200 <= response.status_code < 300:
            raise ProviderSchemaError('Massive returned a non-object response')
        return {}
    return payload


def _response_error(status_code: int, payload: Mapping[str, Any]) -> Exception | None:
    if 200 <= status_code < 300:
        return None
    message = _provider_error_message(payload)
    if status_code == 401:
        return ProviderAuthenticationError('Massive rejected the API key')
    if status_code == 403:
        return ProviderEntitlementError('The API key cannot access the requested Massive stock data')
    if status_code == 404:
        return ProviderSymbolNotFoundError('Massive could not find the requested stock ticker')
    if status_code == 429:
        if 'quota' in message:
            return ProviderQuotaError('Massive request quota is exhausted')
        return ProviderRateLimitError('Massive rate limit reached')
    if status_code == 400:
        if 'date' in message or 'range' in message or 'timestamp' in message:
            return ProviderRangeError('Massive rejected the requested candle range')
        return ProviderRequestError('Massive rejected the candle request')
    if status_code >= 500:
        return ProviderUnavailableError('Massive is currently unavailable')
    if 300 <= status_code < 400:
        return ProviderPaginationError('Massive attempted an unexpected redirect')
    return ProviderRequestError('Massive could not complete the candle request')


def _provider_error_message(payload: Mapping[str, Any]) -> str:
    for key in ('error', 'message'):
        value = payload.get(key)
        if isinstance(value, str):
            return value.lower()
    return ''


def _normalize_page(
    payload: Mapping[str, Any],
    request: HistoricalCandleRequest,
    provider_symbol: str,
) -> tuple[HistoricalCandle, ...]:
    status = payload.get('status')
    if status is not None and status != 'OK':
        raise ProviderSchemaError('Massive returned an unsuccessful payload with a successful HTTP status')
    ticker = payload.get('ticker')
    if ticker is not None and ticker != provider_symbol:
        raise ProviderSchemaError('Massive returned candles for an unexpected ticker')
    adjusted = payload.get('adjusted')
    if adjusted is not None and adjusted is not True:
        raise ProviderSchemaError('Massive returned candles with an unexpected adjustment mode')

    results = payload.get('results', [])
    if not isinstance(results, list):
        raise ProviderSchemaError('Massive candle results must be an array')
    try:
        results_count = payload.get('resultsCount')
        if results_count is not None and _strict_int(results_count, 'resultsCount') != len(results):
            raise ProviderSchemaError('Massive resultsCount does not match the returned candles')
        candles_list = []
        for row in results:
            if not isinstance(row, Mapping):
                raise ProviderSchemaError('Massive candle entries must be objects')
            timestamp = _strict_int(row['t'], 'timestamp')
            if not request.requested_range.start_timestamp <= timestamp < request.requested_range.end_timestamp:
                continue
            candles_list.append(
                HistoricalCandle(
                    timestamp=timestamp,
                    open=float(row['o']),
                    high=float(row['h']),
                    low=float(row['l']),
                    close=float(row['c']),
                    volume=float(row['v']),
                    vwap=float(row['vw']) if row.get('vw') is not None else None,
                    transaction_count=(
                        _strict_int(row['n'], 'transaction_count') if row.get('n') is not None else None
                    ),
                )
            )
        candles = tuple(candles_list)
    except ProviderSchemaError:
        raise
    except (HistoricalCandleValidationError, KeyError, TypeError, ValueError) as exc:
        raise ProviderSchemaError('Massive returned an invalid candle payload') from exc

    if any(current.timestamp <= previous.timestamp for previous, current in zip(candles, candles[1:])):
        raise ProviderSchemaError('Massive candle timestamps must be ascending and unique within a page')
    return candles


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderSchemaError(f'Massive {field} must be an integer')
    return value


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = 2 ** attempt
        if not isfinite(delay):
            delay = 2 ** attempt
        return min(max(delay, 0.0), MASSIVE_MAX_RETRY_DELAY_SECONDS)
    return float(2 ** attempt)

from abc import ABC, abstractmethod
import requests
from jesse import exceptions
from jesse.helpers import timeframe_to_one_minutes
from jesse.services.historical_data import (
    HistoricalCandle,
    HistoricalCandleBatch,
    HistoricalCandleProvider,
    HistoricalCandleRequest,
    ProviderCapabilities,
)
from jesse.services.historical_data.errors import (
    HistoricalDataError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderSchemaError,
    ProviderSymbolNotFoundError,
    ProviderUnavailableError,
)


class CandleExchange(HistoricalCandleProvider, ABC):
    def __init__(self, name: str, count: int, rate_limit_per_second: float, backup_exchange_class):
        self.name = name
        self.provider_id = name
        # Crypto persistence imports native 1m bars and derives larger timeframes later.
        self.capabilities = ProviderCapabilities(native_timeframes=('1m',))
        self.count = count
        self.sleep_time = 1 / rate_limit_per_second
        self._backup_exchange_class = backup_exchange_class
        self._backup_exchange = None

    @property
    def backup_exchange(self):
        if self._backup_exchange_class is None:
            return None

        if self._backup_exchange is None:
            self._backup_exchange = self._backup_exchange_class()

        return self._backup_exchange

    @abstractmethod
    def fetch(self, symbol: str, start_timestamp: int, timeframe: str) -> list:
        pass

    def _fetch_candles(self, request: HistoricalCandleRequest) -> HistoricalCandleBatch:
        """Adapt one legacy provider page to the shared immutable candle contract."""
        interval = timeframe_to_one_minutes(request.timeframe) * 60_000
        # Bound the page geometrically because sparse markets can return short pages and
        # some legacy drivers overfetch beyond their declared page size.
        page_end = min(
            request.requested_range.end_timestamp,
            request.requested_range.start_timestamp + self.count * interval,
        )

        try:
            rows = self.fetch(
                request.symbol,
                request.requested_range.start_timestamp,
                request.timeframe,
            )
        except HistoricalDataError:
            raise
        except (exceptions.SymbolNotFound, exceptions.InvalidSymbol) as exc:
            raise ProviderSymbolNotFoundError(str(exc)) from exc
        except exceptions.ExchangeInMaintenance as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        except (requests.exceptions.ConnectionError, ConnectionError) as exc:
            # Legacy HTTP handling discards status metadata, but retains 429/rate-limit text.
            message = str(exc)
            if '429' in message or 'rate limit' in message.lower():
                raise ProviderRateLimitError(message) from exc
            raise ProviderUnavailableError(message) from exc
        except requests.exceptions.JSONDecodeError as exc:
            raise ProviderSchemaError(str(exc)) from exc
        except ValueError as exc:
            # Legacy 404 handling retains this phrase after discarding the response status.
            if 'check the symbol' in str(exc).lower():
                raise ProviderSymbolNotFoundError(str(exc)) from exc
            raise ProviderRequestError(str(exc)) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderSchemaError(str(exc)) from exc

        try:
            candles = tuple(
                HistoricalCandle(
                    timestamp=int(row['timestamp']),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=float(row['volume']),
                )
                for row in rows
                if request.requested_range.start_timestamp <= int(row['timestamp']) < page_end
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderSchemaError(f'Invalid candle payload from {self.provider_id}: {exc}') from exc

        continuation_token = str(page_end) if page_end < request.requested_range.end_timestamp else None
        return HistoricalCandleBatch(request, candles, continuation_token)

    @abstractmethod
    def get_starting_time(self, symbol: str) -> int:
        pass

    @abstractmethod
    def get_available_symbols(self) -> list:
        pass

    @staticmethod
    def validate_response(response: requests.Response) -> None:
        if response.status_code == 502:
            raise exceptions.ExchangeInMaintenance('ERROR: 502 Bad Gateway. Please try again later')
        elif response.status_code // 100 == 5:
            raise ConnectionError('ERROR: {} {}'.format(response.status_code, response.reason))

        # unsupported inputs
        if response.status_code == 400:
            raise ValueError(response.content)

        # unsupported inputs
        if response.status_code == 404:
            raise ValueError(f'ERROR {response.status_code} {response.reason}. Check the symbol')

        # if the response code is not in the 200-299, raise an exception
        if response.status_code // 100 != 2:
            raise ConnectionError(f'ERROR {response.status_code} {response.reason}')

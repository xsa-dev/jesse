from jesse.models.Candle import Candle
import jesse.helpers as jh
from collections.abc import Sequence
from typing import List, TYPE_CHECKING
import numpy as np
import arrow
import peewee

if TYPE_CHECKING:
    from jesse.services.historical_data.contracts import HistoricalCandle


# Nine values are bound per row; 5,000 remains below PostgreSQL's 65,535 bind-parameter limit.
OBSERVED_CANDLE_INSERT_BATCH_SIZE = 5_000


def delete_candles_from_db(exchange: str, symbol: str) -> None:
    """
    Deletes all candles for the given exchange and symbol
    """
    Candle.delete().where(
        Candle.exchange == exchange,
        Candle.symbol == symbol
    ).execute()


def purge_candles_by_exchanges(exchanges: list) -> int:
    """
    Deletes all candles for the given list of exchanges. Returns the number of deleted rows.
    """
    count = Candle.delete().where(Candle.exchange.in_(exchanges)).execute()
    return count


def get_existing_candles() -> List[dict]:
    """
    Returns a list of all existing candles grouped by exchange and symbol
    """
    results = []
    
    # Get unique exchange-symbol combinations
    pairs = Candle.select(
        Candle.exchange, 
        Candle.symbol
    ).distinct().tuples()

    for exchange, symbol in pairs:
        # Get first and last candle for this pair
        first = Candle.select(
            Candle.timestamp
        ).where(
            Candle.exchange == exchange,
            Candle.symbol == symbol
        ).order_by(
            Candle.timestamp.asc()
        ).first()

        last = Candle.select(
            Candle.timestamp
        ).where(
            Candle.exchange == exchange,
            Candle.symbol == symbol
        ).order_by(
            Candle.timestamp.desc()
        ).first()

        if first and last:
            results.append({
                'exchange': exchange,
                'symbol': symbol,
                'start_date': arrow.get(first.timestamp / 1000).format('YYYY-MM-DD'),
                'end_date': arrow.get(last.timestamp / 1000).format('YYYY-MM-DD')
            })

    return results


def fetch_candles_from_db(exchange: str, symbol: str, timeframe: str, start_date: int, finish_date: int) -> tuple:
    res = tuple(
        Candle.select(
            Candle.timestamp, Candle.open, Candle.close, Candle.high, Candle.low,
            Candle.volume
        ).where(
            Candle.exchange == exchange,
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.timestamp.between(start_date, finish_date)
        ).order_by(Candle.timestamp.asc()).tuples()
    )

    return res


def get_candle_timestamp_bounds(exchange: str, symbol: str, timeframe: str) -> tuple[int | None, int | None]:
    """Return the first and latest stored timestamps for one canonical candle series."""
    timeframe_condition = Candle.timeframe == timeframe
    if timeframe == '1m':
        # Older imports may have stored one-minute rows before the timeframe column was populated.
        timeframe_condition = timeframe_condition | Candle.timeframe.is_null()
    first_timestamp, last_timestamp = (
        Candle.select(
            peewee.fn.MIN(Candle.timestamp),
            peewee.fn.MAX(Candle.timestamp),
        )
        .where(
            Candle.exchange == exchange,
            Candle.symbol == symbol,
            timeframe_condition,
        )
        .tuples()
        .get()
    )
    return (
        int(first_timestamp) if first_timestamp is not None else None,
        int(last_timestamp) if last_timestamp is not None else None,
    )


def store_observed_candles(
    exchange: str,
    symbol: str,
    timeframe: str,
    candles: Sequence['HistoricalCandle'],
) -> None:
    """Persist one provider page atomically while retaining existing canonical rows."""
    # SQL batching respects PostgreSQL's bind limit; the outer transaction preserves resumable boundaries.
    with Candle._meta.database.atomic():
        for offset in range(0, len(candles), OBSERVED_CANDLE_INSERT_BATCH_SIZE):
            rows = [
                {
                    'id': jh.generate_unique_id(),
                    'exchange': exchange,
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'timestamp': candle.timestamp,
                    'open': candle.open,
                    'close': candle.close,
                    'high': candle.high,
                    'low': candle.low,
                    'volume': candle.volume,
                }
                for candle in candles[offset:offset + OBSERVED_CANDLE_INSERT_BATCH_SIZE]
            ]
            Candle.insert_many(rows).on_conflict_ignore().execute()


def store_candles_into_db(exchange: str, symbol: str, timeframe: str, candles: np.ndarray, on_conflict='ignore') -> None:
    # make sure the number of candles is more than 0
    if len(candles) == 0:
        raise Exception(f'No candles to store for {exchange}-{symbol}-{timeframe}')

    # convert candles to list of dicts
    candles_list = []
    for candle in candles:
        d = {
            'id': jh.generate_unique_id(),
            'symbol': symbol,
            'exchange': exchange,
            'timestamp': candle[0],
            'open': candle[1],
            'high': candle[3],
            'low': candle[4],
            'close': candle[2],
            'volume': candle[5],
            'timeframe': timeframe,
        }
        candles_list.append(d)

    if on_conflict == 'ignore':
        Candle.insert_many(candles_list).on_conflict_ignore().execute()
    elif on_conflict == 'replace':
        Candle.insert_many(candles_list).on_conflict(
            conflict_target=['exchange', 'symbol', 'timeframe', 'timestamp'],
            preserve=(Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume),
        ).execute()
    elif on_conflict == 'error':
        Candle.insert_many(candles_list).execute()
    else:
        raise Exception(f'Unknown on_conflict value: {on_conflict}')


def store_candle_into_db(exchange: str, symbol: str, timeframe: str, candle: np.ndarray, on_conflict='ignore') -> None:
    d = {
        'id': jh.generate_unique_id(),
        'exchange': exchange,
        'symbol': symbol,
        'timeframe': timeframe,
        'timestamp': candle[0],
        'open': candle[1],
        'high': candle[3],
        'low': candle[4],
        'close': candle[2],
        'volume': candle[5]
    }

    if on_conflict == 'ignore':
        Candle.insert(**d).on_conflict_ignore().execute()
    elif on_conflict == 'replace':
        Candle.insert(**d).on_conflict(
            conflict_target=['exchange', 'symbol', 'timeframe', 'timestamp'],
            preserve=(Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume),
        ).execute()
    elif on_conflict == 'error':
        Candle.insert(**d).execute()
    else:
        raise Exception(f'Unknown on_conflict value: {on_conflict}')

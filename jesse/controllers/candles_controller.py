import csv
import io
import re
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse
from jesse.repositories import candle_repository
from jesse.services.auth import require_auth, require_auth_form
from jesse.services.multiprocessing import process_manager
from jesse.services.web import ImportCandlesRequestJson, CancelRequestJson, GetCandlesRequestJson, DeleteCandlesRequestJson, PurgeCandlesRequestJson
from jesse.services.redis import is_process_active
from jesse.services.custom_candle_import import (
    CustomCandleImportError,
    import_custom_candle_csv,
    normalize_custom_symbol,
    scan_custom_candle_csv,
)
import jesse.helpers as jh

router = APIRouter(prefix="/candles", tags=["Candles"], dependencies=[Depends(require_auth)])
# Native form downloads cannot set an Authorization header, so this isolated route verifies
# the same token from the POST body without exposing it in browser history or server URLs.
export_router = APIRouter(
    prefix="/candles",
    tags=["Candles"],
    dependencies=[Depends(require_auth_form)],
)


def _safe_export_filename(exchange: str, symbol: str) -> str:
    source = re.sub(r'[^a-z0-9]+', '-', exchange.lower()).strip('-') or 'candles'
    instrument = re.sub(r'[^a-z0-9._-]+', '-', symbol.lower()).strip('-') or 'symbol'
    return f'{source}_{instrument}_1m.csv'


def _stream_candle_csv(exchange: str, symbol: str) -> Iterator[str]:
    """Encode bounded database batches into chunks compatible with custom-data import."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(('timestamp', 'open', 'close', 'high', 'low', 'volume'))
    yield output.getvalue()

    for rows in candle_repository.stream_one_minute_candles(exchange, symbol):
        output.seek(0)
        output.truncate(0)
        writer.writerows(rows)
        yield output.getvalue()


@export_router.post('/export')
def export_candles(
    exchange: Annotated[str, Form()],
    symbol: Annotated[str, Form()],
) -> StreamingResponse:
    """Stream one canonical exchange/symbol series as a round-trippable CSV download."""
    filename = _safe_export_filename(exchange, symbol)
    return StreamingResponse(
        _stream_candle_csv(exchange, symbol),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.post('/custom/preview')
def preview_custom_candles(
    file: Annotated[UploadFile, File()],
    symbol: Annotated[str, Form()],
    adjustment_mode: Annotated[str, Form()],
    timestamp_format: Annotated[str, Form()] = 'auto',
    timestamp_column: Annotated[str, Form()] = 'timestamp',
    open_column: Annotated[str, Form()] = 'open',
    high_column: Annotated[str, Form()] = 'high',
    low_column: Annotated[str, Form()] = 'low',
    close_column: Annotated[str, Form()] = 'close',
    volume_column: Annotated[str, Form()] = 'volume',
) -> JSONResponse:
    """Validate an uploaded candle CSV without changing persisted data."""
    try:
        if adjustment_mode not in {'adjusted', 'unadjusted'}:
            raise CustomCandleImportError(f'Unsupported adjustment mode: {adjustment_mode}')
        column_mapping = {
            'timestamp': timestamp_column,
            'open': open_column,
            'high': high_column,
            'low': low_column,
            'close': close_column,
            'volume': volume_column,
        }
        report = scan_custom_candle_csv(file.file, timestamp_format, column_mapping)
        return JSONResponse({
            'data': {
                **report,
                'symbol': normalize_custom_symbol(symbol),
                'timeframe': '1m',
                'adjustment_mode': adjustment_mode,
            }
        }, status_code=200)
    except CustomCandleImportError as exc:
        return JSONResponse({'error': str(exc)}, status_code=422)


@router.post('/custom/import')
def import_custom_candles(
    file: Annotated[UploadFile, File()],
    symbol: Annotated[str, Form()],
    adjustment_mode: Annotated[str, Form()],
    timestamp_format: Annotated[str, Form()] = 'auto',
    timestamp_column: Annotated[str, Form()] = 'timestamp',
    open_column: Annotated[str, Form()] = 'open',
    high_column: Annotated[str, Form()] = 'high',
    low_column: Annotated[str, Form()] = 'low',
    close_column: Annotated[str, Form()] = 'close',
    volume_column: Annotated[str, Form()] = 'volume',
) -> JSONResponse:
    """Validate and atomically persist an uploaded observed-candle CSV."""
    try:
        report = import_custom_candle_csv(
            file.file,
            symbol,
            timestamp_format,
            adjustment_mode,
            {
                'timestamp': timestamp_column,
                'open': open_column,
                'high': high_column,
                'low': low_column,
                'close': close_column,
                'volume': volume_column,
            },
        )
        return JSONResponse({'data': report}, status_code=201)
    except CustomCandleImportError as exc:
        return JSONResponse({'error': str(exc)}, status_code=422)


@router.post("/import")
def import_candles(request_json: ImportCandlesRequestJson) -> JSONResponse:
    """
    Import candles for a specific exchange and symbol
    """
    from jesse.modes import import_candles_mode

    try:
        jh.validate_cwd()
        import_candles_mode.validate_import_request(
            request_json.exchange,
            request_json.symbol,
            request_json.start_date,
        )
        import_candles_mode.store_import_outcome(request_json.id, 'running')
        process_manager.add_task(
            import_candles_mode.run,
            request_json.id,
            request_json.exchange,
            request_json.symbol,
            request_json.start_date,
        )
    except ValueError as e:
        error = f'{type(e).__name__}: {e}'
        import_candles_mode.store_import_outcome(request_json.id, 'failed', error)
        return JSONResponse({'error': error}, status_code=422)
    except Exception as e:
        import traceback

        error = f'{type(e).__name__}: {e}'
        import_candles_mode.store_import_outcome(
            request_json.id,
            'failed',
            error,
            traceback.format_exc(),
        )
        return JSONResponse({'error': error}, status_code=500)

    return JSONResponse({'message': 'Started importing candles...'}, status_code=202)


@router.post("/cancel-import")
def cancel_import_candles(request_json: CancelRequestJson):
    """
    Cancel an import candles process
    """

    process_manager.cancel_process(request_json.id)
    from jesse.modes.import_candles_mode import store_import_outcome
    store_import_outcome(request_json.id, 'cancelled')

    return JSONResponse({'message': f'Candles process with ID of {request_json.id} was requested for termination'},
                        status_code=202)


@router.post("/clear-cache")
def clear_candles_database_cache():
    """
    Clear the candles database cache
    """

    from jesse.services.cache import cache
    cache.flush()

    return JSONResponse({
        'status': 'success',
        'message': 'Candles database cache cleared successfully',
    }, status_code=200)


@router.post("/get")
def get_candles(json_request: GetCandlesRequestJson) -> JSONResponse:
    """
    Get candles for a specific exchange, symbol, and timeframe
    """

    jh.validate_cwd()

    from jesse.modes.data_provider import get_candles as gc

    arr = gc(json_request.exchange, json_request.symbol, json_request.timeframe)

    return JSONResponse({
        'id': json_request.id,
        'data': arr
    }, status_code=200)


@router.post("/import-status")
def get_candle_import_status(request_json: CancelRequestJson) -> JSONResponse:
    """
    Return active progress or the persisted terminal outcome for an import.

    The endpoint uses Redis only and never queries the candle database.
    """

    from jesse.modes.import_candles_mode import (
        candle_import_progress_key,
        get_import_outcome,
    )

    outcome = get_import_outcome(request_json.id)
    terminal_status = outcome.get('status')
    running = bool(is_process_active(request_json.id)) and terminal_status in {None, 'running'}
    response = {
        'import_id': request_json.id,
        'status': 'running' if running else terminal_status or 'finished',
    }
    if not running:
        if outcome.get('error') is not None:
            response['error'] = outcome['error']
        if outcome.get('traceback') is not None:
            response['traceback'] = outcome['traceback']

    # Attach live progress (percent complete, ETA, date reached so far) when the import
    # is still running so callers can see real movement instead of a blind "running".
    # When finished, clean up any lingering progress key.
    import json
    from jesse.services.redis import sync_redis
    progress_key = candle_import_progress_key(request_json.id)
    try:
        if running:
            raw = sync_redis.get(progress_key)
            if raw:
                response['progress'] = json.loads(raw)
        else:
            sync_redis.delete(progress_key)
    except Exception:
        pass

    return JSONResponse(response, status_code=200)


@router.post("/existing")
def get_existing_candles() -> JSONResponse:
    """
    Get all existing candles in the database
    """
    
    try:
        data = candle_repository.get_existing_candles()
        return JSONResponse({'data': data}, status_code=200)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/delete")
def delete_candles(json_request: DeleteCandlesRequestJson) -> JSONResponse:
    """
    Delete candles for a specific exchange and symbol
    """

    try:
        candle_repository.delete_candles_from_db(json_request.exchange, json_request.symbol)
        return JSONResponse({'message': 'Candles deleted successfully'}, status_code=200)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/purge")
def purge_candles(json_request: PurgeCandlesRequestJson) -> JSONResponse:
    """
    Delete all candles for the given list of exchanges
    """

    try:
        deleted_count = candle_repository.purge_candles_by_exchanges(json_request.exchanges)
        return JSONResponse({'message': f'Purged candles for {len(json_request.exchanges)} exchange(s)', 'deleted_count': deleted_count}, status_code=200)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)

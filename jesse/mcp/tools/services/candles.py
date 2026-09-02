"""
Jesse Candles Service Functions

This module contains the core candle service functions used by Jesse's
MCP tools. These functions handle candle data import, management, and retrieval.

The functions are separated from the MCP tool wrappers to allow for better
code organization and reusability.
"""

import requests
import uuid
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile
from typing import Literal, Optional
from .auth import hash_password
import jesse.mcp.mcp_config as mcp_config
from jesse.services.custom_candle_import import CustomCandleImportError, clean_custom_candle_csv


# Multi-year CSV validation can outlast ordinary MCP reads, but remains bounded for a stalled backend.
CUSTOM_CSV_REQUEST_TIMEOUT_SECONDS = 300


def _custom_csv_path(file_path: str) -> Path:
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise ValueError('file_path must be an absolute path')
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.csv':
        raise ValueError('file_path must point to a CSV file')
    return path


def _custom_csv_form(symbol: str) -> dict[str, str]:
    return {
        'symbol': symbol,
        'timestamp_format': 'unix_ms',
        'timestamp_column': 'timestamp',
        'open_column': 'open',
        'close_column': 'close',
        'high_column': 'high',
        'low_column': 'low',
        'volume_column': 'volume',
    }


def _clean_custom_csv(
    file_path: str,
    timestamp_format: str,
    timestamp_column: str,
    open_column: str,
    close_column: str,
    high_column: str,
    low_column: str,
    volume_column: str,
    invalid_row_policy: Literal['reject', 'drop'],
):
    path = _custom_csv_path(file_path)
    cleaned_file = TemporaryFile(mode='w+b')
    try:
        with path.open('rb') as source_file:
            report = clean_custom_candle_csv(
                source_file,
                cleaned_file,
                timestamp_format,
                {
                    'timestamp': timestamp_column,
                    'open': open_column,
                    'close': close_column,
                    'high': high_column,
                    'low': low_column,
                    'volume': volume_column,
                },
                invalid_row_policy,
            )
        cleaned_file.seek(0)
        return cleaned_file, report
    except Exception:
        cleaned_file.close()
        raise


def preview_custom_candle_csv_service(
    file_path: str,
    symbol: str,
    timestamp_format: str = 'auto',
    timestamp_column: str = 'timestamp',
    open_column: str = 'open',
    close_column: str = 'close',
    high_column: str = 'high',
    low_column: str = 'low',
    volume_column: str = 'volume',
) -> dict:
    """Preview deterministic cleanup and verify the normalized output with Jesse's import API."""
    try:
        cleaned_file, report = _clean_custom_csv(
            file_path,
            timestamp_format,
            timestamp_column,
            open_column,
            close_column,
            high_column,
            low_column,
            volume_column,
            'drop',
        )
        try:
            backend_preview = None
            if report['can_import_with_drop']:
                response = requests.post(
                    f'{mcp_config.JESSE_API_URL}/candles/custom/preview',
                    data=_custom_csv_form(symbol),
                    files={'file': ('cleaned-candles.csv', cleaned_file, 'text/csv')},
                    headers={'Authorization': hash_password(mcp_config.JESSE_PASSWORD)},
                    timeout=CUSTOM_CSV_REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code != 200:
                    return {
                        'status': 'error',
                        'action': 'custom_candle_csv_preview_failed',
                        'error_type': 'api_error',
                        'cleaning_report': report,
                        'message': f'Jesse rejected the normalized CSV: {response.text}',
                    }
                backend_preview = response.json().get('data')
            return {
                'status': 'success',
                'action': 'custom_candle_csv_previewed',
                'file_path': str(_custom_csv_path(file_path)),
                'symbol': symbol.strip().upper(),
                'cleaning_report': report,
                'import_preview': backend_preview,
                'message': (
                    'Preview complete. Choose invalid_row_policy="reject" or "drop" when importing; '
                    'conflicting duplicate timestamps must be fixed in the source file.'
                ),
            }
        finally:
            cleaned_file.close()
    except (CustomCandleImportError, OSError, ValueError) as exc:
        return {
            'status': 'error',
            'action': 'custom_candle_csv_preview_failed',
            'error_type': 'validation_error',
            'message': str(exc),
        }
    except requests.RequestException as exc:
        return {
            'status': 'error',
            'action': 'custom_candle_csv_preview_failed',
            'error_type': 'network_error',
            'message': f'Could not reach Jesse: {exc}',
        }


def clean_and_import_custom_candle_csv_service(
    file_path: str,
    symbol: str,
    invalid_row_policy: Literal['reject', 'drop'],
    timestamp_format: str = 'auto',
    timestamp_column: str = 'timestamp',
    open_column: str = 'open',
    close_column: str = 'close',
    high_column: str = 'high',
    low_column: str = 'low',
    volume_column: str = 'volume',
) -> dict:
    """Clean one local CSV and import its canonical rows through Jesse's custom-data endpoint."""
    try:
        cleaned_file, report = _clean_custom_csv(
            file_path,
            timestamp_format,
            timestamp_column,
            open_column,
            close_column,
            high_column,
            low_column,
            volume_column,
            invalid_row_policy,
        )
        try:
            if not report['valid']:
                return {
                    'status': 'error',
                    'action': 'custom_candle_csv_import_failed',
                    'error_type': 'validation_error',
                    'cleaning_report': report,
                    'message': (
                        'The selected cleaning policy cannot safely import this file. '
                        'Review invalid rows or conflicting duplicate timestamps.'
                    ),
                }
            response = requests.post(
                f'{mcp_config.JESSE_API_URL}/candles/custom/import',
                data=_custom_csv_form(symbol),
                files={'file': ('cleaned-candles.csv', cleaned_file, 'text/csv')},
                headers={'Authorization': hash_password(mcp_config.JESSE_PASSWORD)},
                timeout=CUSTOM_CSV_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 201:
                return {
                    'status': 'error',
                    'action': 'custom_candle_csv_import_failed',
                    'error_type': 'api_error',
                    'cleaning_report': report,
                    'message': f'Jesse rejected the normalized CSV: {response.text}',
                }
            imported = response.json().get('data', {})
            return {
                'status': 'success',
                'action': 'custom_candle_csv_imported',
                'exchange': imported.get('exchange', 'Custom Data'),
                'symbol': imported.get('symbol', symbol.strip().upper()),
                'timeframe': '1m',
                'cleaning_report': report,
                'import_report': imported,
                'message': (
                    f'Imported {report["cleaned_row_count"]} cleaned one-minute candles as Custom Data.'
                ),
            }
        finally:
            cleaned_file.close()
    except (CustomCandleImportError, OSError, ValueError) as exc:
        return {
            'status': 'error',
            'action': 'custom_candle_csv_import_failed',
            'error_type': 'validation_error',
            'message': str(exc),
        }
    except requests.RequestException as exc:
        return {
            'status': 'error',
            'action': 'custom_candle_csv_import_failed',
            'error_type': 'network_error',
            'message': f'Could not reach Jesse: {exc}',
        }


def import_candles_service(
    exchange: str,
    symbol: str,
    start_date: str,
    import_id: Optional[str] = None,
) -> dict:
    """
    Trigger a candle import and return immediately.

    Fires POST /candles/import and returns as soon as the server acknowledges (202).
    The caller is responsible for polling get_existing_candles_service() to confirm
    the data has landed.

    Args:
        exchange: Exchange name (e.g., 'Binance Spot', 'Bybit USDT Perpetual')
        symbol: Trading symbol (e.g., 'BTC-USDT', 'ETH-USDT')
        start_date: Start date in YYYY-MM-DD format
        import_id: Optional import ID to reuse for retrying a previous import.
                   If None, a new unique ID is generated.

    Returns:
        {"status": "started", "import_id": "...", ...} on success, or an error dict.
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        if import_id is None:
            import_id = str(uuid.uuid4())

        response = requests.post(
            f'{api_url}/candles/import',
            json={
                "id": import_id,
                "exchange": exchange,
                "symbol": symbol,
                "start_date": start_date
            },
            headers={'Authorization': auth_token_hashed},
            timeout=30
        )

        if response.status_code == 202:
            return {
                "status": "started",
                "action": "candle_import_started",
                "import_id": import_id,
                "exchange": exchange,
                "symbol": symbol,
                "start_date": start_date,
                "message": f"Candle import started for {symbol} on {exchange}. Poll get_candle_import_status(import_id) until it reaches a terminal state."
            }
        else:
            return {
                "status": "error",
                "action": "candle_import_failed",
                "exchange": exchange,
                "symbol": symbol,
                "error_type": "api_error",
                "message": f"Failed to start candle import: {response.text}"
            }

    except Exception as e:
        return {
            "status": "error",
            "action": "candle_import_failed",
            "exchange": exchange,
            "symbol": symbol,
            "error_type": "network_error",
            "message": f"Failed to start candle import: {str(e)}"
        }


def cancel_candle_import_service(
    import_id: str,
) -> dict:
    """
    Cancel an ongoing candle import process.

    Stops the import process for the specified import ID.

    Args:
        import_id: The import process ID to cancel

    Returns:
        Success confirmation or error message
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        # Make API request to cancel import
        response = requests.post(
            f'{api_url}/candles/cancel-import',
            json={"id": import_id},
            headers={'Authorization': auth_token_hashed},
            timeout=10
        )

        if response.status_code == 202:
            return {
                "status": "success",
                "action": "candle_import_cancelled",
                "import_id": import_id,
                "message": f"Candle import process {import_id} has been requested for termination"
            }
        else:
            return {
                "status": "error",
                "action": "cancel_failed",
                "import_id": import_id,
                "error_type": "api_error",
                "message": f"Failed to cancel import: {response.text}"
            }

    except ValueError as e:
        return {
            "status": "error",
            "action": "config_error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "cancel_failed",
            "import_id": import_id,
            "error_type": "network_error",
            "message": f"Network error during cancel: {str(e)}"
        }


def clear_candle_cache_service() -> dict:
    """
    Clear the candles database cache.

    Flushes the cache to ensure fresh data is loaded from the database.

    Returns:
        Success confirmation or error message
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        # Make API request to clear cache
        response = requests.post(
            f'{api_url}/candles/clear-cache',
            headers={'Authorization': auth_token_hashed},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "status": "success",
                "action": "cache_cleared",
                "message": data.get('message', 'Candles database cache cleared successfully')
            }
        else:
            return {
                "status": "error",
                "action": "cache_clear_failed",
                "error_type": "api_error",
                "message": f"Failed to clear cache: {response.text}"
            }

    except ValueError as e:
        return {
            "status": "error",
            "action": "config_error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "cache_clear_failed",
            "error_type": "network_error",
            "message": f"Network error during cache clear: {str(e)}"
        }


def get_candles_service(
    exchange: str,
    symbol: str,
    timeframe: str,
) -> dict:
    """
    Retrieve candle data for analysis.

    Gets historical candle data for the specified exchange, symbol, and timeframe.

    Args:
        exchange: Exchange name (e.g., 'Binance', 'Bybit')
        symbol: Trading symbol (e.g., 'BTC-USDT', 'ETH-USDT')
        timeframe: Timeframe (e.g., '1m', '5m', '1h', '1D', '1W', '1M')

    Returns:
        Candle data or error message
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Make API request to get candles
        response = requests.post(
            f'{api_url}/candles/get',
            json={
                "id": request_id,
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe
            },
            headers={'Authorization': auth_token_hashed},
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            candles = data.get('data', [])
            return {
                "status": "success",
                "action": "candles_retrieved",
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "candle_count": len(candles),
                "candles": candles,
                "message": f"Retrieved {len(candles)} candles for {symbol} on {exchange} ({timeframe})"
            }
        else:
            return {
                "status": "error",
                "action": "candles_retrieval_failed",
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "error_type": "api_error",
                "message": f"Failed to retrieve candles: {response.text}"
            }

    except ValueError as e:
        return {
            "status": "error",
            "action": "config_error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "candles_retrieval_failed",
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "error_type": "network_error",
            "message": f"Network error during candle retrieval: {str(e)}"
        }


def get_candle_import_status_service(import_id: str) -> dict:
    """
    Check whether a candle import process is still running.

    Single Redis SISMEMBER call on the server — no database queries, no timeout risk.

    Args:
        import_id: The import process ID returned by import_candles()

    Returns:
        {"status": "running"|"finished"|"failed"|"cancelled", "import_id": "...", ...}
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        response = requests.post(
            f'{api_url}/candles/import-status',
            json={"id": import_id},
            headers={'Authorization': auth_token_hashed},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            result = {
                "status": data.get("status"),
                "import_id": import_id,
                "message": f"Import {import_id} is {data.get('status')}."
            }
            if data.get('error') is not None:
                result['error'] = data['error']
                result['message'] = f"Import {import_id} failed: {data['error']}"
            if data.get('traceback') is not None:
                result['traceback'] = data['traceback']
            if data.get('result') is not None:
                result['result'] = data['result']
                result['message'] = data['result'].get('message', result['message'])
            # Surface live progress (percent complete, ETA, date reached so far) while
            # the import runs, so the caller sees it advancing instead of a blind "running".
            progress = data.get("progress")
            if progress:
                result["progress"] = progress
                pct = progress.get("current")
                eta = progress.get("estimated_remaining_seconds")
                reached = progress.get("current_date")
                bits = []
                if pct is not None:
                    bits.append(f"{pct}% complete")
                if reached:
                    bits.append(f"reached {reached}")
                if eta is not None:
                    bits.append(f"~{round(eta)}s remaining")
                if bits:
                    result["message"] = f"Import {import_id} is running ({', '.join(bits)})."
            return result
        else:
            return {
                "status": "error",
                "import_id": import_id,
                "error_type": "api_error",
                "message": f"Failed to get import status: {response.text}"
            }

    except Exception as e:
        return {
            "status": "error",
            "import_id": import_id,
            "error_type": "network_error",
            "message": f"Network error checking import status: {str(e)}"
        }


def get_existing_candles_service() -> dict:
    """
    List all imported candle data in the database.

    Returns information about all candles that have been imported and stored.

    Returns:
        List of existing candle data or error message
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        # Make API request to get existing candles
        response = requests.post(
            f'{api_url}/candles/existing',
            headers={'Authorization': auth_token_hashed},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            candles_data = data.get('data', [])
            return {
                "status": "success",
                "action": "existing_candles_retrieved",
                "candle_sets_count": len(candles_data),
                "candle_sets": candles_data,
                "message": f"Found {len(candles_data)} candle datasets in database"
            }
        else:
            return {
                "status": "error",
                "action": "existing_candles_retrieval_failed",
                "error_type": "api_error",
                "message": f"Failed to retrieve existing candles: {response.text}"
            }

    except ValueError as e:
        return {
            "status": "error",
            "action": "config_error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "existing_candles_retrieval_failed",
            "error_type": "network_error",
            "message": f"Network error during existing candles retrieval: {str(e)}"
        }


def delete_candles_service(
    exchange: str,
    symbol: str,
) -> dict:
    """
    Remove candle data from the database.

    Permanently deletes candle data for the specified exchange and symbol.

    Args:
        exchange: Exchange name (e.g., 'binance', 'bybit')
        symbol: Trading symbol (e.g., 'BTC-USDT', 'ETH-USDT')

    Returns:
        Success confirmation or error message
    """
    api_url = mcp_config.JESSE_API_URL
    password = mcp_config.JESSE_PASSWORD

    try:
        auth_token_hashed = hash_password(password)

        # Make API request to delete candles
        response = requests.post(
            f'{api_url}/candles/delete',
            json={
                "exchange": exchange,
                "symbol": symbol
            },
            headers={'Authorization': auth_token_hashed},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "status": "success",
                "action": "candles_deleted",
                "exchange": exchange,
                "symbol": symbol,
                "message": data.get('message', f'Candles for {symbol} on {exchange} deleted successfully')
            }
        else:
            return {
                "status": "error",
                "action": "candles_deletion_failed",
                "exchange": exchange,
                "symbol": symbol,
                "error_type": "api_error",
                "message": f"Failed to delete candles: {response.text}"
            }

    except ValueError as e:
        return {
            "status": "error",
            "action": "config_error",
            "message": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "action": "candles_deletion_failed",
            "exchange": exchange,
            "symbol": symbol,
            "error_type": "network_error",
            "message": f"Network error during candle deletion: {str(e)}"
        }

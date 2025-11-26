# FastMCP Integration

This document describes the FastMCP (Model Context Protocol) integration added to Jesse from PR #42.

## Overview

FastMCP integration allows external AI assistants to control Jesse background workers through a standardized protocol. This enables features like:
- Starting backtests remotely
- Managing running processes
- Monitoring worker status

## New CLI Command

```bash
jesse fastmcp [OPTIONS]
```

### Options

- `--transport`: Transport protocol (stdio, sse, streamable-http, http) [default: stdio]
- `--host`: Host address for HTTP/SSE transports [default: 127.0.0.1]
- `--port`: Port for HTTP/SSE transports [default: 8765]
- `--hide-banner`: Hide startup banner

### Examples

```bash
# Start MCP server with stdio transport (default)
jesse fastmcp

# Start MCP server with HTTP transport
jesse fastmcp --transport http --host 0.0.0.0 --port 8765

# Start MCP server with SSE transport
jesse fastmcp --transport sse --port 8765
```

## Available MCP Tools

### 1. start_backtest
Start a Jesse backtest in a managed worker process.

**Parameters:**
- `exchange`: Exchange name (e.g., "Sandbox")
- `routes`: List of trading routes with exchange, symbol, timeframe, strategy
- `data_routes`: List of data routes for additional timeframes
- `config`: Backtest configuration dictionary
- `start_date`: Start date (YYYY-MM-DD format)
- `finish_date`: End date (YYYY-MM-DD format)
- `id`: Optional unique identifier (auto-generated if not provided)
- `debug_mode`: Enable debug mode [default: False]
- `export_csv`: Export results to CSV [default: False]
- `export_json`: Export results to JSON [default: False]
- `export_chart`: Export chart [default: False]
- `export_tradingview`: Export TradingView data [default: False]
- `fast_mode`: Enable fast mode [default: False]
- `benchmark`: Enable benchmark mode [default: False]

### 2. list_processes
List all active Jesse worker identifiers managed by the process manager.

**Returns:** List of worker IDs

### 3. cancel_process
Request cancellation of a specific Jesse worker.

**Parameters:**
- `client_id`: Identifier of the worker to cancel

### 4. flush_processes
Terminate all running Jesse worker processes.

**Returns:** Status confirmation

### 5. list_strategies
List all available trading strategies in the current project.

**Parameters:** None

**Returns:** 
```json
{
  "strategies": [
    {
      "file": "SimpleStrategy.py",
      "name": "SimpleStrategy", 
      "classes": [
        {
          "name": "SimpleStrategy",
          "description": "Simple trading strategy using SMA crossover",
          "parameters": {...}
        }
      ]
    }
  ],
  "total_count": 2
}
```

### 6. get_strategy_info
Get detailed information about a specific trading strategy.

**Parameters:**
- `strategy_name`: Name of the strategy file (without .py extension)

**Returns:**
```json
{
  "strategy_file": "SimpleStrategy.py",
  "strategy_name": "SimpleStrategy",
  "classes": [
    {
      "name": "SimpleStrategy",
      "description": "Full description of the strategy...",
      "methods": {
        "should_long": {
          "doc": "Method documentation..."
        },
        "go_long": {
          "doc": "Method documentation..."
        }
      },
      "hyperparameters": {
        "rsi_period": [14, 21, 30]
      }
    }
  ]
}
```

## Dependencies

New dependencies added to requirements.txt:
- `fastmcp~=2.12.4` - Main MCP server framework
- Updated `uvicorn~=0.37.0` - For HTTP transport support
- Updated `python-dotenv~=1.1.0` - Environment configuration

## Code Structure

```
jesse/services/mcp/
├── __init__.py          # Module exports
└── server.py           # FastMCP server implementation
```

## Testing

Run the FastMCP integration tests:

```bash
pytest tests/test_fastmcp_integration.py
```

## Security Considerations

- MCP server validates current working directory before operations
- Process manager provides isolation for background tasks
- HTTP/SSE transports should be used with proper network security

## Transport Protocols

1. **stdio** (default): Standard input/output for local usage
2. **http**: HTTP server for web-based integrations
3. **sse**: Server-Sent Events for streaming communication
4. **streamable-http**: Enhanced HTTP with streaming capabilities

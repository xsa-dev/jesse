"""FastMCP server integration for Jesse."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import jesse.helpers as jh
from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from jesse.services.multiprocessing import process_manager
from jesse.services.web import BacktestRequestJson

_DEFAULT_INSTRUCTIONS = (
    "Control Jesse's background workers via the Model Context Protocol. "
    "Use the available tools to start new backtests, inspect running tasks, "
    "and request graceful shutdowns of worker processes."
)


class StartBacktestPayload(BaseModel):
    """Parameters accepted by the FastMCP backtest tool."""

    exchange: str
    routes: List[Dict[str, str]]
    data_routes: List[Dict[str, str]]
    config: Dict[str, Any]
    start_date: str
    finish_date: str
    id: Optional[str] = Field(
        default=None,
        description=(
            "Unique client identifier for the spawned worker. If omitted, an ID "
            "is generated automatically."
        ),
    )
    debug_mode: bool = False
    export_csv: bool = False
    export_json: bool = False
    export_chart: bool = False
    export_tradingview: bool = False
    fast_mode: bool = False
    benchmark: bool = False

    def to_backtest_request(self) -> BacktestRequestJson:
        """Convert payload to the internal request schema."""

        payload = self.model_dump()
        if not payload.get("id"):
            payload["id"] = uuid.uuid4().hex
        return BacktestRequestJson.model_validate(payload)


class CancelProcessPayload(BaseModel):
    """Payload for requesting cancellation of a running worker."""

    client_id: str = Field(description="Identifier of the worker to cancel.")


def create_fastmcp_server(
    *,
    name: str | None = None,
    instructions: str | None = None,
) -> FastMCP:
    """Instantiate a FastMCP server with Jesse specific tools."""

    server = FastMCP(
        name=name or "Jesse MCP Server",
        instructions=instructions or _DEFAULT_INSTRUCTIONS,
    )

    @server.tool(
        name="start_backtest",
        description="Start a Jesse backtest in a managed worker process.",
        tags=["backtest", "jesse"],
    )
    def start_backtest(
        payload: StartBacktestPayload,
        ctx: Context | None = None,
    ) -> Dict[str, str]:
        request = payload.to_backtest_request()
        jh.validate_cwd()

        from jesse.modes.backtest_mode import run as run_backtest

        process_manager.add_task(
            run_backtest,
            request.id,
            request.debug_mode,
            request.config,
            request.exchange,
            request.routes,
            request.data_routes,
            request.start_date,
            request.finish_date,
            None,
            request.export_chart,
            request.export_tradingview,
            request.export_csv,
            request.export_json,
            request.fast_mode,
            request.benchmark,
        )

        message = f"Backtest session {request.id} started"
        if ctx is not None:
            ctx.info(message)
        return {"status": "started", "id": request.id}

    @server.tool(
        name="list_processes",
        description="List active Jesse worker identifiers managed by the process manager.",
        tags=["jesse"],
    )
    def list_processes() -> List[str]:
        return sorted(process_manager.active_workers)

    @server.tool(
        name="cancel_process",
        description="Request cancellation of a Jesse worker by its client identifier.",
        tags=["jesse"],
    )
    def cancel_process(
        payload: CancelProcessPayload,
        ctx: Context | None = None,
    ) -> Dict[str, str]:
        process_manager.cancel_process(payload.client_id)
        message = f"Cancellation requested for worker {payload.client_id}"
        if ctx is not None:
            ctx.info(message)
        return {"status": "requested", "id": payload.client_id}

    @server.tool(
        name="flush_processes",
        description="Terminate all running Jesse worker processes.",
        tags=["jesse"],
    )
    def flush_processes(ctx: Context | None = None) -> Dict[str, str]:
        process_manager.flush()
        message = "All Jesse workers were terminated"
        if ctx is not None:
            ctx.info(message)
        return {"status": "terminated"}

    @server.tool(
        name="list_strategies",
        description="List all available trading strategies in the project.",
        tags=["jesse", "strategies"],
    )
    def list_strategies(ctx: Context | None = None) -> Dict[str, Any]:
        """List all available Jesse strategies in the current project."""
        import os
        import importlib.util
        import inspect
        from jesse.strategies import Strategy
        
        strategies_dir = "strategies"
        strategies = []
        
        if os.path.exists(strategies_dir):
            for filename in os.listdir(strategies_dir):
                if filename.endswith('.py') and not filename.startswith('__'):
                    strategy_name = filename[:-3]  # Remove .py extension
                    
                    try:
                        # Try to get strategy info
                        file_path = os.path.join(strategies_dir, filename)
                        spec = importlib.util.spec_from_file_location(strategy_name, file_path)
                        
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            
                            # Load module to extract class info
                            spec.loader.exec_module(module)
                            
                            # Find strategy classes
                            strategy_classes = []
                            for name, obj in inspect.getmembers(module):
                                if (inspect.isclass(obj) and 
                                    issubclass(obj, Strategy) and 
                                    obj != Strategy):
                                    
                                    # Get docstring if available
                                    doc = inspect.getdoc(obj) or "No description available"
                                    
                                    # Get strategy parameters
                                    params = {}
                                    if hasattr(obj, 'hyperparameters'):
                                        params['parameters'] = obj.hyperparameters()
                                    
                                    strategy_classes.append({
                                        "name": name,
                                        "description": doc.split('\n')[0] if doc else "No description",
                                        "parameters": params
                                    })
                            
                            strategies.append({
                                "file": filename,
                                "name": strategy_name,
                                "classes": strategy_classes
                            })
                            
                    except Exception as e:
                        strategies.append({
                            "file": filename,
                            "name": strategy_name,
                            "error": str(e),
                            "classes": []
                        })
        
        result = {
            "strategies": strategies,
            "total_count": len(strategies)
        }
        
        message = f"Found {len(strategies)} strategy files"
        if ctx is not None:
            ctx.info(message)
            
        return result

    @server.tool(
        name="get_strategy_info",
        description="Get detailed information about a specific strategy.",
        tags=["jesse", "strategies"],
    )
    def get_strategy_info(
        strategy_name: str,
        ctx: Context | None = None,
    ) -> Dict[str, Any]:
        """Get detailed information about a specific trading strategy."""
        import os
        import importlib.util
        import inspect
        from jesse.strategies import Strategy
        
        strategies_dir = "strategies"
        strategy_file = f"{strategy_name}.py"
        strategy_path = os.path.join(strategies_dir, strategy_file)
        
        if not os.path.exists(strategy_path):
            error_msg = f"Strategy file '{strategy_file}' not found"
            if ctx is not None:
                ctx.error(error_msg)
            return {"error": error_msg}
        
        try:
            # Load the strategy module
            spec = importlib.util.spec_from_file_location(strategy_name, strategy_path)
            if not spec or not spec.loader:
                return {"error": f"Could not load strategy file '{strategy_file}'"}
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find strategy classes
            strategy_classes = []
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, Strategy) and 
                    obj != Strategy):
                    
                    # Get detailed class information
                    doc = inspect.getdoc(obj) or "No description available"
                    
                    # Get method information
                    methods = {}
                    for method_name in ['should_long', 'should_short', 'should_cancel_entry', 
                                     'go_long', 'go_short', 'update_position', 'before', 'after']:
                        if hasattr(obj, method_name):
                            method = getattr(obj, method_name)
                            methods[method_name] = {
                                "doc": inspect.getdoc(method) or "No description",
                                "source": inspect.getsource(method) if hasattr(method, '__code__') else None
                            }
                    
                    # Get hyperparameters
                    hyperparameters = {}
                    if hasattr(obj, 'hyperparameters'):
                        hyperparameters = obj.hyperparameters()
                    
                    strategy_classes.append({
                        "name": name,
                        "description": doc,
                        "methods": methods,
                        "hyperparameters": hyperparameters
                    })
            
            if not strategy_classes:
                return {"error": f"No valid strategy classes found in '{strategy_file}'"}
            
            result = {
                "strategy_file": strategy_file,
                "strategy_name": strategy_name,
                "classes": strategy_classes
            }
            
            message = f"Successfully loaded strategy '{strategy_name}'"
            if ctx is not None:
                ctx.info(message)
                
            return result
            
        except Exception as e:
            error_msg = f"Error loading strategy '{strategy_name}': {str(e)}"
            if ctx is not None:
                ctx.error(error_msg)
            return {"error": error_msg}

    return server


def run_fastmcp_server(
    *,
    transport: str = "stdio",
    host: str | None = None,
    port: int | None = None,
    show_banner: bool = True,
    **transport_kwargs: Any,
) -> None:
    """Run the FastMCP server using the requested transport."""

    jh.validate_cwd()
    server = create_fastmcp_server()

    supported_http_transports = {"http", "sse", "streamable-http"}
    kwargs: Dict[str, Any] = dict(transport_kwargs)

    if transport in supported_http_transports:
        if host is not None:
            kwargs.setdefault("host", host)
        if port is not None:
            kwargs.setdefault("port", port)

    server.run(transport=transport, show_banner=show_banner, **kwargs)

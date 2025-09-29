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

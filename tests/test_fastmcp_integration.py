"""Tests for the FastMCP integration layer."""

from pathlib import Path
from types import SimpleNamespace
import sys


if "pkg_resources" not in sys.modules:  # pragma: no cover - test helper setup
    class _PkgResourcesStub:
        def resource_filename(self, package: str, resource: str) -> str:
            base = Path(__file__).resolve().parent.parent / package
            target = base if not resource else base / resource
            return str(target)

        def get_distribution(self, name: str) -> SimpleNamespace:
            return SimpleNamespace(version="0.0.0")

    sys.modules["pkg_resources"] = _PkgResourcesStub()

from jesse.services.mcp import create_fastmcp_server
from jesse.services.mcp.server import StartBacktestPayload


def test_fastmcp_server_registers_expected_tools():
    server = create_fastmcp_server()
    tool_names = set(server._tool_manager._tools.keys())
    expected_tools = {"start_backtest", "list_processes", "cancel_process", "flush_processes", "list_strategies", "get_strategy_info"}
    assert expected_tools <= tool_names


def test_start_backtest_payload_generates_identifier():
    payload = StartBacktestPayload(
        exchange="Sandbox",
        routes=[
            {
                "exchange": "Sandbox",
                "symbol": "BTC-USDT",
                "timeframe": "1m",
                "strategy": "Example",
            }
        ],
        data_routes=[{"exchange": "Sandbox", "symbol": "BTC-USDT", "timeframe": "5m"}],
        config={},
        start_date="2020-01-01",
        finish_date="2020-01-02",
    )

    request = payload.to_backtest_request()

    assert request.id is not None
    assert request.exchange == "Sandbox"

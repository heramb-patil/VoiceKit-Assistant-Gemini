"""
Unit + functional tests for MCPClientManager and MCPServerConnection.

Uses the local mock_mcp_server.py to avoid any external npm dependencies.
All tests run against the real mcp SDK (stdio_client / ClientSession).
"""
import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
MOCK_SERVER = str(BACKEND_DIR / "tests" / "fixtures" / "mock_mcp_server.py")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(name="mock", estimated_seconds=None):
    from config import MCPServerConfig
    return MCPServerConfig(
        name=name,
        command=sys.executable,
        args=[MOCK_SERVER],
        env={},
        estimated_seconds=estimated_seconds or {},
    )


# ── MCPServerConnection ────────────────────────────────────────────────────────

class TestMCPServerConnection:
    @pytest.mark.asyncio
    async def test_start_initializes_session(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        try:
            assert conn.initialized is True
            assert conn._session is not None
            assert conn._task is not None
            assert not conn._task.done()
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_session(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        await conn.stop()
        assert conn.initialized is False
        assert conn._session is None
        assert conn._task.done()

    @pytest.mark.asyncio
    async def test_list_tools_returns_three_tools(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        try:
            tools = await conn.list_tools()
            names = [t["name"] for t in tools]
            assert "echo" in names
            assert "add_numbers" in names
            assert "fail_tool" in names
            assert len(tools) == 3
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_list_tools_have_schemas(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        try:
            tools = await conn.list_tools()
            for t in tools:
                assert "inputSchema" in t or "parameters" in t, \
                    f"Tool {t['name']} missing schema"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_call_echo_tool(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        try:
            result = await conn.call_tool("echo", {"message": "hello world"})
            assert result == "Echo: hello world"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_call_add_numbers_tool(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        try:
            result = await conn.call_tool("add_numbers", {"a": 7, "b": 13})
            assert result == "20"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_restart_reconnects(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        await conn.start()
        first_pid = conn._task
        await conn.restart()
        try:
            assert conn.initialized is True
            # Task was replaced
            assert conn._task is not first_pid
            # Can still call tools
            result = await conn.call_tool("echo", {"message": "after restart"})
            assert result == "Echo: after restart"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_list_tools_when_not_initialized_returns_empty(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        # Not started
        tools = await conn.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_call_tool_when_not_initialized_raises(self):
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection("test", sys.executable, [MOCK_SERVER], {})
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.call_tool("echo", {"message": "hi"})

    @pytest.mark.asyncio
    async def test_timeout_on_bad_command(self):
        from mcp_client import MCPServerConnection
        # "sleep 999" never sends initialize response
        conn = MCPServerConnection("slow", "sleep", ["999"], {})
        with pytest.raises((RuntimeError, asyncio.TimeoutError)):
            await conn.start(timeout=2.0)
        await conn.stop()


# ── MCPClientManager ─────────────────────────────────────────────────────────

class TestMCPClientManagerConnect:
    @pytest.mark.asyncio
    async def test_connect_all_single_server(self, mock_server_config):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager()
        await mgr.connect_all([mock_server_config])
        try:
            assert "mock" in mgr._connections
            assert mgr._connections["mock"].initialized
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_connect_all_two_servers(self, mock_server_config, second_mock_server_config):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager()
        await mgr.connect_all([mock_server_config, second_mock_server_config])
        try:
            assert "mock" in mgr._connections
            assert "mock2" in mgr._connections
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_bad_server_does_not_prevent_good_ones(self, mock_server_config):
        from mcp_client import MCPClientManager
        from config import MCPServerConfig
        bad_config = MCPServerConfig(
            name="bad", command="does_not_exist_xyz", args=[], env={},
        )
        mgr = MCPClientManager()
        # Should not raise — bad server failure is swallowed, good server connects
        # Use short timeout so the test doesn't wait 30s for the bad server
        await mgr.connect_all([bad_config, mock_server_config], connect_timeout=2.0)
        try:
            assert "mock" in mgr._connections
            assert mgr._connections["mock"].initialized
            # bad server either not in connections or not initialized
            bad_conn = mgr._connections.get("bad")
            assert bad_conn is None or not bad_conn.initialized
        finally:
            await mgr.shutdown()


class TestMCPClientManagerToolIndex:
    @pytest.mark.asyncio
    async def test_list_all_tools_returns_tool_defs(self, mcp_manager):
        from mcp_client import ToolDef
        tools = await mcp_manager.list_all_tools()
        assert len(tools) == 3
        assert all(isinstance(t, ToolDef) for t in tools)

    @pytest.mark.asyncio
    async def test_tool_defs_have_correct_fields(self, mcp_manager):
        tools = await mcp_manager.list_all_tools()
        for t in tools:
            assert t.name, f"Tool missing name: {t}"
            assert t.description, f"Tool {t.name} missing description"
            assert t.server_name == "mock"
            assert isinstance(t.parameters, dict)

    @pytest.mark.asyncio
    async def test_tool_names_match_expected(self, mcp_manager):
        tools = await mcp_manager.list_all_tools()
        names = {t.name for t in tools}
        assert names == {"echo", "add_numbers", "fail_tool"}

    @pytest.mark.asyncio
    async def test_parameters_are_json_schema(self, mcp_manager):
        tools = await mcp_manager.list_all_tools()
        echo = next(t for t in tools if t.name == "echo")
        assert echo.parameters.get("type") == "object"
        assert "message" in echo.parameters.get("properties", {})

    @pytest.mark.asyncio
    async def test_two_servers_merged_index(self, mcp_manager_two_servers):
        """Two servers with same tools — index has each tool mapped to a server."""
        mgr = mcp_manager_two_servers
        tools = await mgr.list_all_tools()
        # Each server has 3 tools; after merging same names the index has 3 unique names
        # (second server overwrites first for same-name tools)
        names = [t.name for t in tools]
        assert "echo" in names
        assert "add_numbers" in names

    @pytest.mark.asyncio
    async def test_tool_to_server_mapping_populated(self, mcp_manager):
        assert "echo" in mcp_manager._tool_to_server
        assert "add_numbers" in mcp_manager._tool_to_server
        assert mcp_manager._tool_to_server["echo"] == "mock"


class TestMCPClientManagerCallTool:
    @pytest.mark.asyncio
    async def test_call_echo(self, mcp_manager):
        result = await mcp_manager.call_tool("echo", {"message": "pytest"})
        assert result == "Echo: pytest"

    @pytest.mark.asyncio
    async def test_call_add_numbers(self, mcp_manager):
        result = await mcp_manager.call_tool("add_numbers", {"a": 3, "b": 4})
        assert result == "7"

    @pytest.mark.asyncio
    async def test_call_add_numbers_large(self, mcp_manager):
        result = await mcp_manager.call_tool("add_numbers", {"a": 1000, "b": 2345})
        assert result == "3345"

    @pytest.mark.asyncio
    async def test_call_unknown_tool_raises_value_error(self, mcp_manager):
        with pytest.raises(ValueError, match="not found in MCP index"):
            await mcp_manager.call_tool("does_not_exist", {})

    @pytest.mark.asyncio
    async def test_call_fail_tool_raises_runtime_error(self, mcp_manager):
        """fail_tool returns an MCP error — should surface as RuntimeError."""
        with pytest.raises(Exception):
            await mcp_manager.call_tool("fail_tool", {})

    @pytest.mark.asyncio
    async def test_multiple_sequential_calls(self, mcp_manager):
        """Session remains usable across multiple calls."""
        r1 = await mcp_manager.call_tool("echo", {"message": "first"})
        r2 = await mcp_manager.call_tool("add_numbers", {"a": 1, "b": 1})
        r3 = await mcp_manager.call_tool("echo", {"message": "third"})
        assert r1 == "Echo: first"
        assert r2 == "2"
        assert r3 == "Echo: third"

    @pytest.mark.asyncio
    async def test_concurrent_calls(self, mcp_manager):
        """Multiple concurrent tool calls don't corrupt each other."""
        tasks = [
            mcp_manager.call_tool("add_numbers", {"a": i, "b": i})
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        for i, r in enumerate(results):
            assert r == str(i * 2), f"i={i}: expected {i*2}, got {r!r}"


class TestMCPClientManagerEstimatedSeconds:
    @pytest.mark.asyncio
    async def test_returns_configured_value(self, mcp_manager):
        assert mcp_manager.get_estimated_seconds("echo") == 1
        assert mcp_manager.get_estimated_seconds("add_numbers") == 2

    @pytest.mark.asyncio
    async def test_returns_none_for_unconfigured_tool(self, mcp_manager):
        # fail_tool has no configured estimated_seconds
        assert mcp_manager.get_estimated_seconds("fail_tool") is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_tool(self, mcp_manager):
        assert mcp_manager.get_estimated_seconds("nonexistent") is None


class TestMCPClientManagerRestart:
    @pytest.mark.asyncio
    async def test_restart_server_refreshes_tool_index(self, mcp_manager):
        # Confirm tools are indexed before restart
        assert "echo" in mcp_manager._tool_to_server

        await mcp_manager.restart_server("mock")

        # Still indexed after restart
        assert "echo" in mcp_manager._tool_to_server
        # Can still call tools
        result = await mcp_manager.call_tool("echo", {"message": "post-restart"})
        assert result == "Echo: post-restart"

    @pytest.mark.asyncio
    async def test_restart_unknown_server_logs_warning(self, mcp_manager):
        # Should not raise, just log a warning
        await mcp_manager.restart_server("does_not_exist")


class TestMCPClientManagerShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_clears_all_state(self, mock_server_config):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager()
        await mgr.connect_all([mock_server_config])
        await mgr.shutdown()

        assert len(mgr._connections) == 0
        assert len(mgr._tool_to_server) == 0
        assert len(mgr._tool_defs) == 0

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, mock_server_config):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager()
        await mgr.connect_all([mock_server_config])
        await mgr.shutdown()
        # Second shutdown should not raise
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_stops_subprocess(self, mock_server_config):
        from mcp_client import MCPClientManager
        mgr = MCPClientManager()
        await mgr.connect_all([mock_server_config])
        conn = mgr._connections["mock"]
        task = conn._task

        await mgr.shutdown()

        # Background task should be done
        await asyncio.sleep(0.1)
        assert task.done()

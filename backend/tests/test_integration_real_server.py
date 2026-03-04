"""
Integration tests against the real @modelcontextprotocol/server-filesystem.

These tests require:
  • Node.js + npx available on PATH
  • Network access (first run downloads the package via npx --yes)

Skipped automatically if npx is not available.
"""
import asyncio
import subprocess
import sys
import tempfile
import os
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# ── Skip guard ────────────────────────────────────────────────────────────────

def _npx_available() -> bool:
    try:
        r = subprocess.run(["npx", "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_npx = pytest.mark.skipif(
    not _npx_available(),
    reason="npx not available — skipping real MCP server tests",
)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    """A temporary directory that the filesystem MCP server is allowed to access."""
    with tempfile.TemporaryDirectory() as d:
        # Resolve symlinks so the path matches what the MCP server sees
        # (on macOS, /var/folders/... is a symlink to /private/var/folders/...)
        yield os.path.realpath(d)


@pytest_asyncio.fixture
async def fs_manager(tmp_dir):
    """MCPClientManager connected to the real filesystem MCP server."""
    from mcp_client import MCPClientManager
    from config import MCPServerConfig

    cfg = MCPServerConfig(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", tmp_dir],
        env={},
        estimated_seconds={"write_file": 3, "read_file": 2, "list_directory": 1},
    )
    mgr = MCPClientManager()
    await mgr.connect_all([cfg])
    yield mgr, tmp_dir
    await mgr.shutdown()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRealFilesystemServer:

    @requires_npx
    @pytest.mark.asyncio
    async def test_connects_and_discovers_tools(self, fs_manager):
        mgr, _ = fs_manager
        tools = await mgr.list_all_tools()
        assert len(tools) > 0
        names = [t.name for t in tools]
        assert "read_file" in names or "list_directory" in names

    @requires_npx
    @pytest.mark.asyncio
    async def test_all_tools_have_valid_schemas(self, fs_manager):
        mgr, _ = fs_manager
        tools = await mgr.list_all_tools()
        for t in tools:
            assert t.parameters.get("type") == "object", \
                f"{t.name}: parameters.type != 'object'"
            assert "properties" in t.parameters, \
                f"{t.name}: missing 'properties'"

    @requires_npx
    @pytest.mark.asyncio
    async def test_tool_defs_have_server_name(self, fs_manager):
        mgr, _ = fs_manager
        tools = await mgr.list_all_tools()
        assert all(t.server_name == "filesystem" for t in tools)

    @requires_npx
    @pytest.mark.asyncio
    async def test_list_allowed_directories(self, fs_manager):
        mgr, tmp_dir = fs_manager
        result = await mgr.call_tool("list_allowed_directories", {})
        assert isinstance(result, str)
        assert len(result) > 0

    @requires_npx
    @pytest.mark.asyncio
    async def test_write_then_read_file(self, fs_manager):
        mgr, tmp_dir = fs_manager
        file_path = str(Path(tmp_dir) / "test_roundtrip.txt")
        content = "Hello from MCP integration test"

        write_result = await mgr.call_tool("write_file", {
            "path": file_path,
            "content": content,
        })
        assert write_result is not None

        read_result = await mgr.call_tool("read_file", {"path": file_path})
        assert content in read_result

    @requires_npx
    @pytest.mark.asyncio
    async def test_create_and_list_directory(self, fs_manager):
        mgr, tmp_dir = fs_manager
        new_dir = str(Path(tmp_dir) / "subdir")

        await mgr.call_tool("create_directory", {"path": new_dir})

        list_result = await mgr.call_tool("list_directory", {"path": tmp_dir})
        assert "subdir" in list_result

    @requires_npx
    @pytest.mark.asyncio
    async def test_multiple_concurrent_writes(self, fs_manager):
        """Concurrent tool calls complete without corrupting each other."""
        mgr, tmp_dir = fs_manager

        async def write(i: int):
            path = str(Path(tmp_dir) / f"file_{i}.txt")
            await mgr.call_tool("write_file", {"path": path, "content": f"content_{i}"})
            return await mgr.call_tool("read_file", {"path": path})

        results = await asyncio.gather(*[write(i) for i in range(5)])
        for i, result in enumerate(results):
            assert f"content_{i}" in result

    @requires_npx
    @pytest.mark.asyncio
    async def test_estimated_seconds_override(self, fs_manager):
        mgr, _ = fs_manager
        assert mgr.get_estimated_seconds("write_file") == 3
        assert mgr.get_estimated_seconds("read_file") == 2
        assert mgr.get_estimated_seconds("list_directory") == 1

    @requires_npx
    @pytest.mark.asyncio
    async def test_path_outside_allowed_raises(self, fs_manager):
        """Accessing /etc outside the allowed tmp_dir returns an error string."""
        mgr, _ = fs_manager
        result = await mgr.call_tool("read_file", {"path": "/etc/passwd"})
        # Server returns error content, not raises (it's a valid tool response)
        assert "denied" in result.lower() or "not allowed" in result.lower() \
            or "outside" in result.lower()

    @requires_npx
    @pytest.mark.asyncio
    async def test_restart_reconnects_and_retains_tools(self, fs_manager):
        mgr, tmp_dir = fs_manager

        # Write before restart
        file_path = str(Path(tmp_dir) / "pre_restart.txt")
        await mgr.call_tool("write_file", {"path": file_path, "content": "before"})

        # Restart the server
        await mgr.restart_server("filesystem")

        # Should still work after restart
        tools = await mgr.list_all_tools()
        assert len(tools) > 0

        # Filesystem persists (file survived restart)
        result = await mgr.call_tool("read_file", {"path": file_path})
        assert "before" in result

    @requires_npx
    @pytest.mark.asyncio
    async def test_sjf_estimated_seconds_in_tool_metadata(self):
        """
        Confirms that filesystem server timing overrides appear in /tools
        tool_metadata when the full API stack is running with MCP enabled.
        """
        import httpx
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=BACKEND_DIR / ".env", override=True)

        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as _d:
            d = os.path.realpath(_d)
            import json
            servers_json = json.dumps([{
                "name": "filesystem",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", d],
                "env": {},
                "estimated_seconds": {"write_file": 7, "read_file": 4},
            }])
            os.environ["GEMINI_LIVE_MCP_ENABLED"] = "true"
            os.environ["GEMINI_LIVE_MCP_SERVERS_JSON"] = servers_json

            for mod in ["config", "orchestration", "api", "main"]:
                sys.modules.pop(mod, None)

            from main import app
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                data = (await client.get("/gemini-live/tools")).json()

            del os.environ["GEMINI_LIVE_MCP_ENABLED"]
            del os.environ["GEMINI_LIVE_MCP_SERVERS_JSON"]

        meta = data["tool_metadata"]
        assert meta.get("write_file", {}).get("estimated_seconds") == 7
        assert meta.get("read_file", {}).get("estimated_seconds") == 4

"""
Shared pytest fixtures for MCP integration tests.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

# Mock user returned by get_current_user in tests (bypasses JWT validation)
TEST_USER = {"email": "test@example.com", "name": "Test User", "picture": "", "sub": "test-sub-123"}

# ── Path setup ──────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent.parent
MOCK_SERVER = str(BACKEND_DIR / "tests" / "fixtures" / "mock_mcp_server.py")
sys.path.insert(0, str(BACKEND_DIR))


# ── Reset orchestration singleton between tests ──────────────────────────────

@pytest.fixture(autouse=True)
def reset_orchestration():
    """
    Reset the orchestration singleton before and after every test so tests
    don't bleed state into each other.
    """
    import orchestration as _orch_mod
    _orch_mod._orchestration = None
    yield
    _orch_mod._orchestration = None


# ── Config helpers ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_server_config():
    """MCPServerConfig pointing to the local mock MCP server."""
    # Import here so BACKEND_DIR is already on sys.path
    from config import MCPServerConfig
    return MCPServerConfig(
        name="mock",
        command=sys.executable,
        args=[MOCK_SERVER],
        env={},
        estimated_seconds={"echo": 1, "add_numbers": 2},
    )


@pytest.fixture
def second_mock_server_config():
    """A second MCPServerConfig (same binary, different name) for multi-server tests."""
    from config import MCPServerConfig
    return MCPServerConfig(
        name="mock2",
        command=sys.executable,
        args=[MOCK_SERVER],
        env={},
        estimated_seconds={},
    )


# ── Live MCPClientManager fixture ────────────────────────────────────────────

@pytest_asyncio.fixture
async def mcp_manager(mock_server_config):
    """MCPClientManager connected to the mock server. Shuts down after test."""
    from mcp_client import MCPClientManager
    mgr = MCPClientManager()
    await mgr.connect_all([mock_server_config])
    yield mgr
    await mgr.shutdown()


@pytest_asyncio.fixture
async def mcp_manager_two_servers(mock_server_config, second_mock_server_config):
    """MCPClientManager connected to two mock servers."""
    from mcp_client import MCPClientManager
    mgr = MCPClientManager()
    await mgr.connect_all([mock_server_config, second_mock_server_config])
    yield mgr
    await mgr.shutdown()


# ── FastAPI test client ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator:
    """
    httpx.AsyncClient pointed at the FastAPI app via ASGI transport.
    LifespanManager triggers startup/shutdown so bg_queue and orchestration
    are fully initialized, matching production behaviour.
    """
    import httpx
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=BACKEND_DIR / ".env", override=True)

    # Ensure MCP is disabled for plain endpoint tests
    os.environ.pop("GEMINI_LIVE_MCP_ENABLED", None)
    os.environ.pop("GEMINI_LIVE_MCP_SERVERS_JSON", None)

    # Use a temp SQLite DB so tests don't depend on the production data/ path.
    # Set GEMINI_LIVE_DB_URL (absolute URL) — env vars take priority over env_file
    # in pydantic-settings, so this overrides the relative voicekit_db_path in .env.
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["GEMINI_LIVE_DB_URL"] = f"sqlite+aiosqlite:///{tmpdir}/voicekit.db"

        # Fresh module imports so each test gets an isolated app + singletons
        for mod in ["config", "orchestration", "api", "main", "auth"]:
            sys.modules.pop(mod, None)

        from main import app
        from auth import get_current_user

        # Override auth: skip JWT validation in tests, return a fixed test user
        app.dependency_overrides[get_current_user] = lambda: TEST_USER

        async with LifespanManager(app) as manager:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
            ) as client:
                yield client

        app.dependency_overrides.clear()
        del os.environ["GEMINI_LIVE_DB_URL"]


@pytest_asyncio.fixture
async def async_client_with_mcp(mock_server_config) -> AsyncGenerator:
    """
    AsyncClient backed by a FastAPI app that has MCP enabled (mock server).
    LifespanManager ensures the MCP servers are started during the lifespan.
    """
    import httpx
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=BACKEND_DIR / ".env", override=True)

    servers_json = json.dumps([{
        "name": mock_server_config.name,
        "command": mock_server_config.command,
        "args": mock_server_config.args,
        "env": mock_server_config.env,
        "estimated_seconds": mock_server_config.estimated_seconds,
    }])
    os.environ["GEMINI_LIVE_MCP_ENABLED"] = "true"
    os.environ["GEMINI_LIVE_MCP_SERVERS_JSON"] = servers_json

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["GEMINI_LIVE_DB_URL"] = f"sqlite+aiosqlite:///{tmpdir}/voicekit.db"

        for mod in ["config", "orchestration", "api", "main", "auth"]:
            sys.modules.pop(mod, None)

        from main import app
        from auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: TEST_USER

        async with LifespanManager(app) as manager:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
            ) as client:
                yield client

        app.dependency_overrides.clear()
        del os.environ["GEMINI_LIVE_DB_URL"]

        app.dependency_overrides.clear()

    del os.environ["GEMINI_LIVE_MCP_ENABLED"]
    del os.environ["GEMINI_LIVE_MCP_SERVERS_JSON"]

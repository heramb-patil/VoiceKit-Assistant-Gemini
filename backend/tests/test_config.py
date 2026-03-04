"""
Unit tests for MCPServerConfig / MCPConfig parsing in config.py.

These tests manipulate environment variables and re-import the config module
to ensure env-driven configuration works correctly.
"""
import json
import os
import sys

import pytest


def _fresh_config(env_overrides: dict = None) -> "GeminiLiveBackendConfig":
    """Re-import config with a clean module cache and optional env overrides."""
    for key in list(os.environ.keys()):
        if key.startswith("GEMINI_LIVE_MCP"):
            del os.environ[key]

    if env_overrides:
        os.environ.update(env_overrides)

    sys.modules.pop("config", None)
    from config import GeminiLiveBackendConfig
    return GeminiLiveBackendConfig()


@pytest.fixture(autouse=True)
def _clean_mcp_env():
    """Remove MCP env vars before/after every test in this file."""
    for k in list(os.environ):
        if k.startswith("GEMINI_LIVE_MCP"):
            del os.environ[k]
    yield
    for k in list(os.environ):
        if k.startswith("GEMINI_LIVE_MCP"):
            del os.environ[k]


# ── MCPConfig defaults ────────────────────────────────────────────────────────

class TestMCPConfigDefaults:
    def test_mcp_disabled_by_default(self):
        cfg = _fresh_config()
        assert cfg.mcp.enabled is False

    def test_no_servers_by_default(self):
        cfg = _fresh_config()
        assert cfg.mcp.servers == []

    def test_mcp_returns_mcp_config_type(self):
        # _fresh_config re-imports the module, creating a new class object.
        # Use the class from the same fresh import to avoid identity mismatch.
        cfg = _fresh_config()
        import config as _cfg_mod
        assert isinstance(cfg.mcp, _cfg_mod.MCPConfig)


# ── Enabling MCP ──────────────────────────────────────────────────────────────

class TestMCPEnabled:
    def test_enable_via_env(self):
        cfg = _fresh_config({"GEMINI_LIVE_MCP_ENABLED": "true"})
        assert cfg.mcp.enabled is True

    def test_disabled_explicitly(self):
        cfg = _fresh_config({"GEMINI_LIVE_MCP_ENABLED": "false"})
        assert cfg.mcp.enabled is False


# ── MCPServerConfig parsing ───────────────────────────────────────────────────

class TestMCPServersParsing:
    def test_single_server_minimal(self):
        servers = [{"name": "test", "command": "python3", "args": ["server.py"]}]
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_ENABLED": "true",
            "GEMINI_LIVE_MCP_SERVERS_JSON": json.dumps(servers),
        })
        mcp = cfg.mcp
        assert len(mcp.servers) == 1
        srv = mcp.servers[0]
        assert srv.name == "test"
        assert srv.command == "python3"
        assert srv.args == ["server.py"]
        assert srv.env == {}
        assert srv.estimated_seconds == {}

    def test_server_with_env_and_timing(self):
        servers = [{
            "name": "google",
            "command": "npx",
            "args": ["-y", "google-workspace-mcp"],
            "env": {"TOKEN_FILE": "/tmp/token.json"},
            "estimated_seconds": {"send_email": 5, "get_recent_emails": 6},
        }]
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_ENABLED": "true",
            "GEMINI_LIVE_MCP_SERVERS_JSON": json.dumps(servers),
        })
        srv = cfg.mcp.servers[0]
        assert srv.env == {"TOKEN_FILE": "/tmp/token.json"}
        assert srv.estimated_seconds == {"send_email": 5, "get_recent_emails": 6}

    def test_multiple_servers(self):
        servers = [
            {"name": "google", "command": "node", "args": ["google.js"]},
            {"name": "basecamp", "command": "node", "args": ["basecamp.js"]},
        ]
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_ENABLED": "true",
            "GEMINI_LIVE_MCP_SERVERS_JSON": json.dumps(servers),
        })
        assert len(cfg.mcp.servers) == 2
        assert cfg.mcp.servers[0].name == "google"
        assert cfg.mcp.servers[1].name == "basecamp"

    def test_empty_servers_list(self):
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_ENABLED": "true",
            "GEMINI_LIVE_MCP_SERVERS_JSON": "[]",
        })
        assert cfg.mcp.servers == []

    def test_malformed_json_falls_back_to_empty(self):
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_ENABLED": "true",
            "GEMINI_LIVE_MCP_SERVERS_JSON": "NOT_VALID_JSON",
        })
        assert cfg.mcp.servers == []  # graceful fallback, no crash

    def test_partial_json_falls_back_to_empty(self):
        cfg = _fresh_config({
            "GEMINI_LIVE_MCP_SERVERS_JSON": '[{"name": "broken"',  # truncated
        })
        assert cfg.mcp.servers == []

    def test_missing_command_raises(self):
        """MCPServerConfig requires 'command' field."""
        from config import MCPServerConfig
        with pytest.raises(Exception):
            MCPServerConfig(name="x")  # missing command

    def test_mcp_enabled_but_no_json_gives_empty_servers(self):
        cfg = _fresh_config({"GEMINI_LIVE_MCP_ENABLED": "true"})
        # No GEMINI_LIVE_MCP_SERVERS_JSON set → defaults to "[]"
        assert cfg.mcp.servers == []

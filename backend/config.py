"""
Gemini Live Backend Configuration

Isolated configuration for the Gemini Live integration.
Does NOT modify main VoiceKit config.
"""
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from typing import Optional


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server subprocess."""
    name: str = Field(..., description="Logical name, e.g. 'google-workspace' or 'basecamp'")
    command: str = Field(..., description="Executable to launch, e.g. 'npx', 'python', 'node'")
    args: list[str] = Field(default_factory=list, description="Command-line arguments")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to the subprocess (credentials etc.)"
    )
    estimated_seconds: dict[str, int] = Field(
        default_factory=dict,
        description="Per-tool timing overrides for the SJF queue, e.g. {'send_email': 5}"
    )


class MCPConfig(BaseModel):
    """Top-level MCP integration config."""
    enabled: bool = Field(default=False, description="Enable MCP client layer")
    servers: list[MCPServerConfig] = Field(
        default_factory=list,
        description="MCP server definitions to launch on startup"
    )


class GeminiLiveBackendConfig(BaseSettings):
    """Environment-based configuration for Gemini Live backend."""

    # Server settings
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8001, description="Server port (8001 to avoid conflict with LiveKit on 8000)")

    # CORS settings
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins for frontend"
    )

    # VoiceKit integration
    voicekit_root: str = Field(
        default="../..",
        description="Path to VoiceKit root directory (for importing orchestration)"
    )
    voicekit_db_path: str = Field(
        default="../../data/voicekit.db",
        description="Path to shared VoiceKit database"
    )

    # WebSocket settings
    notification_websocket_enabled: bool = Field(
        default=True,
        description="Enable WebSocket notifications (fallback to polling if False)"
    )
    websocket_ping_interval: int = Field(
        default=30,
        description="WebSocket ping interval in seconds"
    )

    # Task polling settings
    poll_interval_seconds: float = Field(
        default=2.0,
        description="Fallback polling interval for task results"
    )

    # Follow-up settings
    followup_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout for follow-up question responses"
    )

    # Tool execution settings
    tool_execution_timeout: float = Field(
        default=60.0,
        description="Timeout for tool execution in seconds"
    )
    max_retries: int = Field(
        default=3,
        description="Max retries for failed tool executions"
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    # ── SaaS: Authentication & multi-tenancy ─────────────────────────────────
    google_client_id: str = Field(
        default="",
        description="Google OAuth2 client ID for ID token verification (SaaS mode)"
    )
    allowed_domain: str = Field(
        default="",
        description="Restrict login to this Google Workspace domain, e.g. 'yourcompany.com'. "
                    "Empty = any Google account allowed."
    )
    backend_public_url: str = Field(
        default="http://localhost:8001",
        description="Publicly reachable URL for this backend (used as OAuth redirect_uri)"
    )
    frontend_url: str = Field(
        default="http://localhost:3000",
        description="Frontend URL for post-OAuth redirects"
    )
    db_url: str = Field(
        default="",
        description="Async SQLAlchemy DB URL (e.g. 'postgresql+asyncpg://...'). "
                    "Empty = use SQLite at voicekit_db_path."
    )

    # MCP integration
    mcp_enabled: bool = Field(
        default=False,
        description="Enable MCP client layer (set GEMINI_LIVE_MCP_ENABLED=true to activate)"
    )
    mcp_servers_json: str = Field(
        default="[]",
        description=(
            "JSON array of MCPServerConfig objects. "
            "Example: [{\"name\":\"google-workspace\",\"command\":\"npx\","
            "\"args\":[\"-y\",\"google-workspace-mcp\"]}]"
        )
    )

    model_config = {
        "env_file": ".env",
        "env_prefix": "GEMINI_LIVE_",
        "extra": "allow"
    }


    @property
    def mcp(self) -> MCPConfig:
        """Parsed MCP configuration derived from env vars."""
        import json
        try:
            servers_data = json.loads(self.mcp_servers_json)
            servers = [MCPServerConfig(**s) for s in servers_data]
        except Exception:
            servers = []
        return MCPConfig(enabled=self.mcp_enabled, servers=servers)


# Singleton config instance
config = GeminiLiveBackendConfig()


class OrchestrationConfig(BaseModel):
    """Configuration for VoiceKit orchestration integration."""

    # Processing Engine settings
    max_processing_steps: int = 10
    processing_timeout: float = 300.0  # 5 minutes

    # Background Task settings
    task_cleanup_interval: int = 3600  # 1 hour
    max_stale_hours: int = 24

    # Notification settings
    notification_policy: str = "polite"  # "immediate", "polite", "next_turn"
    notification_delay: float = 2.0  # seconds to wait before delivering at pause


# Default orchestration config
orchestration_config = OrchestrationConfig()

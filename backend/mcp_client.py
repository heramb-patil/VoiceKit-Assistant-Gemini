"""
MCP Client Manager for Gemini Live Backend

Uses the official Python MCP SDK (stdio_client + ClientSession) to manage
long-lived subprocess connections to MCP servers.

Architecture:
  tool_registry / BackgroundQueue
        │
        ▼
  MCPClientManager.call_tool(name, args)
        │
        ▼
  MCPServerConnection._session.call_tool()   ← official ClientSession
        │
        ▼
  stdio subprocess (Node.js / Python MCP server)
        │
        ▼
  Real API (Gmail, Calendar, Basecamp, ...)
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """A tool discovered from an MCP server."""
    name: str
    description: str
    parameters: dict  # JSON Schema object
    server_name: str  # Which MCP server owns this tool


class MCPServerConnection:
    """
    Manages a single long-lived MCP server subprocess.

    The SDK's stdio_client + ClientSession run inside a background asyncio task
    that holds the context managers open for the application lifetime.
    A `_ready` event signals once `session.initialize()` succeeds.

    Lifecycle:
      start()   → spawns _run() task, waits for _ready
      stop()    → cancels _run() task, subprocess is killed by SDK teardown
      restart() → stop() then start()
    """

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str]):
        self.name = name
        self.command = command
        self.args = args
        self.env = env

        self._session: Optional[ClientSession] = None
        self._ready: asyncio.Event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def start(self, timeout: float = 30.0) -> None:
        """Launch the subprocess and wait for the MCP handshake to complete."""
        self._ready.clear()
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._task.cancel()
            raise RuntimeError(
                f"MCP server '{self.name}' did not initialize within {timeout}s"
            )

    async def stop(self) -> None:
        """Cancel the background task (SDK will clean up the subprocess)."""
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._session = None
        logger.info("MCP server '%s' stopped", self.name)

    async def restart(self) -> None:
        logger.info("Restarting MCP server '%s'", self.name)
        await self.stop()
        await self.start()

    # ------------------------------------------------------------------ #
    # Background runner                                                    #
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        """
        Long-lived task that keeps the MCP subprocess and session alive.

        Holds the stdio_client and ClientSession context managers open until
        the task is cancelled (i.e. until stop() is called).
        """
        merged_env = {**os.environ, **self.env}
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=merged_env,
        )
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    logger.info("MCP server '%s' initialized", self.name)

                    # Block here until the task is cancelled (app shutdown / restart)
                    await asyncio.get_event_loop().create_future()

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "MCP server '%s' crashed: %s", self.name, exc, exc_info=True
            )
        finally:
            self._session = None

    # ------------------------------------------------------------------ #
    # MCP operations (delegates to SDK ClientSession)                     #
    # ------------------------------------------------------------------ #

    @property
    def initialized(self) -> bool:
        return self._session is not None

    async def list_tools(self) -> list[dict]:
        """Return raw tool dicts from the server's tools/list response."""
        if not self._session:
            return []
        result = await self._session.list_tools()
        return [t.model_dump() for t in result.tools]

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """Call a tool and return the result as a string."""
        if not self._session:
            raise RuntimeError(
                f"MCP server '{self.name}' is not connected"
            )
        result = await self._session.call_tool(tool_name, args)

        parts = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            else:
                import json
                parts.append(json.dumps(item.model_dump()))
        return "\n".join(parts) if parts else ""


class MCPClientManager:
    """
    Manages connections to all configured MCP servers.

    Responsibilities:
    - Launch server subprocesses on startup
    - Discover and merge tool schemas from all servers
    - Route tool calls to the correct server
    - Reconnect on crash or token refresh
    """

    def __init__(self) -> None:
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tool_defs: list[ToolDef] = []
        self._estimated_seconds: dict[str, dict] = {}  # server_name → {tool: secs}

    async def connect_all(self, servers, connect_timeout: float = 30.0) -> None:
        """
        Launch all configured MCP servers and build the tool index.

        Args:
            servers: list of MCPServerConfig objects
            connect_timeout: per-server initialization timeout in seconds
        """
        for srv in servers:
            self._estimated_seconds[srv.name] = dict(srv.estimated_seconds)
            conn = MCPServerConnection(
                name=srv.name,
                command=srv.command,
                args=list(srv.args),
                env=dict(srv.env),
            )
            try:
                await conn.start(timeout=connect_timeout)
                self._connections[srv.name] = conn
                logger.info("MCP server connected: %s", srv.name)
            except Exception as exc:
                logger.error(
                    "Failed to start MCP server '%s': %s", srv.name, exc, exc_info=True
                )

        await self._refresh_tool_index()

    async def _refresh_tool_index(self) -> None:
        """Rebuild the tool→server mapping from all live connections."""
        self._tool_to_server.clear()
        self._tool_defs.clear()

        for srv_name, conn in self._connections.items():
            if not conn.initialized:
                continue
            try:
                raw_tools = await conn.list_tools()
                for t in raw_tools:
                    name = t.get("name", "")
                    if not name:
                        continue
                    self._tool_to_server[name] = srv_name
                    # Prefer inputSchema (MCP spec); fall back to parameters
                    schema = t.get("inputSchema") or t.get("parameters") or {
                        "type": "object", "properties": {}, "required": []
                    }
                    self._tool_defs.append(ToolDef(
                        name=name,
                        description=t.get("description", ""),
                        parameters=schema,
                        server_name=srv_name,
                    ))
                logger.info(
                    "MCP server '%s' exposes %d tools", srv_name, len(raw_tools)
                )
            except Exception as exc:
                logger.warning(
                    "Could not list tools from MCP server '%s': %s", srv_name, exc
                )

        logger.info(
            "MCP tool index: %d tools across %d server(s)",
            len(self._tool_defs),
            len(self._connections),
        )

    async def list_all_tools(self) -> list[ToolDef]:
        return list(self._tool_defs)

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """Route a tool call to the owning MCP server."""
        srv_name = self._tool_to_server.get(tool_name)
        if srv_name is None:
            raise ValueError(
                f"Tool '{tool_name}' not found in MCP index. "
                f"Known: {list(self._tool_to_server.keys())[:10]}"
            )

        conn = self._connections.get(srv_name)
        if conn is None or not conn.initialized:
            raise RuntimeError(
                f"MCP server '{srv_name}' for tool '{tool_name}' is not connected"
            )

        try:
            return await conn.call_tool(tool_name, args)
        except (RuntimeError, ConnectionError) as exc:
            # Attempt one restart on connection failures
            logger.warning(
                "MCP server '%s' error during tool call, restarting: %s", srv_name, exc
            )
            await conn.restart()
            await self._refresh_tool_index()
            return await conn.call_tool(tool_name, args)

    def get_estimated_seconds(self, tool_name: str) -> Optional[int]:
        """Return per-tool timing override from MCPServerConfig, if any."""
        srv_name = self._tool_to_server.get(tool_name)
        if srv_name is None:
            return None
        return self._estimated_seconds.get(srv_name, {}).get(tool_name)

    async def restart_server(self, server_name: str) -> None:
        """Restart a specific server and refresh the tool index."""
        conn = self._connections.get(server_name)
        if conn is None:
            logger.warning("restart_server: unknown server '%s'", server_name)
            return
        await conn.restart()
        await self._refresh_tool_index()
        logger.info(
            "MCP server '%s' restarted, tool index refreshed", server_name
        )

    async def shutdown(self) -> None:
        """Stop all server subprocesses."""
        for conn in self._connections.values():
            try:
                await conn.stop()
            except Exception as exc:
                logger.warning("Error stopping MCP server '%s': %s", conn.name, exc)
        self._connections.clear()
        self._tool_to_server.clear()
        self._tool_defs.clear()
        logger.info("MCPClientManager shut down")

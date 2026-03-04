"""
Standalone Orchestration for Gemini Live SaaS Backend

Per-user tool registries: base tools shared by all users;
integration tools (Google, Basecamp) loaded per user from the DB.
"""
import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config import config
from database.models import Base, BackgroundTask, TaskStatus

logger = logging.getLogger(__name__)


class StandaloneOrchestration:
    """Self-contained orchestration using local tools."""

    def __init__(self, db_path: str = None):
        # If config.db_url is set, use it; otherwise fall back to SQLite
        self._db_url: str = config.db_url or f"sqlite+aiosqlite:///{db_path or config.voicekit_db_path}"
        self.engine = None
        self.session_factory = None

        # Base tools shared across all users (no credentials required)
        self.tool_registry: Dict[str, Callable] = {}

        # Per-user integration tools: {email → {tool_name → fn}}
        self._user_tools: Dict[str, Dict[str, Callable]] = {}

        self.mcp_client = None  # MCPClientManager for server-level MCP (shared)

        # Per-user MCP client managers: {email → MCPClientManager}
        self._user_mcp_clients: Dict[str, Any] = {}

    async def initialize(self):
        """Initialize database and load shared base tools."""
        logger.info("Initializing SaaS orchestration (DB: %s)", self._db_url)

        self.engine = create_async_engine(self._db_url, echo=False)
        self.session_factory = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        # Ensure all tables exist (User, UserCredential, BackgroundTask, etc.)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Load shared tools (no credentials needed)
        await self._load_base_tools()
        logger.info("Loaded %d base tools", len(self.tool_registry))

        # Load MCP tools on top
        await self._load_mcp_tools()
        logger.info("Total tools after MCP: %d", len(self.tool_registry))

    # ── Base tools (shared) ───────────────────────────────────────────────────

    async def _load_base_tools(self):
        """Load tools that don't require per-user credentials."""
        try:
            from tools.calculator import calculate
            from tools.get_time import get_current_time
            from tools.file_ops import create_file, read_file, list_files, append_to_file

            self.tool_registry.update({
                "calculate": calculate,
                "get_current_time": get_current_time,
                "create_file": create_file,
                "read_file": read_file,
                "list_files": list_files,
                "append_to_file": append_to_file,
            })

            from skills.web_search import web_search
            from skills.deep_research import deep_research
            self.tool_registry["web_search"] = web_search
            self.tool_registry["deep_research"] = deep_research

        except Exception as e:
            logger.error("Error loading base tools: %s", e, exc_info=True)

    # ── Per-user tools ────────────────────────────────────────────────────────

    def get_user_tool_registry(self, user_email: str) -> Dict[str, Callable]:
        """Return merged tool registry for a user (base + their integration tools)."""
        merged = dict(self.tool_registry)
        merged.update(self._user_tools.get(user_email, {}))
        return merged

    async def ensure_user_tools_loaded(self, user_email: str) -> None:
        """Lazy-load integration tools for a user if not already loaded."""
        if user_email in self._user_tools:
            return
        self._user_tools[user_email] = {}
        await self._load_google_tools_for_user(user_email)
        await self._load_basecamp_tools_for_user(user_email)
        await self._load_user_mcp_tools(user_email)

    async def _load_google_tools_for_user(self, user_email: str) -> None:
        """Load Google tools for *user_email* from DB credentials."""
        try:
            from integrations.google.auth import GoogleAuth
            from integrations.google.gmail import (
                search_emails, get_recent_emails, send_email, get_email_details,
            )
            from integrations.google.calendar import (
                get_todays_events, get_upcoming_events, create_event, check_availability,
            )
            from integrations.google.chat import list_chat_spaces, send_chat_message
            from integrations.google.drive import (
                upload_to_drive, list_drive_files, VOICEKIT_FOLDER, _get_or_create_folder,
            )

            google_auth = await GoogleAuth.from_db(user_email, self.session_factory)
            if not google_auth.is_authenticated():
                logger.debug("No valid Google credentials for %s — skipping Google tools", user_email)
                return

            # Bind per-user auth into module-level _auth vars
            import integrations.google.gmail as gmail_mod
            import integrations.google.calendar as cal_mod
            import integrations.google.chat as chat_mod
            import integrations.google.drive as drive_mod

            gmail_mod._auth = google_auth
            cal_mod._auth = google_auth
            chat_mod._auth = google_auth
            drive_mod._auth = google_auth

            tools = {
                "search_emails": search_emails,
                "get_recent_emails": get_recent_emails,
                "send_email": send_email,
                "get_email_details": get_email_details,
                "get_todays_events": get_todays_events,
                "get_upcoming_events": get_upcoming_events,
                "create_event": create_event,
                "check_availability": check_availability,
                "list_chat_spaces": list_chat_spaces,
                "send_chat_message": send_chat_message,
                "upload_to_drive": upload_to_drive,
                "list_drive_files": list_drive_files,
            }

            # Drive-aware deep_research: captures auth in closure so background
            # task uses the right credentials even if another user authenticates
            # while the research is running.
            _captured_auth = google_auth

            async def _upload_fn_for_user(filename: str, content: str) -> str:
                """Per-user Drive upload using captured auth (safe for background tasks)."""
                try:
                    import io
                    from googleapiclient.http import MediaIoBaseUpload
                    service = _captured_auth.build_service("drive", "v3")
                    folder_id = _get_or_create_folder(service, VOICEKIT_FOLDER)
                    safe_name = filename if filename.endswith(".md") else f"{filename}.md"
                    file_meta = {"name": safe_name, "parents": [folder_id]}
                    media = MediaIoBaseUpload(
                        io.BytesIO(content.encode("utf-8")),
                        mimetype="text/markdown",
                        resumable=False,
                    )
                    file = service.files().create(
                        body=file_meta, media_body=media, fields="id,webViewLink"
                    ).execute()
                    url = file.get("webViewLink", "")
                    return f"Saved to Google Drive: {safe_name}\nLink: {url}"
                except Exception as exc:
                    logger.warning("Drive upload for %s failed: %s", user_email, exc)
                    return f"Drive save failed: {exc}"

            from skills.deep_research import deep_research as _base_deep_research

            async def _deep_research_with_drive(topic: str, depth: int = 3) -> str:
                """deep_research with automatic Drive upload when Google is connected."""
                return await _base_deep_research(
                    topic=topic, depth=depth, _drive_save_fn=_upload_fn_for_user
                )
            _deep_research_with_drive.__name__ = "deep_research"

            tools["deep_research"] = _deep_research_with_drive

            self._user_tools.setdefault(user_email, {}).update(tools)
            logger.info("Google tools loaded for user %s (%d tools)", user_email, len(tools))

        except ImportError as e:
            logger.warning("Google integrations not available: %s", e)
        except Exception as e:
            logger.warning("Failed to load Google tools for %s: %s", user_email, e)

    async def _load_basecamp_tools_for_user(self, user_email: str) -> None:
        """Load Basecamp tools for *user_email* from DB credentials."""
        try:
            client_id = os.environ.get("BASECAMP_CLIENT_ID", "")
            client_secret = os.environ.get("BASECAMP_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                return

            from integrations.basecamp.auth import BasecampAuth
            from integrations.basecamp.tools import _init_basecamp_tools

            basecamp_auth = await BasecampAuth.from_db(user_email, self.session_factory)
            token = (basecamp_auth._token_data or {}).get("access_token")
            if not token:
                logger.debug("No Basecamp token for %s — skipping", user_email)
                return

            tools = _init_basecamp_tools(basecamp_auth)
            self._user_tools.setdefault(user_email, {}).update(tools)
            logger.info("Basecamp tools loaded for user %s (%d tools)", user_email, len(tools))

        except Exception as e:
            logger.warning("Failed to load Basecamp tools for %s: %s", user_email, e)

    # ── Per-user MCP tools ────────────────────────────────────────────────────

    @dataclass
    class _UserMCPServerConfig:
        name: str
        command: str
        args: list
        env: dict
        estimated_seconds: dict = field(default_factory=dict)

    async def _load_user_mcp_tools(self, user_email: str) -> None:
        """Load MCP tools from the user's configured MCP servers (DB)."""
        try:
            from sqlalchemy import select
            from database.models import UserMCPServer

            async with self.session_factory() as session:
                result = await session.execute(
                    select(UserMCPServer)
                    .where(UserMCPServer.user_email == user_email)
                    .where(UserMCPServer.enabled == True)  # noqa: E712
                )
                db_servers = result.scalars().all()

            if not db_servers:
                return

            from mcp_client import MCPClientManager
            mgr = MCPClientManager()
            configs = [
                StandaloneOrchestration._UserMCPServerConfig(
                    name=srv.name,
                    command=srv.command,
                    args=json.loads(srv.args_json or "[]"),
                    env=json.loads(srv.env_json or "{}"),
                )
                for srv in db_servers
            ]
            await mgr.connect_all(configs, connect_timeout=15.0)

            tools = await mgr.list_all_tools()
            for tool in tools:
                self._user_tools[user_email][tool.name] = self._wrap_user_mcp_tool(mgr, tool.name)

            # Stop previous manager for this user if any
            old_mgr = self._user_mcp_clients.get(user_email)
            if old_mgr is not None:
                try:
                    await old_mgr.shutdown()
                except Exception:
                    pass
            self._user_mcp_clients[user_email] = mgr
            logger.info(
                "User MCP tools loaded for %s: %d tools from %d server(s)",
                user_email, len(tools), len(db_servers),
            )

        except ImportError:
            logger.debug("MCP SDK not installed — skipping user MCP tools for %s", user_email)
        except Exception as exc:
            logger.warning("Failed to load user MCP tools for %s: %s", user_email, exc)

    def _wrap_user_mcp_tool(self, mgr: Any, tool_name: str) -> Callable:
        """Return an async callable that delegates execution to the user's MCP manager."""
        async def _call(**kwargs: Any) -> str:
            return await mgr.call_tool(tool_name, kwargs)
        _call.__name__ = tool_name
        _call.__mcp_tool__ = True
        return _call

    async def reload_user_mcp_tools(self, user_email: str) -> None:
        """Reload user MCP tools after they add/remove a server."""
        # Clear existing user MCP tools (but keep integration tools)
        user_tools = self._user_tools.get(user_email, {})
        # Remove previously loaded MCP tool entries
        old_mgr = self._user_mcp_clients.get(user_email)
        if old_mgr is not None:
            old_tool_names = set(old_mgr._tool_to_server.keys())
            for name in old_tool_names:
                user_tools.pop(name, None)
            try:
                await old_mgr.shutdown()
            except Exception:
                pass
            self._user_mcp_clients.pop(user_email, None)
        # Load fresh
        await self._load_user_mcp_tools(user_email)

    # ── Reload helpers (called after OAuth callbacks) ─────────────────────────

    async def reload_google_tools_for_user(self, user_email: str) -> None:
        """Reload Google tools for a user after they (re-)authenticated."""
        self._user_tools.setdefault(user_email, {})
        await self._load_google_tools_for_user(user_email)
        logger.info(
            "Google tools reloaded for %s (%d user tools, %d total)",
            user_email,
            len(self._user_tools.get(user_email, {})),
            len(self.get_user_tool_registry(user_email)),
        )
        if self.mcp_client:
            for srv_name in list(self.mcp_client._connections.keys()):
                if "google" in srv_name.lower():
                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(self.mcp_client.restart_server(srv_name))
                    except RuntimeError:
                        pass

    async def reload_basecamp_tools_for_user(self, user_email: str) -> None:
        """Reload Basecamp tools for a user after they (re-)authenticated."""
        self._user_tools.setdefault(user_email, {})
        await self._load_basecamp_tools_for_user(user_email)
        logger.info(
            "Basecamp tools reloaded for %s (%d user tools, %d total)",
            user_email,
            len(self._user_tools.get(user_email, {})),
            len(self.get_user_tool_registry(user_email)),
        )
        if self.mcp_client:
            for srv_name in list(self.mcp_client._connections.keys()):
                if "basecamp" in srv_name.lower():
                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(self.mcp_client.restart_server(srv_name))
                    except RuntimeError:
                        pass

    # ── Tool execution ────────────────────────────────────────────────────────

    async def execute_tool(
        self,
        user_identity: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        run_in_background: bool = False,
    ) -> Dict[str, Any]:
        """Execute a tool for *user_identity*.

        Looks up tools in: user's integration tools → shared base tools.
        Lazily loads user integration tools on first call.
        """
        try:
            # Lazy-load user-specific integration tools
            await self.ensure_user_tools_loaded(user_identity)

            registry = self.get_user_tool_registry(user_identity)
            if tool_name not in registry:
                available = list(registry.keys())[:5]
                return {
                    "success": False,
                    "result": "",
                    "error": (
                        f"Tool '{tool_name}' not found. "
                        f"Available: {available}"
                    ),
                }

            tool_fn = registry[tool_name]

            BACKGROUND_TOOLS = {"deep_research"}
            should_background = run_in_background or tool_name in BACKGROUND_TOOLS

            if should_background:
                task_id = str(uuid.uuid4())
                async with self.session_factory() as session:
                    task = BackgroundTask(
                        id=task_id,
                        user_identity=user_identity,
                        tool_name=tool_name,
                        status=TaskStatus.running,
                        delivered=False,
                    )
                    session.add(task)
                    await session.commit()

                asyncio.create_task(self._execute_background_task(task_id, tool_fn, tool_args))
                return {
                    "success": True,
                    "result": "Started background task. You'll be notified when complete.",
                    "error": None,
                    "background": True,
                    "task_id": task_id,
                }

            try:
                result = await asyncio.wait_for(
                    tool_fn(**tool_args),
                    timeout=config.tool_execution_timeout,
                )
                return {"success": True, "result": str(result), "error": None}
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "result": "",
                    "error": f"Tool timed out after {config.tool_execution_timeout}s",
                }

        except Exception as e:
            logger.error("Error executing tool %s for %s: %s", tool_name, user_identity, e, exc_info=True)
            return {"success": False, "result": "", "error": str(e)}

    async def _execute_background_task(
        self,
        task_id: str,
        tool_fn: Callable,
        tool_args: Dict[str, Any],
    ):
        """Execute a tool in the background and update the DB task record."""
        from sqlalchemy import update as sa_update

        try:
            logger.info("Executing background task %s", task_id)
            result = await tool_fn(**tool_args)

            async with self.session_factory() as session:
                await session.execute(
                    sa_update(BackgroundTask)
                    .where(BackgroundTask.id == task_id)
                    .values(status=TaskStatus.completed, result=str(result))
                )
                await session.commit()
            logger.info("Background task %s completed", task_id)

        except Exception as e:
            logger.error("Background task %s failed: %s", task_id, e, exc_info=True)
            try:
                async with self.session_factory() as session:
                    await session.execute(
                        sa_update(BackgroundTask)
                        .where(BackgroundTask.id == task_id)
                        .values(status=TaskStatus.completed, result=f"Error: {e}")
                    )
                    await session.commit()
            except Exception:
                pass

    # ── Task helpers ──────────────────────────────────────────────────────────

    async def get_pending_tasks(
        self,
        user_identity: str,
        delivered: bool = False,
    ) -> list[Dict[str, Any]]:
        """Get completed (not yet delivered) task results for a user."""
        try:
            from sqlalchemy import select
            async with self.session_factory() as session:
                query = select(BackgroundTask).where(
                    BackgroundTask.user_identity == user_identity
                )
                if not delivered:
                    query = query.where(BackgroundTask.delivered == False)  # noqa: E712
                result = await session.execute(query)
                tasks = result.scalars().all()
                return [
                    {
                        "task_id": t.id,
                        "status": t.status.value,
                        "result": t.result or "",
                        "tool_name": t.tool_name,
                        "created_at": t.created_at.isoformat() if t.created_at else None,
                        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    }
                    for t in tasks
                    if t.status == TaskStatus.completed
                ]
        except Exception as e:
            logger.error("Error getting pending tasks: %s", e, exc_info=True)
            return []

    async def mark_task_delivered(self, task_id: str):
        """Mark task as delivered."""
        try:
            from sqlalchemy import update as sa_update
            async with self.session_factory() as session:
                await session.execute(
                    sa_update(BackgroundTask)
                    .where(BackgroundTask.id == task_id)
                    .values(delivered=True)
                )
                await session.commit()
        except Exception as e:
            logger.error("Error marking task %s as delivered: %s", task_id, e, exc_info=True)

    # ── MCP ───────────────────────────────────────────────────────────────────

    async def _load_mcp_tools(self) -> None:
        """Optionally load tools from MCP servers."""
        mcp_cfg = config.mcp
        if not mcp_cfg.enabled or not mcp_cfg.servers:
            return

        try:
            from mcp_client import MCPClientManager
            self.mcp_client = MCPClientManager()
            await self.mcp_client.connect_all(mcp_cfg.servers)

            tools = await self.mcp_client.list_all_tools()
            for tool in tools:
                self.tool_registry[tool.name] = self._wrap_mcp_tool(tool.name)
                logger.debug("MCP tool registered: %s (from %s)", tool.name, tool.server_name)

            logger.info(
                "MCP layer active: %d tools from %d server(s)",
                len(tools),
                len(mcp_cfg.servers),
            )
        except ImportError:
            logger.warning("MCP SDK not installed — skipping MCP layer")
        except Exception as exc:
            logger.error("Failed to initialize MCP layer: %s", exc, exc_info=True)

    def _wrap_mcp_tool(self, tool_name: str) -> Callable:
        """Return an async callable that delegates execution to the MCP client."""
        async def _call(**kwargs: Any) -> str:
            return await self.mcp_client.call_tool(tool_name, kwargs)
        _call.__name__ = tool_name
        _call.__mcp_tool__ = True
        return _call

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def shutdown(self):
        """Cleanup resources."""
        logger.info("Shutting down orchestration")
        if self.mcp_client:
            await self.mcp_client.shutdown()
        for email, mgr in list(self._user_mcp_clients.items()):
            try:
                await mgr.shutdown()
            except Exception as exc:
                logger.warning("Error shutting down user MCP client for %s: %s", email, exc)
        self._user_mcp_clients.clear()
        if self.engine:
            await self.engine.dispose()


# ── Global singleton ──────────────────────────────────────────────────────────

_orchestration: Optional[StandaloneOrchestration] = None


async def get_orchestration() -> StandaloneOrchestration:
    """Get or create the orchestration singleton."""
    global _orchestration
    if _orchestration is None:
        _orchestration = StandaloneOrchestration()
        await _orchestration.initialize()
    return _orchestration

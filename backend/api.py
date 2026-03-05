"""
Gemini Live Backend API

HTTP/WebSocket bridge between frontend and VoiceKit orchestration.
Provides endpoints:
1. POST /tool-execute - Execute tool directly
2. POST /task-delegate - Delegate to ProcessingEngine
3. GET /tasks - Poll pending results
4. POST /followup-response - Answer follow-up question
5. WebSocket /notifications - Real-time push notifications
6. POST /tool-submit - Submit tool to SJF background queue
7. GET /tasks/stream - SSE stream for task completion events
8. GET /tasks/{task_id} - Poll single background task status
"""
import asyncio
import heapq
import json
import logging
import os
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from auth import CurrentUser
from orchestration import get_orchestration  # Using standalone orchestration
from websocket import ws_manager, followup_channel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gemini-live", tags=["gemini-live"])


# ============================================================================
# Auth Flow Manager — Per-user OAuth status tracking for Google + Basecamp
# ============================================================================

@dataclass
class _IntegrationStatus:
    status: str = "disconnected"  # disconnected | pending | connected | error
    label: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AuthFlowManager:
    google: _IntegrationStatus = field(default_factory=_IntegrationStatus)
    basecamp: _IntegrationStatus = field(default_factory=_IntegrationStatus)


# Per-user auth state dict: email → AuthFlowManager
_auth_state: Dict[str, AuthFlowManager] = {}

# Pending OAuth state tokens: state_token → {email, created_at}
# Short-lived (120s TTL); used to verify /callback requests aren't CSRF
_pending_states: Dict[str, Dict] = {}


def _get_auth_mgr(email: str) -> AuthFlowManager:
    """Return (or create) the AuthFlowManager for a user."""
    if email not in _auth_state:
        _auth_state[email] = AuthFlowManager()
    return _auth_state[email]


_STATE_TOKEN_TTL = 600  # 10 minutes — enough time to complete OAuth in another tab


def _new_state_token(user_email: str) -> str:
    """Generate a fresh CSRF state token, associated with user_email (TTL 10 min)."""
    import secrets
    token = secrets.token_urlsafe(32)
    _pending_states[token] = {"email": user_email, "created_at": time.time()}
    # Prune expired tokens
    cutoff = time.time() - _STATE_TOKEN_TTL
    expired = [k for k, v in _pending_states.items() if v["created_at"] < cutoff]
    for k in expired:
        _pending_states.pop(k, None)
    return token


def _pop_state_token(state: str) -> Optional[str]:
    """Validate + consume a state token. Returns user_email or None if invalid/expired."""
    entry = _pending_states.pop(state, None)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > _STATE_TOKEN_TTL:
        return None
    return entry["email"]


async def _upsert_user(email: str, name: str = "", picture: str = "") -> None:
    """Ensure a User row exists in the DB; update last_seen every call."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from database.models import User

    orch = await get_orchestration()
    async with orch.session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if user is None:
            user = User(email=email, name=name, picture=picture, created_at=now, last_seen=now)
            session.add(user)
        else:
            user.last_seen = now
            if name:
                user.name = name
            if picture:
                user.picture = picture
        await session.commit()


# ============================================================================
# Tool Metadata — estimated execution time + background classification
# ============================================================================

TOOL_METADATA: Dict[str, Dict] = {
    "get_todays_events":      {"estimated_seconds": 2,  "is_background": False},
    "get_upcoming_events":    {"estimated_seconds": 3,  "is_background": False},
    "create_event":           {"estimated_seconds": 3,  "is_background": False},
    "check_availability":     {"estimated_seconds": 2,  "is_background": False},
    "web_search":             {"estimated_seconds": 5,  "is_background": False},
    "get_recent_emails":      {"estimated_seconds": 6,  "is_background": True},
    "search_emails":          {"estimated_seconds": 8,  "is_background": True},
    "get_email_details":      {"estimated_seconds": 4,  "is_background": True},
    "send_email":             {"estimated_seconds": 5,  "is_background": True},
    "reply_email":            {"estimated_seconds": 5,  "is_background": True},
    "list_chat_spaces":       {"estimated_seconds": 3,  "is_background": False},
    "get_chat_messages":      {"estimated_seconds": 4,  "is_background": True},
    "send_chat_message":      {"estimated_seconds": 4,  "is_background": True},
    "list_basecamp_projects": {"estimated_seconds": 4,  "is_background": False},
    "get_basecamp_todos":     {"estimated_seconds": 5,  "is_background": False},
    "create_basecamp_todo":   {"estimated_seconds": 5,  "is_background": True},
    "get_basecamp_messages":  {"estimated_seconds": 5,  "is_background": True},
    "post_basecamp_message":  {"estimated_seconds": 5,  "is_background": True},
    "update_basecamp_todo":   {"estimated_seconds": 4,  "is_background": True},
    "post_basecamp_comment":  {"estimated_seconds": 4,  "is_background": True},
    "get_basecamp_checkins":  {"estimated_seconds": 8,  "is_background": False},
    "answer_basecamp_checkin":{"estimated_seconds": 4,  "is_background": False},
    "deep_research":          {"estimated_seconds": 60, "is_background": True},
    "upload_to_drive":        {"estimated_seconds": 5,  "is_background": False},
    "list_drive_files":       {"estimated_seconds": 3,  "is_background": False},
}


# ============================================================================
# Background Queue — SJF (Shortest Job First) min-heap scheduler
# ============================================================================

@dataclass(order=True)
class _QueueEntry:
    """Heap entry sorted by estimated execution time (SJF), then FIFO."""
    estimated_seconds: float        # primary sort key — smaller = higher priority
    created_at: float               # secondary sort key — FIFO within same estimate
    task_id: str = field(compare=False)
    tool_name: str = field(compare=False)
    tool_args: dict = field(compare=False)
    user_identity: str = field(compare=False)
    session_id: str = field(compare=False)


class BackgroundQueue:
    """
    SJF background task queue with SSE delivery.

    - Tasks are ordered by estimated_seconds (shortest first)
    - MAX_CONCURRENT tasks run at once (semaphore-limited)
    - Results are pushed to per-session SSE queues when complete
    """
    MAX_CONCURRENT = 3

    def __init__(self):
        self._heap: list[_QueueEntry] = []
        self._heap_lock: asyncio.Lock = None   # created lazily (needs running loop)
        self._notify: asyncio.Event = None
        self._tasks: Dict[str, dict] = {}
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._semaphore: asyncio.Semaphore = None
        self._orchestration = None

    async def start(self, orchestration) -> None:
        """Initialize queue and start worker. Call once from lifespan."""
        self._heap_lock = asyncio.Lock()
        self._notify = asyncio.Event()
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._orchestration = orchestration
        asyncio.create_task(self._worker())
        logger.info("BackgroundQueue started (SJF, max_concurrent=%d)", self.MAX_CONCURRENT)

    async def submit(
        self,
        task_id: str,
        tool_name: str,
        tool_args: dict,
        user_identity: str,
        session_id: str,
        estimated_seconds: Optional[int] = None,
    ) -> None:
        """Add a tool call to the SJF queue."""
        estimated = estimated_seconds if estimated_seconds is not None \
            else TOOL_METADATA.get(tool_name, {}).get("estimated_seconds", 10)
        entry = _QueueEntry(
            estimated_seconds=float(estimated),
            created_at=time.time(),
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            user_identity=user_identity,
            session_id=session_id,
        )
        async with self._heap_lock:
            heapq.heappush(self._heap, entry)

        self._tasks[task_id] = {
            "task_id": task_id,
            "tool_name": tool_name,
            "status": "pending",
            "estimated_seconds": estimated,
            "session_id": session_id,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "result": None,
        }
        self._notify.set()
        logger.info("Queued task %s (%s, ~%ds)", task_id, tool_name, estimated)

    async def _worker(self) -> None:
        """Worker loop: drain heap, spawn tasks, wait for more."""
        while True:
            # Drain all pending entries from the heap
            while True:
                entry = None
                async with self._heap_lock:
                    if self._heap:
                        entry = heapq.heappop(self._heap)
                if entry is None:
                    break
                asyncio.create_task(self._run_task(entry))

            # Clear the event, then re-check for race-condition items
            self._notify.clear()
            async with self._heap_lock:
                if self._heap:
                    continue  # items arrived between drain and clear — loop again

            await self._notify.wait()

    async def _run_task(self, entry: _QueueEntry) -> None:
        """Execute one task, respecting the concurrency semaphore."""
        async with self._semaphore:
            task_id = entry.task_id
            tool_name = entry.tool_name

            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "running"
                self._tasks[task_id]["started_at"] = time.time()

            logger.info("Running background task %s (%s)", task_id, tool_name)

            # Push running status to SSE subscriber so TaskPanel shows spinner
            q = self._subscribers.get(entry.session_id)
            if q is not None:
                await q.put({
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "status": "running",
                    "result": None,
                    "estimated_seconds": entry.estimated_seconds,
                })

            try:
                # Look up tool in user's registry (base tools + their integration tools)
                await self._orchestration.ensure_user_tools_loaded(entry.user_identity)
                user_registry = self._orchestration.get_user_tool_registry(entry.user_identity)
                tool_fn = user_registry.get(tool_name)
                if not tool_fn:
                    raise ValueError(f"Tool '{tool_name}' not found in registry")

                result = await asyncio.wait_for(
                    tool_fn(**entry.tool_args),
                    timeout=120.0,
                )
                status = "done"
                result_str = str(result)
            except asyncio.TimeoutError:
                status = "failed"
                result_str = f"Tool '{tool_name}' timed out after 120s"
                logger.warning("Task %s timed out", task_id)
            except Exception as exc:
                status = "failed"
                result_str = str(exc)
                logger.error("Task %s failed: %s", task_id, exc, exc_info=True)

            # Update in-memory state
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = status
                self._tasks[task_id]["result"] = result_str
                self._tasks[task_id]["completed_at"] = time.time()

            # Push to the SSE subscriber for this session
            q = self._subscribers.get(entry.session_id)
            if q is not None:
                event = {
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "status": status,
                    "result": result_str,
                    "estimated_seconds": entry.estimated_seconds,
                }
                await q.put(event)
                logger.info("Pushed SSE event for task %s → session %s", task_id, entry.session_id)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """Register an SSE subscriber for a session. Returns the delivery queue."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[session_id] = q
        return q

    def unsubscribe(self, session_id: str) -> None:
        self._subscribers.pop(session_id, None)

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list:
        return list(self._tasks.values())


# Module-level singleton — started in main.py lifespan
bg_queue = BackgroundQueue()


# ============================================================================
# Request/Response Models
# ============================================================================

class ToolExecuteRequest(BaseModel):
    """Request to execute a tool."""
    tool_name: str = Field(..., description="Tool name from registry")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class ToolExecuteResponse(BaseModel):
    """Response from tool execution."""
    success: bool
    result: str
    error: Optional[str] = None


class TaskDelegateRequest(BaseModel):
    """Request to delegate task to ProcessingEngine."""
    task_description: str = Field(..., description="Task description for ProcessingEngine")
    tool_names: Optional[list[str]] = Field(
        default=None,
        description="Optional list of tool names to use (defaults to all)"
    )


class TaskDelegateResponse(BaseModel):
    """Response from task delegation."""
    task_id: str
    status: str = "started"


class TaskResult(BaseModel):
    """Task result."""
    task_id: str
    status: str
    result: str
    tool_name: str
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskListResponse(BaseModel):
    """Response from task listing."""
    pending_results: list[TaskResult]


class FollowUpResponseRequest(BaseModel):
    """Request to answer follow-up question."""
    response_text: str = Field(..., description="User's answer to follow-up question")


class FollowUpResponseResponse(BaseModel):
    """Response from follow-up answer."""
    success: bool
    message: str = ""


class ToolSubmitRequest(BaseModel):
    """Request to submit a tool call to the SJF background queue."""
    tool_name: str = Field(..., description="Tool name to execute in background")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    session_id: str = Field(..., description="Session ID for SSE delivery")


class ToolSubmitResponse(BaseModel):
    """Response from background tool submission."""
    task_id: str
    estimated_seconds: int
    status: str = "queued"


class BgTaskStatus(BaseModel):
    """Status of a single background queue task."""
    task_id: str
    tool_name: str
    status: str   # pending | running | done | failed
    estimated_seconds: int
    result: Optional[str] = None
    created_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/tool-execute", response_model=ToolExecuteResponse)
async def execute_tool(
    request: ToolExecuteRequest,
    current_user: CurrentUser,
) -> ToolExecuteResponse:
    """
    Execute a tool via VoiceKit's tool registry.

    Simple tools (get_time, web_search, etc.) execute directly and return results.
    Frontend can then send result back to Gemini Live via sendToolResponse().
    """
    import time as _time
    t0 = _time.monotonic()
    try:
        user_identity = current_user["email"]
        orchestration = await get_orchestration()
        result = await orchestration.execute_tool(
            user_identity=user_identity,
            tool_name=request.tool_name,
            tool_args=request.tool_args
        )
        logger.info("[TIMING] tool-execute %s end-to-end=%.0fms", request.tool_name, (_time.monotonic() - t0) * 1000)
        return ToolExecuteResponse(**result)
    except Exception as e:
        logger.error(f"Error in tool-execute endpoint: {e}", exc_info=True)
        return ToolExecuteResponse(
            success=False,
            result="",
            error=str(e)
        )


@router.post("/task-delegate", response_model=TaskDelegateResponse)
async def delegate_task(
    request: TaskDelegateRequest,
    current_user: CurrentUser,
) -> TaskDelegateResponse:
    """
    Delegate complex task to VoiceKit's ProcessingEngine.

    Task runs in background via BackgroundTaskManager.
    Frontend receives result via WebSocket notification or polling.
    """
    try:
        user_identity = current_user["email"]
        orchestration = await get_orchestration()
        result = await orchestration.delegate_task(
            user_identity=user_identity,
            task_description=request.task_description,
            tool_names=request.tool_names
        )
        return TaskDelegateResponse(**result)
    except Exception as e:
        logger.error(f"Error in task-delegate endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks", response_model=TaskListResponse)
async def get_tasks(
    current_user: CurrentUser,
    delivered: bool = Query(default=False, description="Include delivered tasks"),
) -> TaskListResponse:
    """
    Poll for pending task results.

    Fallback if WebSocket notifications unavailable.
    Frontend should poll every 2 seconds for completed tasks.
    """
    try:
        user_identity = current_user["email"]
        orchestration = await get_orchestration()
        tasks = await orchestration.get_pending_tasks(
            user_identity=user_identity,
            delivered=delivered
        )
        return TaskListResponse(
            pending_results=[TaskResult(**task) for task in tasks]
        )
    except Exception as e:
        logger.error(f"Error in get-tasks endpoint: {e}", exc_info=True)
        return TaskListResponse(pending_results=[])


@router.post("/followup-response", response_model=FollowUpResponseResponse)
async def followup_response(
    request: FollowUpResponseRequest,
    current_user: CurrentUser,
) -> FollowUpResponseResponse:
    """
    Answer a follow-up question from ProcessingEngine.

    When ProcessingEngine asks a question (e.g., "Who should I send this to?"),
    it waits for user response. Frontend sends the answer here.
    """
    try:
        user_identity = current_user["email"]
        resolved = followup_channel.resolve(
            user_identity=user_identity,
            response_text=request.response_text
        )

        if resolved:
            return FollowUpResponseResponse(
                success=True,
                message="Follow-up question answered"
            )
        else:
            return FollowUpResponseResponse(
                success=False,
                message="No pending follow-up question found"
            )
    except Exception as e:
        logger.error(f"Error in followup-response endpoint: {e}", exc_info=True)
        return FollowUpResponseResponse(
            success=False,
            message=str(e)
        )


@router.websocket("/notifications")
async def websocket_notifications(
    websocket: WebSocket,
    token: str = Query(..., description="Bearer ID token for authentication"),
):
    """
    WebSocket endpoint for real-time notifications.

    Frontend connects with: ws://localhost:8001/gemini-live/notifications?token=<id_token>

    Receives messages:
    - { "type": "task_complete", "task_id": "...", "result": "..." }
    - { "type": "followup_question", "question": "..." }
    - { "type": "error", "error": "..." }
    """
    # Validate the token before accepting the WebSocket
    from config import config as _config
    try:
        from google.oauth2 import id_token as _id_token
        from google.auth.transport import requests as _google_requests
        claims = _id_token.verify_oauth2_token(
            token, _google_requests.Request(), _config.google_client_id
        )
        user_identity: str = claims["email"]
        if _config.allowed_domain and claims.get("hd") != _config.allowed_domain:
            await websocket.close(code=4003)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    await ws_manager.connect(user_identity, websocket)

    try:
        # Keep connection alive and handle incoming pings
        while True:
            try:
                # Wait for ping from client (optional)
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning(f"WebSocket error for {user_identity}: {e}")
                break
    finally:
        await ws_manager.disconnect(user_identity, websocket)


@router.post("/tool-submit", response_model=ToolSubmitResponse)
async def submit_tool(
    request: ToolSubmitRequest,
    current_user: CurrentUser,
) -> ToolSubmitResponse:
    """
    Submit a tool call to the SJF background queue.

    Returns a task_id immediately — the tool executes in the background.
    Results are pushed via GET /tasks/stream?session_id=<session_id>.
    """
    if not bg_queue._semaphore:
        raise HTTPException(status_code=503, detail="Background queue not started")

    user_identity = current_user["email"]
    task_id = str(uuid.uuid4())

    # Prefer MCP-server timing override, then TOOL_METADATA, then default
    orch = await get_orchestration()
    mcp_secs = (
        orch.mcp_client.get_estimated_seconds(request.tool_name)
        if orch.mcp_client else None
    )
    estimated = mcp_secs if mcp_secs is not None \
        else TOOL_METADATA.get(request.tool_name, {}).get("estimated_seconds", 10)

    await bg_queue.submit(
        task_id=task_id,
        tool_name=request.tool_name,
        tool_args=request.tool_args,
        user_identity=user_identity,
        session_id=request.session_id,
        estimated_seconds=estimated,
    )

    return ToolSubmitResponse(task_id=task_id, estimated_seconds=estimated)


@router.get("/tasks/stream")
async def task_stream(
    request: Request,
    session_id: str = Query(..., description="Session ID matching the one used in tool-submit"),
):
    """
    SSE stream of background task completion events for a session.

    Each event is a JSON object:
      { task_id, tool_name, status, result, estimated_seconds }

    Keep-alive comments are sent every 25s to prevent proxy timeouts.
    """
    if not bg_queue._semaphore:
        raise HTTPException(status_code=503, detail="Background queue not started")

    async def event_generator() -> AsyncGenerator[str, None]:
        q = await bg_queue.subscribe(session_id)
        logger.info("SSE stream opened for session %s", session_id)
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            bg_queue.unsubscribe(session_id)
            logger.info("SSE stream closed for session %s", session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/tasks/{task_id}", response_model=BgTaskStatus)
async def get_bg_task(task_id: str) -> BgTaskStatus:
    """Poll the status of a single background queue task."""
    task = bg_queue.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return BgTaskStatus(**task)


@router.get("/health")
async def health_check(current_user: CurrentUser):
    """Health check endpoint. Also upserts the user record (called once per session)."""
    orchestration = await get_orchestration()
    user_email = current_user["email"]
    # Upsert user here (once per session) instead of on every tool call
    await _upsert_user(user_email, current_user.get("name", ""), current_user.get("picture", ""))
    user_tools = orchestration.get_user_tool_registry(user_email)
    return {
        "status": "healthy",
        "tool_count": len(user_tools),
        "websocket_connections": ws_manager.get_connection_count(),
        "user": user_email,
    }


def _build_tool_metadata(orch) -> Dict[str, Dict]:
    """
    Build TOOL_METADATA merged with any per-tool overrides from MCP server configs.

    MCP server config can specify estimated_seconds per tool; those override the
    hardcoded TOOL_METADATA values so SJF scheduling stays accurate.
    """
    merged = dict(TOOL_METADATA)
    if orch.mcp_client is not None:
        for tool_name in orch.mcp_client._tool_to_server:
            override_secs = orch.mcp_client.get_estimated_seconds(tool_name)
            if override_secs is not None:
                if tool_name not in merged:
                    merged[tool_name] = {"estimated_seconds": override_secs, "is_background": override_secs >= 5}
                else:
                    merged[tool_name] = {**merged[tool_name], "estimated_seconds": override_secs}
    return merged


@router.get("/tools")
async def get_tools(current_user: CurrentUser):
    """
    Get tool declarations in Gemini Live format.

    When MCP is enabled and at least one server is connected, tool schemas are
    discovered dynamically from those servers and merged with the hardcoded
    local tools below.  The hardcoded block is always present as a fallback for
    basic/local tools (calculate, get_current_time, file_ops, web_search, etc.).
    """
    orch = await get_orchestration()

    # Build the dynamic MCP tool list (empty if MCP not enabled)
    mcp_tools: list[dict] = []
    if orch.mcp_client is not None:
        for tool_def in await orch.mcp_client.list_all_tools():
            mcp_tools.append({
                "name": tool_def.name,
                "description": tool_def.description,
                "parameters": tool_def.parameters,
            })

    # ── Hardcoded local tools (always present) ────────────────────────────────
    # These are kept verbatim: reflection-based generation produces wrong types
    # (int→string) and useless descriptions, causing Gemini to form bad calls.
    # MCP tools discovered above are appended after this block.
    local_tools = [
        # ── Basic utilities ──────────────────────────────────────────────────
        {
            "name": "calculate",
            "description": "Evaluate a mathematical expression and return the numeric result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A valid mathematical expression, e.g. '2 + 2', '15 * 27', 'sqrt(144)'"
                    }
                },
                "required": ["expression"]
            }
        },
        {
            "name": "get_current_time",
            "description": "Get the current date and time for a city. Use 'local' if no city is specified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name, e.g. 'New York', 'London', 'Tokyo'. Use 'local' if the user didn't specify one."
                    }
                },
                "required": ["city"]
            }
        },

        # ── File operations ──────────────────────────────────────────────────
        {
            "name": "create_file",
            "description": "Create or overwrite a file in the workspace with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path in the workspace, e.g. 'notes.txt' or 'reports/summary.md'"
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write into the file"
                    }
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "read_file",
            "description": "Read and return the full contents of a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path to read, e.g. 'notes.txt' or 'reports/summary.md'"
                    }
                },
                "required": ["path"]
            }
        },
        {
            "name": "list_files",
            "description": "List all files and folders in the workspace or a sub-directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Sub-directory to list. Use '.' for the workspace root (default)."
                    }
                },
                "required": []
            }
        },
        {
            "name": "append_to_file",
            "description": "Append text to an existing file, or create it if it doesn't exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path to append to, e.g. 'log.txt'"
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to append at the end of the file"
                    }
                },
                "required": ["path", "content"]
            }
        },

        # ── Web / research ───────────────────────────────────────────────────
        {
            "name": "web_search",
            "description": "Search the web for current information and return a concise summary. Use for quick facts, news, or simple questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'latest iPhone release date' or 'weather in San Francisco'"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "deep_research",
            "description": "Conduct comprehensive multi-angle research on a topic using several parallel searches. Runs as a BACKGROUND task — takes ~30 seconds. Results are saved to a file and shown in the notification panel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Research topic, e.g. 'impact of AI on healthcare' or 'latest trends in renewable energy'"
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Number of parallel search angles to use. Between 2 and 5. Default is 3."
                    }
                },
                "required": ["topic"]
            }
        },

        # ── Google Drive ─────────────────────────────────────────────────────
        {
            "name": "upload_to_drive",
            "description": "Upload a text or markdown file to Google Drive (VoiceKit Research folder). Use this to save reports, notes, or research results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name for the file, e.g. 'Meeting Notes' or 'Research: AI trends'. .md extension added automatically."
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text/markdown content to save."
                    }
                },
                "required": ["filename", "content"]
            }
        },
        {
            "name": "list_drive_files",
            "description": "List recent files in the VoiceKit Research folder on Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of files to return. Default is 10."
                    }
                },
                "required": []
            }
        },

        # ── Gmail ────────────────────────────────────────────────────────────
        {
            "name": "get_recent_emails",
            "description": "Fetch the most recent emails from your Gmail inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of emails to retrieve. Default is 5. Maximum recommended is 20."
                    }
                },
                "required": []
            }
        },
        {
            "name": "search_emails",
            "description": "Search Gmail inbox using a query string (same syntax as Gmail search bar).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query, e.g. 'from:boss@company.com', 'subject:invoice', 'is:unread'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching emails to return. Default is 10."
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_email_details",
            "description": "Get the full body and details of a specific email by its message ID (obtained from get_recent_emails or search_emails).",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Gmail message ID, e.g. '18e4f3c2a1b0d9e8'. Obtain this from get_recent_emails or search_emails results."
                    }
                },
                "required": ["message_id"]
            }
        },
        {
            "name": "send_email",
            "description": "Compose and send an email via Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address, e.g. 'jane@example.com'"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "body": {
                        "type": "string",
                        "description": "Full email body text"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        },

        # ── Google Calendar ──────────────────────────────────────────────────
        {
            "name": "get_todays_events",
            "description": "Get your full schedule for today from Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "get_upcoming_events",
            "description": "Get your upcoming events for the next N days from Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days ahead to look. Default is 7 (one week)."
                    }
                },
                "required": []
            }
        },
        {
            "name": "create_event",
            "description": "Create a new event in Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Event title / name, e.g. 'Team standup' or 'Lunch with Alice'"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Event start time in ISO 8601 format, e.g. '2026-03-05T10:00:00'"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "Event end time in ISO 8601 format, e.g. '2026-03-05T11:00:00'"
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description or agenda. Leave empty if not needed."
                    },
                    "attendees": {
                        "type": "string",
                        "description": "Optional comma-separated attendee email addresses, e.g. 'alice@example.com,bob@example.com'. Leave empty if none."
                    }
                },
                "required": ["title", "start_time", "end_time"]
            }
        },
        {
            "name": "check_availability",
            "description": "Check your free time slots on a specific date in Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to check in YYYY-MM-DD format, e.g. '2026-03-05'"
                    }
                },
                "required": ["date"]
            }
        },

        # ── Google Chat ──────────────────────────────────────────────────────
        {
            "name": "list_chat_spaces",
            "description": "List all available Google Chat spaces and direct message threads you have access to.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "send_chat_message",
            "description": "Send a message to a Google Chat space or direct message thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_name": {
                        "type": "string",
                        "description": "The space resource name, e.g. 'spaces/AAAA1234'. Get this from list_chat_spaces first."
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text to send"
                    }
                },
                "required": ["space_name", "message"]
            }
        },

        # ── Basecamp ─────────────────────────────────────────────────────────
        {
            "name": "list_basecamp_projects",
            "description": "List all Basecamp projects you have access to, including their IDs and descriptions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "create_basecamp_todo",
            "description": "Create a new to-do item in a Basecamp project's todo list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Basecamp project ID. Obtain from list_basecamp_projects."
                    },
                    "todolist_id": {
                        "type": "string",
                        "description": "ID of the todo list within the project to add the item to."
                    },
                    "title": {
                        "type": "string",
                        "description": "The to-do item title / task description."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional longer description or notes for the to-do item."
                    }
                },
                "required": ["project_id", "todolist_id", "title"]
            }
        },
        {
            "name": "get_basecamp_messages",
            "description": "Fetch recent messages from a Basecamp project's message board.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Basecamp project ID. Obtain from list_basecamp_projects."
                    }
                },
                "required": ["project_id"]
            }
        },
        {
            "name": "post_basecamp_message",
            "description": "Post a new message to a Basecamp project's message board.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Basecamp project ID. Obtain from list_basecamp_projects."
                    },
                    "subject": {
                        "type": "string",
                        "description": "Message subject / title."
                    },
                    "body": {
                        "type": "string",
                        "description": "Message body text."
                    }
                },
                "required": ["project_id", "subject", "body"]
            }
        },
        {
            "name": "get_basecamp_checkins",
            "description": (
                "Fetch Automatic Check-in questions for a Basecamp project (or all projects if "
                "project_id is omitted). Shows each question, its question_id, and whether you "
                "have already answered it today. Call this first to discover question IDs before "
                "using answer_basecamp_checkin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Basecamp project ID (numeric) or project name (partial match). "
                            "Examples: '40471438' or 'Suzega HQ'. Leave empty to fetch check-ins from all projects."
                        )
                    }
                },
                "required": []
            }
        },
        {
            "name": "answer_basecamp_checkin",
            "description": (
                "Post your answer to a Basecamp Automatic Check-in question. "
                "Use get_basecamp_checkins first to discover the question_id and project_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Basecamp project ID (from get_basecamp_checkins)."
                    },
                    "question_id": {
                        "type": "string",
                        "description": "Check-in question ID (from get_basecamp_checkins)."
                    },
                    "content": {
                        "type": "string",
                        "description": "Your answer text."
                    }
                },
                "required": ["project_id", "question_id", "content"]
            }
        },
    ]

    # Merge: MCP tools override local tools with the same name (MCP wins)
    local_by_name = {t["name"]: t for t in local_tools}
    mcp_by_name   = {t["name"]: t for t in mcp_tools}
    merged_by_name = {**local_by_name, **mcp_by_name}
    tools = list(merged_by_name.values())

    tool_metadata = _build_tool_metadata(orch)
    return {"tools": tools, "count": len(tools), "tool_metadata": tool_metadata}


# ============================================================================
# Auth helpers — Web OAuth2 flows (no localhost servers; proper redirect_uri)
# ============================================================================

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/drive.file",
]
GOOGLE_CREDENTIALS = "integrations/google/credentials/google_credentials.json"

BASECAMP_AUTH_URL = "https://launchpad.37signals.com/authorization/new"
BASECAMP_TOKEN_URL = "https://launchpad.37signals.com/authorization/token"


def _lazy_init_auth_status(user_email: str) -> None:
    """Re-validate per-user auth status from the database (non-blocking, best-effort)."""
    mgr = _get_auth_mgr(user_email)
    # Avoid re-checking while a flow is in progress
    if mgr.google.status != "pending":
        try:
            import asyncio as _aio
            loop = _aio.get_event_loop()
            if loop.is_running():
                # schedule a background check; status will refresh on next poll
                loop.create_task(_async_check_google_status(user_email))
        except Exception:
            pass
    if mgr.basecamp.status != "pending":
        try:
            import asyncio as _aio
            loop = _aio.get_event_loop()
            if loop.is_running():
                loop.create_task(_async_check_basecamp_status(user_email))
        except Exception:
            pass


async def _async_check_google_status(user_email: str) -> None:
    """Check if user has a valid Google credential in the DB."""
    mgr = _get_auth_mgr(user_email)
    if mgr.google.status == "pending":
        return
    try:
        from sqlalchemy import select
        from database.models import UserCredential
        orch = await get_orchestration()
        async with orch.session_factory() as session:
            row = await session.execute(
                select(UserCredential).where(
                    UserCredential.user_email == user_email,
                    UserCredential.provider == "google",
                )
            )
            cred = row.scalar_one_or_none()
        if cred:
            data = json.loads(cred.token_json)
            if data.get("token") or data.get("access_token"):
                mgr.google.status = "connected"
                mgr.google.label = user_email
            else:
                mgr.google.status = "disconnected"
                mgr.google.label = None
        else:
            mgr.google.status = "disconnected"
            mgr.google.label = None
    except Exception as exc:
        logger.debug("Google status check failed: %s", exc)


async def _async_check_basecamp_status(user_email: str) -> None:
    """Check if user has a valid Basecamp credential in the DB."""
    mgr = _get_auth_mgr(user_email)
    if mgr.basecamp.status == "pending":
        return
    try:
        from sqlalchemy import select
        from database.models import UserCredential
        orch = await get_orchestration()
        async with orch.session_factory() as session:
            row = await session.execute(
                select(UserCredential).where(
                    UserCredential.user_email == user_email,
                    UserCredential.provider == "basecamp",
                )
            )
            cred = row.scalar_one_or_none()
        if cred:
            data = json.loads(cred.token_json)
            if data.get("access_token"):
                mgr.basecamp.status = "connected"
                accounts = data.get("accounts", [])
                mgr.basecamp.label = accounts[0].get("name", user_email) if accounts else user_email
            else:
                mgr.basecamp.status = "disconnected"
                mgr.basecamp.label = None
        else:
            mgr.basecamp.status = "disconnected"
            mgr.basecamp.label = None
    except Exception as exc:
        logger.debug("Basecamp status check failed: %s", exc)


def _start_google_flow(user_email: str) -> str:
    """
    Start Google OAuth2 web flow (no localhost server).

    Returns the Google authorization URL immediately.
    The callback is handled by GET /gemini-live/auth/google/callback.
    """
    from config import config as _config
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # allow http redirect in local dev

    if not Path(GOOGLE_CREDENTIALS).exists():
        raise FileNotFoundError(f"Google credentials file not found: {GOOGLE_CREDENTIALS}")

    redirect_uri = f"{_config.backend_public_url}/gemini-live/auth/google/callback"
    state = _new_state_token(user_email)

    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        GOOGLE_CREDENTIALS,
        scopes=GOOGLE_SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    # Store code_verifier so callback can pass it to fetch_token (PKCE round-trip)
    _pending_states[state]["code_verifier"] = flow.code_verifier

    mgr = _get_auth_mgr(user_email)
    mgr.google.status = "pending"
    mgr.google.error = None
    return auth_url


def _start_basecamp_flow(user_email: str) -> str:
    """
    Start Basecamp OAuth2 web flow (no localhost server).

    Returns the Basecamp authorization URL immediately.
    The callback is handled by GET /gemini-live/auth/basecamp/callback.
    """
    from config import config as _config

    client_id = os.environ.get("BASECAMP_CLIENT_ID", "")
    if not client_id:
        raise ValueError("BASECAMP_CLIENT_ID environment variable is required")

    redirect_uri = f"{_config.backend_public_url}/gemini-live/auth/basecamp/callback"
    state = _new_state_token(user_email)

    params = {
        "type": "web_server",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"{BASECAMP_AUTH_URL}?{urllib.parse.urlencode(params)}"

    mgr = _get_auth_mgr(user_email)
    mgr.basecamp.status = "pending"
    mgr.basecamp.error = None
    return auth_url


async def _save_credential(user_email: str, provider: str, token_json: str) -> None:
    """Upsert a UserCredential row in the database."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from database.models import UserCredential

    orch = await get_orchestration()
    async with orch.session_factory() as session:
        row = await session.execute(
            select(UserCredential).where(
                UserCredential.user_email == user_email,
                UserCredential.provider == provider,
            )
        )
        cred = row.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if cred is None:
            cred = UserCredential(
                user_email=user_email,
                provider=provider,
                token_json=token_json,
                updated_at=now,
            )
            session.add(cred)
        else:
            cred.token_json = token_json
            cred.updated_at = now
        await session.commit()


async def _delete_credential(user_email: str, provider: str) -> None:
    """Delete a UserCredential row from the database (disconnect)."""
    from sqlalchemy import select
    from database.models import UserCredential

    orch = await get_orchestration()
    async with orch.session_factory() as session:
        row = await session.execute(
            select(UserCredential).where(
                UserCredential.user_email == user_email,
                UserCredential.provider == provider,
            )
        )
        cred = row.scalar_one_or_none()
        if cred:
            await session.delete(cred)
            await session.commit()


# ============================================================================
# Auth Endpoints — per-user, JWT-authenticated, web OAuth2 callbacks
# ============================================================================

@router.get("/auth/status")
async def get_auth_status(current_user: CurrentUser):
    """
    Get per-user authentication status for Google and Basecamp integrations.

    Triggers an async DB check to refresh status from credentials table.
    Returns: { google: {status, label, error}, basecamp: {status, label, error} }
    """
    user_email = current_user["email"]
    # Trigger async status checks (returns immediately; status refreshes on next call)
    await _async_check_google_status(user_email)
    await _async_check_basecamp_status(user_email)
    mgr = _get_auth_mgr(user_email)
    return {
        "google": {"status": mgr.google.status, "label": mgr.google.label, "error": mgr.google.error},
        "basecamp": {"status": mgr.basecamp.status, "label": mgr.basecamp.label, "error": mgr.basecamp.error},
    }


@router.post("/auth/google/start")
async def start_google_auth(current_user: CurrentUser):
    """
    Start (or restart) Google OAuth web flow for the current user.

    Returns { auth_url } immediately. Open the URL in a new tab.
    Poll GET /auth/status every 2s until status becomes 'connected'.
    """
    user_email = current_user["email"]
    try:
        auth_url = _start_google_flow(user_email)
        return {"auth_url": auth_url}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to start Google auth for %s: %s", user_email, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/auth/google/callback")
async def google_auth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """
    OAuth2 callback from Google after user authorizes the application.

    Validates CSRF state token, exchanges the code, saves token to DB,
    reloads Google tools for this user, and redirects to frontend.
    """
    from config import config as _config

    if error:
        logger.warning("Google OAuth error: %s", error)
        return RedirectResponse(url=f"{_config.frontend_url}/?google_error={urllib.parse.quote(error)}")

    # Pop full state entry to retrieve both email and PKCE code_verifier
    _state_entry = _pending_states.pop(state or "", None)
    if _state_entry is None or time.time() - _state_entry["created_at"] > _STATE_TOKEN_TTL:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state token")
    user_email = _state_entry["email"]
    _code_verifier = _state_entry.get("code_verifier")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    mgr = _get_auth_mgr(user_email)
    try:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        redirect_uri = f"{_config.backend_public_url}/gemini-live/auth/google/callback"

        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            GOOGLE_CREDENTIALS,
            scopes=GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
        )
        # Restore the PKCE code_verifier so fetch_token can complete the exchange
        flow.code_verifier = _code_verifier
        full_url = str(request.url)
        flow.fetch_token(authorization_response=full_url)
        creds = flow.credentials

        # Save to database (per-user)
        await _save_credential(user_email, "google", creds.to_json())
        logger.info("Google token saved to DB for user %s", user_email)

        # Reload tools for this user in orchestration
        import orchestration as _orch_mod
        orch = _orch_mod._orchestration
        if orch:
            try:
                await orch.reload_google_tools_for_user(user_email)
            except Exception as exc:
                logger.warning("reload_google_tools_for_user failed: %s", exc)

        mgr.google.status = "connected"
        mgr.google.label = user_email
        mgr.google.error = None
        logger.info("Google OAuth complete for user %s", user_email)

    except Exception as exc:
        logger.error("Google OAuth callback failed for %s: %s", user_email, exc, exc_info=True)
        mgr.google.status = "error"
        mgr.google.error = str(exc)
        return RedirectResponse(
            url=f"{_config.frontend_url}/?google_error={urllib.parse.quote(str(exc))}"
        )

    return RedirectResponse(url=f"{_config.frontend_url}/?google_connected=true")


@router.post("/auth/google/cancel")
async def cancel_google_auth(current_user: CurrentUser):
    """Cancel an in-progress Google OAuth flow and return to disconnected state."""
    user_email = current_user["email"]
    mgr = _get_auth_mgr(user_email)
    mgr.google.status = "disconnected"
    mgr.google.error = None
    # Prune any pending state tokens for this user
    expired = [k for k, v in _pending_states.items() if v.get("email") == user_email]
    for k in expired:
        _pending_states.pop(k, None)
    return {"status": "cancelled"}


@router.post("/auth/basecamp/start")
async def start_basecamp_auth(current_user: CurrentUser):
    """
    Start (or restart) Basecamp OAuth web flow for the current user.

    Returns { auth_url } immediately. Open the URL in a new tab.
    Poll GET /auth/status every 2s until status becomes 'connected'.
    """
    user_email = current_user["email"]
    try:
        auth_url = _start_basecamp_flow(user_email)
        return {"auth_url": auth_url}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to start Basecamp auth for %s: %s", user_email, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/auth/basecamp/callback")
async def basecamp_auth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """
    OAuth2 callback from Basecamp after user authorizes the application.

    Validates CSRF state token, exchanges the code, saves token to DB,
    reloads Basecamp tools for this user, and redirects to frontend.
    """
    import json as _json
    import ssl
    import urllib.request
    from config import config as _config

    if error:
        logger.warning("Basecamp OAuth error: %s", error)
        return RedirectResponse(url=f"{_config.frontend_url}/?basecamp_error={urllib.parse.quote(error)}")

    user_email = _pop_state_token(state or "")
    if not user_email:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state token")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    client_id = os.environ.get("BASECAMP_CLIENT_ID", "")
    client_secret = os.environ.get("BASECAMP_CLIENT_SECRET", "")
    redirect_uri = f"{_config.backend_public_url}/gemini-live/auth/basecamp/callback"

    mgr = _get_auth_mgr(user_email)
    try:
        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ssl_ctx = ssl.create_default_context()

        payload = urllib.parse.urlencode({
            "type": "web_server",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }).encode()
        req = urllib.request.Request(
            f"{BASECAMP_TOKEN_URL}?type=web_server",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            token_data = _json.loads(resp.read())

        # Save to database (per-user)
        await _save_credential(user_email, "basecamp", _json.dumps(token_data))
        logger.info("Basecamp token saved to DB for user %s", user_email)

        # Reload tools for this user in orchestration
        import orchestration as _orch_mod
        orch = _orch_mod._orchestration
        if orch:
            try:
                await orch.reload_basecamp_tools_for_user(user_email)
            except Exception as exc:
                logger.warning("reload_basecamp_tools_for_user failed: %s", exc)

        accounts = token_data.get("accounts", [])
        mgr.basecamp.status = "connected"
        mgr.basecamp.label = accounts[0].get("name", user_email) if accounts else user_email
        mgr.basecamp.error = None
        logger.info("Basecamp OAuth complete for user %s", user_email)

    except Exception as exc:
        logger.error("Basecamp OAuth callback failed for %s: %s", user_email, exc, exc_info=True)
        mgr.basecamp.status = "error"
        mgr.basecamp.error = str(exc)
        return RedirectResponse(
            url=f"{_config.frontend_url}/?basecamp_error={urllib.parse.quote(str(exc))}"
        )

    return RedirectResponse(url=f"{_config.frontend_url}/?basecamp_connected=true")


@router.post("/auth/basecamp/cancel")
async def cancel_basecamp_auth(current_user: CurrentUser):
    """Cancel an in-progress Basecamp OAuth flow and return to disconnected state."""
    user_email = current_user["email"]
    mgr = _get_auth_mgr(user_email)
    mgr.basecamp.status = "disconnected"
    mgr.basecamp.error = None
    expired = [k for k, v in _pending_states.items() if v.get("email") == user_email]
    for k in expired:
        _pending_states.pop(k, None)
    return {"status": "cancelled"}


@router.delete("/auth/google/disconnect")
async def disconnect_google(current_user: CurrentUser):
    """Remove Google credentials from DB and reset in-memory status."""
    user_email = current_user["email"]
    await _delete_credential(user_email, "google")
    mgr = _get_auth_mgr(user_email)
    mgr.google.status = "disconnected"
    mgr.google.label = None
    mgr.google.error = None
    logger.info("Google disconnected for user %s", user_email)
    return {"status": "disconnected"}


@router.delete("/auth/basecamp/disconnect")
async def disconnect_basecamp(current_user: CurrentUser):
    """Remove Basecamp credentials from DB and reset in-memory status."""
    user_email = current_user["email"]
    await _delete_credential(user_email, "basecamp")
    mgr = _get_auth_mgr(user_email)
    mgr.basecamp.status = "disconnected"
    mgr.basecamp.label = None
    mgr.basecamp.error = None
    logger.info("Basecamp disconnected for user %s", user_email)
    return {"status": "disconnected"}


# ============================================================================
# MCP Servers — per-user custom MCP tool servers
# ============================================================================

class MCPServerCreateRequest(BaseModel):
    name: str = Field(..., description="Display name for the server")
    command: str = Field(..., description="Executable to run, e.g. 'npx' or 'python'")
    args: list = Field(default_factory=list, description="Arguments list")
    env: dict = Field(default_factory=dict, description="Environment variables")


class MCPServerToggleRequest(BaseModel):
    enabled: bool


@router.get("/mcp-servers")
async def list_mcp_servers(current_user: CurrentUser):
    """List the current user's configured MCP servers."""
    from sqlalchemy import select
    from database.models import UserMCPServer
    orch = await get_orchestration()
    async with orch.session_factory() as session:
        result = await session.execute(
            select(UserMCPServer)
            .where(UserMCPServer.user_email == current_user["email"])
            .order_by(UserMCPServer.created_at)
        )
        servers = result.scalars().all()
        # Augment with live tool counts from in-memory MCP client manager
        from collections import defaultdict
        tool_data: dict[str, dict] = {}
        mgr = orch._user_mcp_clients.get(current_user["email"])
        if mgr is not None:
            server_tools: dict[str, list] = defaultdict(list)
            for tool_name, srv_name in mgr._tool_to_server.items():
                server_tools[srv_name].append(tool_name)
            tool_data = {
                srv_name: {"count": len(tools), "names": sorted(tools)}
                for srv_name, tools in server_tools.items()
            }

        return [
            {
                "id": s.id,
                "name": s.name,
                "command": s.command,
                "args": json.loads(s.args_json or "[]"),
                "env": json.loads(s.env_json or "{}"),
                "enabled": s.enabled,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "tool_count": tool_data.get(s.name, {}).get("count", 0),
                "tool_names": tool_data.get(s.name, {}).get("names", []),
            }
            for s in servers
        ]


@router.post("/mcp-servers")
async def add_mcp_server(body: MCPServerCreateRequest, current_user: CurrentUser):
    """Add a new MCP server for the current user."""
    from database.models import UserMCPServer
    orch = await get_orchestration()
    server = UserMCPServer(
        user_email=current_user["email"],
        name=body.name,
        command=body.command,
        args_json=json.dumps(body.args),
        env_json=json.dumps(body.env),
        enabled=True,
    )
    async with orch.session_factory() as session:
        session.add(server)
        await session.commit()
        await session.refresh(server)
        server_id = server.id

    # Reload user MCP tools in background (don't block response)
    asyncio.create_task(orch.reload_user_mcp_tools(current_user["email"]))
    return {"id": server_id, "status": "created"}


@router.patch("/mcp-servers/{server_id}")
async def toggle_mcp_server(
    server_id: str,
    body: MCPServerToggleRequest,
    current_user: CurrentUser,
):
    """Enable or disable an MCP server."""
    from sqlalchemy import select
    from database.models import UserMCPServer
    orch = await get_orchestration()
    async with orch.session_factory() as session:
        result = await session.execute(
            select(UserMCPServer)
            .where(UserMCPServer.id == server_id)
            .where(UserMCPServer.user_email == current_user["email"])
        )
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="MCP server not found")
        server.enabled = body.enabled
        await session.commit()

    asyncio.create_task(orch.reload_user_mcp_tools(current_user["email"]))
    return {"id": server_id, "enabled": body.enabled}


@router.delete("/mcp-servers/{server_id}")
async def delete_mcp_server(server_id: str, current_user: CurrentUser):
    """Delete an MCP server."""
    from sqlalchemy import select
    from database.models import UserMCPServer
    orch = await get_orchestration()
    async with orch.session_factory() as session:
        result = await session.execute(
            select(UserMCPServer)
            .where(UserMCPServer.id == server_id)
            .where(UserMCPServer.user_email == current_user["email"])
        )
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="MCP server not found")
        await session.delete(server)
        await session.commit()

    asyncio.create_task(orch.reload_user_mcp_tools(current_user["email"]))
    return {"status": "deleted"}

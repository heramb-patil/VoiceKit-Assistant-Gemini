"""
ProcessEngine — the single entry point for all LLM tool calls.

This is the "mini operating system" that sits between the LLM and the tools.
The LLM never calls tools directly; it always goes through the ProcessEngine.

How it works
------------
  1. LLM makes a tool call → dispatch() is called
  2. on_ack(ack_message) is called immediately so the assistant can say
     "Sure, I'm fetching your emails" while the work is in progress
  3. The task is classified:
       INLINE     → execute synchronously, call on_result immediately
       AWAITED    → enqueue; user is waiting for the spoken result
       BACKGROUND → enqueue; result is a notification, user moved on
  4. on_result(TaskResult) is called when the task completes

Caller responsibilities
-----------------------
  on_ack:    Send the ack_message as the toolResponse back to the LLM
             (so the LLM can speak it right away and the session continues)
  on_result: Inject the result back into the conversation.
             For AWAITED: inject with turnComplete=True so the LLM speaks it.
             For BACKGROUND: inject with turnComplete=False as silent context.

Integration example (Gemini Live)
----------------------------------
    engine = ProcessEngine(library)
    await engine.start()

    async def handle_tool_call(fc_id, tool_name, args, send_tool_response, inject_text):
        await engine.dispatch(
            tool_name=tool_name,
            args=args,
            on_ack=lambda msg: send_tool_response(fc_id, msg),
            on_result=lambda r: inject_text(
                f"[{r.tool_name} result] {r.result}" if r.success
                else f"{r.tool_name} failed: {r.error}",
                turn_complete=(tool_category == TaskCategory.AWAITED),
            ),
        )
"""

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

from .models import QueueItem, TaskCategory, TaskResult
from .task_queue import TaskQueue
from .tool_library import ToolLibrary

logger = logging.getLogger(__name__)


class ProcessEngine:
    """
    Mini operating system for LLM tool calls.

    Decouples acknowledgment from execution:
      - The LLM always gets an immediate response (no silence)
      - The real result is delivered asynchronously via a callback
      - Multiple tools run concurrently under a semaphore
      - Shortest tasks are scheduled first (SJF)
    """

    def __init__(
        self,
        tool_library: ToolLibrary,
        max_concurrent: int = 3,
        task_timeout: float = 120.0,
    ) -> None:
        self.library = tool_library
        self._queue = TaskQueue(max_concurrent=max_concurrent, task_timeout=task_timeout)
        self._started = False

    async def start(self) -> None:
        """Start the task queue worker. Must be called once inside a running event loop."""
        if self._started:
            return
        await self._queue.start()
        self._started = True
        logger.info(
            "ProcessEngine started — %d tools registered, max_concurrent=%d",
            len(self.library), self._queue.max_concurrent,
        )

    async def stop(self) -> None:
        """Gracefully stop the engine."""
        await self._queue.stop()
        self._started = False
        logger.info("ProcessEngine stopped")

    # ── Main API ──────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        tool_name: str,
        args: dict,
        on_ack: Callable[[str], Awaitable[None]],
        on_result: Callable[[TaskResult], Awaitable[None]],
        session_id: str = "",
    ) -> str:
        """
        Handle one tool call from the LLM.

        Parameters
        ----------
        tool_name   Name of the tool to execute
        args        Tool arguments (keyword arguments dict)
        on_ack      Coroutine called immediately with the acknowledgment message.
                    Caller should send this as the toolResponse to the LLM.
        on_result   Coroutine called when the task finishes (may be immediate for
                    INLINE tools). Caller injects result into the conversation.
        session_id  Optional session identifier for logging / tracking.

        Returns
        -------
        task_id     Unique ID for this task (useful for status polling).
        """
        task_id = str(uuid.uuid4())
        tool = self.library.get(tool_name)

        # ── Unknown tool ──────────────────────────────────────────────────────
        if tool is None:
            logger.warning("[%s] Unknown tool: '%s'", session_id or task_id[:8], tool_name)
            await on_ack(f"I don't have a tool called '{tool_name}'.")
            await on_result(TaskResult(
                task_id=task_id,
                tool_name=tool_name,
                success=False,
                result="",
                error=f"Tool '{tool_name}' is not registered in the ProcessEngine library.",
                completed_at=time.time(),
            ))
            return task_id

        # ── Step 1: immediate acknowledgment ─────────────────────────────────
        logger.info(
            "[%s] dispatch → %s [%s, ~%.0fs]",
            session_id or task_id[:8], tool_name,
            tool.category.value, tool.estimated_seconds,
        )
        try:
            await on_ack(tool.ack_message)
        except Exception as ack_err:
            logger.error("[%s] on_ack callback raised: %s", task_id[:8], ack_err)

        # ── Step 2: INLINE — synchronous execution ────────────────────────────
        if tool.category == TaskCategory.INLINE:
            t0 = time.time()
            try:
                raw = await asyncio.wait_for(tool.fn(**args), timeout=30.0)
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=True,
                    result=str(raw),
                    completed_at=time.time(),
                )
            except asyncio.TimeoutError:
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=False,
                    result="",
                    error=f"'{tool_name}' timed out",
                    completed_at=time.time(),
                )
            except Exception as exc:
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=False,
                    result="",
                    error=str(exc),
                    completed_at=time.time(),
                )
            elapsed = (result.completed_at or time.time()) - t0
            logger.info("[%s] inline %s → %.0fms", task_id[:8], tool_name, elapsed * 1000)
            try:
                await on_result(result)
            except Exception as res_err:
                logger.error("[%s] on_result (inline) raised: %s", task_id[:8], res_err)
            return task_id

        # ── Step 3: AWAITED / BACKGROUND — queue for async execution ──────────
        item = QueueItem(
            priority=tool.estimated_seconds,
            submitted_at=time.time(),
            task_id=task_id,
            tool_name=tool_name,
            args=args,
            tool_fn=tool.fn,
            on_complete=on_result,
        )
        await self._queue.submit(item)
        return task_id

    # ── Convenience: dispatch many calls in parallel ──────────────────────────

    async def dispatch_many(
        self,
        calls: list[dict],
        on_result: Callable[[TaskResult], Awaitable[None]],
        session_id: str = "",
    ) -> list[str]:
        """
        Dispatch multiple tool calls concurrently.

        Each entry in *calls* must have keys: tool_name, args, on_ack.

        Returns list of task_ids in submission order.
        """
        tasks = [
            self.dispatch(
                tool_name=c["tool_name"],
                args=c.get("args", {}),
                on_ack=c["on_ack"],
                on_result=on_result,
                session_id=session_id,
            )
            for c in calls
        ]
        return list(await asyncio.gather(*tasks))

    # ── Status queries ────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._queue.get_task(task_id)

    def get_all_tasks(self) -> list[dict]:
        return self._queue.get_all_tasks()

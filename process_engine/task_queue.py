"""
TaskQueue — concurrent SJF (Shortest Job First) executor.

Architecture
------------
  - Tasks are pushed onto a min-heap ordered by (estimated_seconds, submitted_at).
  - A single worker coroutine drains the heap and spawns asyncio tasks.
  - A semaphore caps concurrency at MAX_CONCURRENT (default 3).
  - When a task finishes its on_complete callback is awaited with the TaskResult.

This module is intentionally decoupled from ToolLibrary and ProcessEngine so
it can be tested and reused independently.
"""

import asyncio
import heapq
import logging
import time
import uuid
from typing import Optional

from .models import QueueItem, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class TaskQueue:
    """
    SJF priority queue with bounded concurrency.

    Call start() once inside a running event loop before submitting tasks.
    """

    def __init__(self, max_concurrent: int = 3, task_timeout: float = 120.0) -> None:
        self.max_concurrent = max_concurrent
        self.task_timeout = task_timeout

        # Internal state — all created lazily in start() to avoid loop issues
        self._heap: list[QueueItem] = []
        self._heap_lock: Optional[asyncio.Lock] = None
        self._notify: Optional[asyncio.Event] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running: bool = False

        # In-memory task state for status polling
        self._tasks: dict[str, dict] = {}

    async def start(self) -> None:
        """Initialise internals and launch the worker. Call once."""
        self._heap_lock = asyncio.Lock()
        self._notify = asyncio.Event()
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._running = True
        self._worker_task = asyncio.create_task(self._worker(), name="process-engine-worker")
        logger.info("TaskQueue started (max_concurrent=%d, timeout=%.0fs)", self.max_concurrent, self.task_timeout)

    async def stop(self) -> None:
        """Gracefully stop the worker."""
        self._running = False
        if self._notify:
            self._notify.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("TaskQueue stopped")

    async def submit(self, item: QueueItem) -> None:
        """Enqueue a task for execution."""
        self._tasks[item.task_id] = {
            "task_id": item.task_id,
            "tool_name": item.tool_name,
            "status": TaskStatus.PENDING.value,
            "submitted_at": item.submitted_at,
            "started_at": None,
            "completed_at": None,
            "result": None,
        }

        async with self._heap_lock:
            heapq.heappush(self._heap, item)

        self._notify.set()
        logger.info(
            "TaskQueue: submitted %s (%s, priority=%.0fs)",
            item.task_id[:8], item.tool_name, item.priority,
        )

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[dict]:
        return list(self._tasks.values())

    # ── Internal worker ───────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """
        Main loop: drain the heap by spawning _run_item tasks, then wait.

        The loop re-checks after clearing the event to avoid a race condition
        where items arrive between the drain and the event clear.
        """
        while self._running:
            # Drain all pending items from the heap
            while True:
                item = None
                async with self._heap_lock:
                    if self._heap:
                        item = heapq.heappop(self._heap)
                if item is None:
                    break
                asyncio.create_task(
                    self._run_item(item),
                    name=f"task-{item.task_id[:8]}-{item.tool_name}",
                )

            # Clear event, then re-check for items that arrived during drain
            self._notify.clear()
            async with self._heap_lock:
                if self._heap:
                    continue   # items sneaked in — loop again without waiting

            await self._notify.wait()

    async def _run_item(self, item: QueueItem) -> None:
        """Execute one queued task inside the semaphore."""
        async with self._semaphore:
            task_id = item.task_id
            tool_name = item.tool_name
            started_at = time.time()

            # Update state: RUNNING
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = TaskStatus.RUNNING.value
                self._tasks[task_id]["started_at"] = started_at

            logger.info("TaskQueue: running %s (%s)", task_id[:8], tool_name)

            try:
                raw = await asyncio.wait_for(
                    item.tool_fn(**item.args),
                    timeout=self.task_timeout,
                )
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=True,
                    result=str(raw),
                    completed_at=time.time(),
                )
                status = TaskStatus.DONE

            except asyncio.TimeoutError:
                logger.warning("TaskQueue: %s (%s) timed out after %.0fs", task_id[:8], tool_name, self.task_timeout)
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=False,
                    result="",
                    error=f"Tool '{tool_name}' timed out after {self.task_timeout:.0f}s",
                    completed_at=time.time(),
                )
                status = TaskStatus.FAILED

            except Exception as exc:
                logger.error("TaskQueue: %s (%s) failed: %s", task_id[:8], tool_name, exc, exc_info=True)
                result = TaskResult(
                    task_id=task_id,
                    tool_name=tool_name,
                    success=False,
                    result="",
                    error=str(exc),
                    completed_at=time.time(),
                )
                status = TaskStatus.FAILED

            # Update in-memory state
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = status.value
                self._tasks[task_id]["result"] = result.result
                self._tasks[task_id]["completed_at"] = result.completed_at

            elapsed = (result.completed_at or time.time()) - started_at
            logger.info(
                "TaskQueue: %s (%s) → %s in %.1fs",
                task_id[:8], tool_name, status.value, elapsed,
            )

            # Deliver result to caller
            try:
                await item.on_complete(result)
            except Exception as cb_exc:
                logger.error(
                    "TaskQueue: on_complete callback for %s raised: %s",
                    task_id[:8], cb_exc, exc_info=True,
                )

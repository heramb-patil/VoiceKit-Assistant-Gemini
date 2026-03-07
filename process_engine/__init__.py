"""
ProcessEngine — mini operating system for LLM tool calls.

Components
----------
  ToolLibrary    Register tools (Python callables or MCP servers) with metadata.
  TaskQueue      SJF priority queue with bounded concurrency.
  ProcessEngine  Single dispatch point: immediate ACK + async result delivery.

Quick start
-----------
    from process_engine import ProcessEngine, ToolLibrary, TaskCategory

    library = ToolLibrary()
    library.register(
        name="get_recent_emails",
        fn=gmail.get_recent_emails,
        category=TaskCategory.AWAITED,
        estimated_seconds=6.0,
        ack_message="Checking your inbox.",
    )

    engine = ProcessEngine(library)
    await engine.start()

    # When the LLM makes a tool call:
    await engine.dispatch(
        tool_name="get_recent_emails",
        args={"max_results": 10},
        on_ack=lambda msg: send_tool_response(call_id, msg),   # immediate
        on_result=lambda r: inject_text(r.result, turn_complete=True),  # async
    )
"""

from .models import TaskCategory, TaskResult, TaskStatus, ToolDefinition, QueueItem
from .tool_library import ToolLibrary
from .task_queue import TaskQueue
from .process_engine import ProcessEngine

__all__ = [
    "ProcessEngine",
    "ToolLibrary",
    "TaskQueue",
    "TaskCategory",
    "TaskResult",
    "TaskStatus",
    "ToolDefinition",
    "QueueItem",
]

"""
Data models for the ProcessEngine.

Three task categories mirror how a voice assistant should handle tool calls:
  INLINE      — sub-second tools; execute synchronously, return result as toolResponse
  AWAITED     — read operations (1-15s); ACK immediately, user waits for spoken result
  BACKGROUND  — write operations / long research; ACK + notify, user does not wait
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional


class TaskStatus(Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"


class TaskCategory(Enum):
    INLINE     = "inline"
    AWAITED    = "awaited"
    BACKGROUND = "background"


@dataclass
class ToolDefinition:
    """A registered tool with its execution metadata."""
    name: str
    fn: Callable
    category: TaskCategory
    estimated_seconds: float
    ack_message: str
    description: str = ""


@dataclass
class TaskResult:
    """The outcome of a completed tool execution."""
    task_id: str
    tool_name: str
    success: bool
    result: str
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "task_id":       self.task_id,
            "tool_name":     self.tool_name,
            "success":       self.success,
            "result":        self.result,
            "error":         self.error,
            "created_at":    self.created_at,
            "completed_at":  self.completed_at,
        }


@dataclass(order=True)
class QueueItem:
    """
    Entry in the SJF min-heap.

    Sorted first by estimated_seconds (shorter jobs first), then by
    submitted_at (FIFO within the same priority bucket).
    """
    priority: float         # estimated_seconds — primary sort key (lower = higher priority)
    submitted_at: float     # FIFO tiebreaker — earlier submission wins

    task_id:   str      = field(compare=False)
    tool_name: str      = field(compare=False)
    args:      dict     = field(compare=False)
    tool_fn:   Callable = field(compare=False)
    on_complete: Callable[["TaskResult"], Awaitable[None]] = field(compare=False)

"""
ToolLibrary — registry of all tools available to the ProcessEngine.

Tools are registered with:
  - A Python callable (sync or async)
  - A category (INLINE / AWAITED / BACKGROUND)
  - An estimated execution time (seconds) — used for SJF scheduling
  - An acknowledgment message — spoken by the voice assistant immediately

MCP server tools can be bulk-registered via register_mcp_tools().
"""

import logging
from typing import Callable, Dict, List, Optional

from .models import TaskCategory, ToolDefinition

logger = logging.getLogger(__name__)


class ToolLibrary:
    """
    Registry of tools available for execution.

    Example:
        library = ToolLibrary()

        library.register(
            name="get_recent_emails",
            fn=gmail.get_recent_emails,
            category=TaskCategory.AWAITED,
            estimated_seconds=6.0,
            ack_message="Checking your inbox.",
            description="Fetch recent emails from Gmail",
        )
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable,
        category: TaskCategory,
        estimated_seconds: float,
        ack_message: str,
        description: str = "",
    ) -> "ToolLibrary":
        """
        Register a single tool.

        Returns self so calls can be chained:
            library.register(...).register(...)
        """
        if name in self._tools:
            logger.debug("ToolLibrary: overwriting existing tool '%s'", name)

        self._tools[name] = ToolDefinition(
            name=name,
            fn=fn,
            category=category,
            estimated_seconds=estimated_seconds,
            ack_message=ack_message,
            description=description,
        )
        logger.debug("ToolLibrary: registered '%s' [%s, ~%.0fs]", name, category.value, estimated_seconds)
        return self

    def register_many(self, definitions: List[dict]) -> "ToolLibrary":
        """
        Bulk-register tools from a list of dicts.

        Each dict must have the same keys as register() parameters.

        Example:
            library.register_many([
                {"name": "calculate", "fn": calc_fn,
                 "category": TaskCategory.INLINE, "estimated_seconds": 0.1,
                 "ack_message": "Calculating."},
                ...
            ])
        """
        for d in definitions:
            self.register(**d)
        return self

    def register_mcp_tools(self, mcp_manager, default_category: TaskCategory = TaskCategory.AWAITED) -> "ToolLibrary":
        """
        Bulk-register all tools exposed by an MCP server manager.

        mcp_manager must support:
            tools = await mcp_manager.list_all_tools()   # returns objects with .name
            result = await mcp_manager.call_tool(name, args)

        All MCP tools are registered under default_category (AWAITED by default).
        Override individual tools by calling register() afterwards.
        """
        import asyncio

        async def _load():
            tools = await mcp_manager.list_all_tools()
            for tool in tools:
                tool_name = tool.name

                async def _call(*, _name=tool_name, **kwargs) -> str:
                    return await mcp_manager.call_tool(_name, kwargs)

                _call.__name__ = tool_name

                self.register(
                    name=tool_name,
                    fn=_call,
                    category=default_category,
                    estimated_seconds=10.0,
                    ack_message=f"Running {tool_name}.",
                    description=getattr(tool, "description", ""),
                )
            logger.info("ToolLibrary: registered %d MCP tools", len(tools))

        # If already inside a running event loop, schedule as a task
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_load())
        except RuntimeError:
            asyncio.run(_load())

        return self

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Return the ToolDefinition for *name*, or None if not registered."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolLibrary({list(self._tools.keys())})"

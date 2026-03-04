"""Google Chat integration tools (standalone version)."""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_auth: Any = None


def _get_service():
    if _auth is None:
        raise RuntimeError("Chat auth not initialised")
    return _auth.build_service("chat", "v1")


async def list_chat_spaces() -> str:
    """List available Google Chat spaces and direct messages."""
    return await asyncio.to_thread(_list_chat_spaces_sync)


def _list_chat_spaces_sync() -> str:
    try:
        service = _get_service()
        result = service.spaces().list().execute()
        spaces = result.get("spaces", [])
        if not spaces:
            return "No Google Chat spaces found."
        lines = []
        for s in spaces:
            name = s.get("name", "")
            display = s.get("displayName") or s.get("name", "DM")
            space_type = s.get("spaceType") or s.get("type", "")
            lines.append(f"• {display} (ID: {name}, type: {space_type})")
        return "Available Chat spaces:\n" + "\n".join(lines)
    except Exception as exc:
        logger.exception("list_chat_spaces failed")
        return f"Failed to list Chat spaces: {exc}"


async def send_chat_message(space_name: str, message: str) -> str:
    """Send a message to a Google Chat space or DM."""
    return await asyncio.to_thread(_send_chat_message_sync, space_name, message)


def _send_chat_message_sync(space_name: str, message: str) -> str:
    try:
        service = _get_service()
        result = service.spaces().messages().create(
            parent=space_name,
            body={"text": message},
        ).execute()
        return f"Message sent to {space_name}. Message name: {result.get('name', '')}"
    except Exception as exc:
        logger.exception("send_chat_message failed")
        return f"Failed to send Chat message: {exc}"

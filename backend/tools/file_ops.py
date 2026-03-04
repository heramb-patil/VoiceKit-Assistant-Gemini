"""File system tools - create, read, list, and append files (standalone)."""

import asyncio
import os
from pathlib import Path

# Workspace root - all file operations are sandboxed here
_WORKSPACE = Path(os.environ.get("VOICEKIT_WORKSPACE", "data/workspace")).expanduser()


def _safe_path(rel_path: str) -> Path:
    """Resolve path inside workspace, blocking directory traversal."""
    resolved = (_WORKSPACE / rel_path).resolve()
    if not str(resolved).startswith(str(_WORKSPACE.resolve())):
        raise ValueError(f"Path '{rel_path}' escapes the workspace directory.")
    return resolved


async def create_file(path: str, content: str) -> str:
    """Create or overwrite a file with the given content.

    Args:
        path: Relative file path inside the workspace (e.g. 'notes/ideas.md').
        content: The text content to write into the file.
    """
    def _write():
        full = _safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return str(full)

    try:
        full_path = await asyncio.to_thread(_write)
        return f"File created: {full_path} ({len(content)} chars)"
    except Exception as exc:
        return f"Failed to create file: {exc}"


async def read_file(path: str) -> str:
    """Read and return the contents of a file.

    Args:
        path: Relative file path inside the workspace.
    """
    def _read():
        full = _safe_path(path)
        if not full.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return full.read_text(encoding="utf-8")

    try:
        content = await asyncio.to_thread(_read)
        return f"Contents of {path}:\n\n{content}"
    except Exception as exc:
        return f"Failed to read file: {exc}"


async def list_files(directory: str = ".") -> str:
    """List files and folders in the workspace (or a subdirectory).

    Args:
        directory: Relative directory path (default: workspace root).
    """
    def _list():
        full = _safe_path(directory)
        if not full.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")
        items = sorted(full.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        return [(p.name, "dir" if p.is_dir() else "file") for p in items]

    try:
        items = await asyncio.to_thread(_list)
        if not items:
            return f"Directory '{directory}' is empty."
        lines = [f"Contents of '{directory}':"]
        for name, typ in items:
            marker = "[DIR]" if typ == "dir" else "     "
            lines.append(f"  {marker} {name}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to list directory: {exc}"


async def append_to_file(path: str, content: str) -> str:
    """Append text to an existing file (or create it if it doesn't exist).

    Args:
        path: Relative file path inside the workspace.
        content: Text to append.
    """
    def _append():
        full = _safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        with full.open("a", encoding="utf-8") as f:
            f.write(content)
        return str(full)

    try:
        full_path = await asyncio.to_thread(_append)
        return f"Appended to {full_path} ({len(content)} chars)"
    except Exception as exc:
        return f"Failed to append to file: {exc}"

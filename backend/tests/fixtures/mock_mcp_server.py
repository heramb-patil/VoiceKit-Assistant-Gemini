#!/usr/bin/env python3
"""
Minimal MCP server for testing — runs as a subprocess over stdio.

Implements just enough of MCP to test MCPClientManager:
  • initialize / notifications/initialized handshake
  • tools/list  → 3 tools: echo, add_numbers, slow_tool
  • tools/call  → returns deterministic results

Tools:
  echo(message: str)         → "Echo: <message>"
  add_numbers(a: int, b: int) → str(a + b)
  fail_tool()                → raises RuntimeError (for error-path tests)

Usage (as subprocess):
  python3 tests/fixtures/mock_mcp_server.py
"""
import json
import sys
import time


TOOLS = [
    {
        "name": "echo",
        "description": "Echo the input message back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Text to echo"}
            },
            "required": ["message"],
        },
    },
    {
        "name": "add_numbers",
        "description": "Add two integers and return the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First integer"},
                "b": {"type": "integer", "description": "Second integer"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail_tool",
        "description": "Always returns an error (for error-path testing).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def respond(rid, result: dict) -> None:
    write({"jsonrpc": "2.0", "id": rid, "result": result})


def error(rid, code: int, message: str) -> None:
    write({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        rid = msg.get("id")  # None for notifications
        params = msg.get("params", {})

        if method == "initialize":
            respond(rid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp-server", "version": "1.0.0"},
            })

        elif method == "notifications/initialized":
            pass  # notification — no response

        elif method == "ping":
            respond(rid, {})

        elif method == "tools/list":
            respond(rid, {"tools": TOOLS})

        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})

            if name == "echo":
                text = f"Echo: {args.get('message', '')}"
                respond(rid, {"content": [{"type": "text", "text": text}]})

            elif name == "add_numbers":
                total = int(args.get("a", 0)) + int(args.get("b", 0))
                respond(rid, {"content": [{"type": "text", "text": str(total)}]})

            elif name == "fail_tool":
                error(rid, -32000, "Intentional failure for testing")

            else:
                error(rid, -32601, f"Unknown tool: {name}")

        elif rid is not None:
            # Unknown request — send method-not-found
            error(rid, -32601, f"Method not found: {method}")
        # else: unknown notification — ignore


if __name__ == "__main__":
    main()

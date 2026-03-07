# Adding Tools & MCP Servers to VoiceKit SAAS

A step-by-step guide for adding new capabilities without causing model hallucination or WebSocket crashes.

---

## How Tools Work End-to-End

```
Gemini Live (browser)
  └─▶ fires toolcall event
        └─▶ ToolRouter.tsx
              ├─▶ fast tool  → bridge.executeTool → POST /gemini-live/tool-execute
              │                  └─▶ orch.execute_tool() → tool_fn(**args) → result
              │                  └─▶ sends real toolResponse back to Gemini
              └─▶ slow tool  → ACK response immediately
                               POST /gemini-live/tool-submit → SJF queue
                               SSE stream delivers result later → injected as context
```

Gemini only sees:
1. The tool **schema** (name + description + parameters) — declared in `GET /tools`
2. The tool **response** — a plain string you return from `execute_tool`

Everything else (routing, state machine, batching) is invisible to the model.

---

## Option A — Native Python Tool

### 1. Create the tool function

Add a file under `backend/tools/` (or `backend/skills/` for research-style tools):

```python
# backend/tools/crm.py

async def get_contact(email: str) -> str:
    """Look up a CRM contact by email address."""
    # ... call your CRM API ...
    return f"Contact: Jane Doe, Account: Acme Corp, Stage: Closed Won"
```

**Critical rules:**
- Must be `async`
- Must return a **plain string** — not a dict, not a Pydantic model. If you return structured data, Gemini gets a raw `repr()` which looks like `{'key': 'val'}` and confuses it. Format the string to sound natural.
- Keep the string under ~2,500 characters. ToolRouter truncates at 3,000 chars for fast tools and 1,500 chars for SSE (background) results. Summarize on the backend rather than returning raw API blobs.
- Never raise an exception for expected failures (contact not found, API down) — return an error string instead: `return "Contact not found for that email."`

### 2. Register in `orchestration.py`

Add your import and registration in `_load_base_tools()` (no credentials needed) or `_load_google_tools_for_user()` / a new `_load_crm_tools_for_user()` if it needs per-user auth:

```python
# backend/orchestration.py  →  _load_base_tools()

from tools.crm import get_contact
self.tool_registry["get_contact"] = get_contact
```

### 3. Add the schema to `GET /tools` in `api.py`

This is the most important step for preventing hallucination. Gemini uses the description and parameter descriptions to decide *when* and *how* to call a tool.

```python
# backend/api.py  →  local_tools list (around line 752)

{
    "name": "get_contact",
    "description": (
        "Look up a contact in the CRM by their email address. "
        "Returns name, company, deal stage, and last activity. "
        "Use when the user asks about a customer, lead, or client."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "The contact's email address, e.g. 'jane@acme.com'"
            }
        },
        "required": ["email"]
    }
},
```

**Why hand-written schemas?** The codebase comment says it directly: reflection-based generation produces wrong types (`int` → `string`) and generic descriptions, causing Gemini to form bad calls. Write the schema by hand.

**Description checklist:**
- Say what it returns, not just what it does
- Include when to use it (vs a similar tool)
- Give an example value in each parameter description
- Keep the top-level description under 120 chars; put extra detail in parameter descriptions

### 4. Register timing + category in `TOOL_METADATA` (`api.py`)

```python
# backend/api.py  →  TOOL_METADATA dict

"get_contact": {"estimated_seconds": 3, "is_background": False},
```

And mirror it in the frontend with the **three-tier `category`** field:

```typescript
// frontend/src/components/ToolRouter.tsx  →  TOOL_META

get_contact: { estimatedSeconds: 3, isBackground: false, category: 'awaited' },
```

**Pick the right category:**

| Category | When to use | Behaviour |
|---|---|---|
| `'inline'` | Trivially fast (<1s): math, time, file reads | Executes synchronously; real result sent as toolResponse; Gemini responds immediately |
| `'awaited'` | User is waiting for data (reads, search, 1–15s) | ACK toolResponse instantly ("Checking your inbox."); runs in background; **result injected with `turnComplete=true`** so Gemini speaks it as soon as it arrives |
| `'background'` | Fire-and-forget writes or long tasks (>15s) | ACK instantly; result injected as **silent context** (`turnComplete=false`); Gemini incorporates it next time user speaks |

For `get_contact` (a 3s read the user is waiting for) → `'awaited'` is correct.

### 5. Add an ACK message (awaited + background tools)

```typescript
// frontend/src/components/ToolRouter.tsx  →  ACK_MESSAGES

get_contact: 'Looking up that contact. Result coming shortly.',
```

Gemini hears this phrase immediately while the tool runs. Add `"Result coming shortly."` to any awaited tool so Gemini knows not to retry. Falls back to `Running get_contact.` if omitted.

---

## Option B — System-Level MCP Server (shared across all users)

Use this when the tool source is an npm/pypi MCP package that all users share (e.g. `@modelcontextprotocol/server-filesystem`).

### 1. Set the env var

```bash
# .env or deployment environment

GEMINI_LIVE_MCP_ENABLED=true
GEMINI_LIVE_MCP_SERVERS='[
  {
    "name": "filesystem",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
    "env": {}
  }
]'
```

That's it for routing — `orchestration._load_mcp_tools()` discovers and registers all tools the server exposes automatically.

### 2. Add timing hints (optional but recommended)

MCP tool schemas are discovered automatically. Their timing defaults to `estimatedSeconds: 10, isBackground: false`. Override per-tool in the JSON config:

```json
{
  "name": "filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
  "env": {},
  "estimated_seconds": {
    "read_file": 1,
    "write_file": 2,
    "list_directory": 1
  }
}
```

`_build_tool_metadata()` in `api.py` merges these overrides into `TOOL_METADATA` at runtime — you do not need to edit Python code.

### 3. Add a system prompt hint if needed

If the new tools need behavioural rules (chaining, when to use them), add a line to the system instruction in `frontend/src/App.tsx`:

```typescript
// App.tsx  →  defaultConfig.systemInstruction

FILESYSTEM — use list_directory before read_file to confirm a path exists.
```

### 4. MCP tools do NOT need schema entries in `local_tools`

The `GET /tools` endpoint appends MCP tools after the hardcoded `local_tools` block using the schema from the MCP server itself. Adding them manually would create duplicates and confuse Gemini.

---

## Option C — Per-User MCP Server (user-configured via UI)

Users can add their own MCP servers through the MCPServersPanel in the frontend. These are stored in the `UserMCPServer` DB table and loaded lazily per-user in `orchestration._load_user_mcp_tools()`.

**No code changes needed.** The UI handles registration. The orchestration handles loading. Tools appear in `GET /tools` automatically for that user.

If a per-user MCP server's tools need timing overrides, you can document the expected `estimated_seconds` values and instruct users to add them through the panel if it exposes that option, or handle it via the system MCP path above.

---

## Crash Prevention Checklist

These are the specific failure modes that cause Gemini Live code-1007 disconnects.

### ✅ Never send a toolResponse unless state is WAITING_TOOLS or TOOL_EXECUTING

The `ToolResponseCoordinator` already enforces this. If you bypass it and call `client.sendToolResponse()` directly, you will get a 1007.

### ✅ Never truncate a result to zero length

If a tool returns an empty string, ToolRouter sends `${toolName} completed with no data returned.` as the response. Returning `""` or `None` and having the coordinator send an empty output object crashes the session.

Make sure your tool always returns a non-empty string:
```python
# Bad
return ""

# Good
return "No results found."
```

### ✅ Do not return raw JSON with database IDs or internal URLs in the result

Gemini reads the tool result and may try to call another tool using data it finds in the response. If your result contains a record ID like `{"id": "T1234567", "url": "basecamp.com/..."}`, Gemini may call `get_basecamp_todos` with `id=T1234567` immediately — usually getting an "Operation not implemented" error and crashing.

Return **human-readable summaries** from all tools, not raw API responses:
```python
# Bad — Gemini will try to do something with these IDs
return json.dumps(api_response)

# Good — Gemini reads it, summarises it, done
items = api_response["todos"]
lines = [f"• {t['title']} (due {t.get('due_on', 'no date')})" for t in items[:10]]
return "\n".join(lines) or "No todos found."
```

### ✅ Keep result length under 2,500 characters

ToolRouter truncates fast-tool results at 3,000 chars and SSE results at 1,500 chars before injecting into the session. Truncation mid-sentence causes Gemini to hallucinate the cut-off content. Summarise on the backend.

### ✅ Do not add an `isBackground: true` tool without registering it in `TOOL_METADATA`

If `getToolMeta(name)` falls back to `{ estimatedSeconds: 10, isBackground: false }`, a tool you intended to be background will be treated as fast/inline. If the tool is slow (>5s), Gemini times out waiting for a toolResponse, fires a toolcallcancellation, and the session becomes unreliable.

### ✅ Test schema validity before deploying

Gemini rejects tool declarations that contain:
- `required: []` — empty required array (stripped automatically by `normalizeSchema` in App.tsx, but verify)
- `properties: {}` — empty properties object (also stripped)
- Type values in lowercase (`"string"` instead of `"STRING"`) — `normalizeSchema` uppercases these, but check MCP-sourced schemas if they bypass normalisation

Use `GET /gemini-live/tools` to inspect what the frontend receives. If a field looks wrong there, it will be wrong in Gemini's tool registry.

---

## Quick-Reference: File Locations for Each Change

| What | File | Where |
|------|------|--------|
| Tool implementation | `backend/tools/<name>.py` or `backend/skills/<name>.py` | New file |
| Register in orchestration | `backend/orchestration.py` | `_load_base_tools()` or `_load_*_tools_for_user()` |
| Schema declaration | `backend/api.py` | `local_tools` list in `get_tools()` (~line 752) |
| Backend timing | `backend/api.py` | `TOOL_METADATA` dict (~line 124) |
| Frontend timing | `frontend/src/components/ToolRouter.tsx` | `TOOL_META` object (~line 46) |
| ACK message | `frontend/src/components/ToolRouter.tsx` | `ACK_MESSAGES` object (~line 94) |
| Behavioural rules | `frontend/src/App.tsx` | `systemInstruction` text (~line 96) |
| System MCP server | `.env` / deployment env | `GEMINI_LIVE_MCP_SERVERS` JSON |
| Per-user MCP server | UI (MCPServersPanel) | No code change needed |

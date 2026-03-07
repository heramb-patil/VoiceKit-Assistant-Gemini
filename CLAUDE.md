# VoiceKit SAAS — Claude Code Guide

Gemini Live voice assistant (speech-to-speech) with a FastAPI backend and React frontend.
Users speak → Gemini makes tool calls → backend executes them → results injected back into the live audio session.

---

## Running locally

```bash
# Terminal 1 — backend (port 8001)
cd backend
OAUTHLIB_INSECURE_TRANSPORT=1 .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2 — frontend (port 3000)
cd frontend
npm start
```

---

## Architecture

```
User speaks
  → Gemini Live (WebSocket, browser)
      → ToolRouter.tsx classifies the tool call (inline / awaited / background)
          → inline:     POST /gemini-live/tool-execute  → orchestration.execute_tool()
                        result sent back as toolResponse → Gemini speaks immediately
          → awaited:    ACK toolResponse sent instantly ("Checking your inbox.")
                        POST /gemini-live/tool-submit   → SJF BackgroundQueue
                        SSE stream delivers result      → injected with turnComplete=true
                        → Gemini speaks the result when ready (no silence)
          → background: ACK toolResponse sent instantly
                        POST /gemini-live/tool-submit   → SJF BackgroundQueue
                        SSE stream delivers result      → injected with turnComplete=false
                        → silent context, Gemini uses it next time user speaks
```

---

## Adding a new tool — 5 touch points in order

### 1. Write the function — `backend/tools/<name>.py`

```python
async def get_contact(email: str) -> str:
    # call your API
    return "Jane Doe — Acme Corp — Closed Won"   # must be non-empty string, never dict
```

Rules: always `async`, always return a non-empty plain string, never raise for expected failures (return an error string instead), keep under 2 500 chars.

### 2. Register it — `backend/orchestration.py`

In `_load_base_tools()` for shared tools (no credentials):
```python
from tools.crm import get_contact
self.tool_registry["get_contact"] = get_contact
```

For per-user auth tools, add a `_load_crm_tools_for_user()` method following the pattern of `_load_google_tools_for_user()`.

### 3. Add the schema — `backend/api.py` → `local_tools` list inside `get_tools()`

```python
{
    "name": "get_contact",
    "description": "Look up a CRM contact by email. Returns name, company, and deal stage. Use when user asks about a customer or lead.",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "Contact email, e.g. jane@acme.com"}
        },
        "required": ["email"]
    }
},
```

Description rules: say what it *returns*, say *when* to use it (vs similar tools), give an example value per parameter, keep under 120 chars at the top level.

### 4. Add timing — two places

`backend/api.py` → `TOOL_METADATA` dict:
```python
"get_contact": {"estimated_seconds": 3, "is_background": False},
```

`frontend/src/components/ToolRouter.tsx` → `TOOL_META` object:
```typescript
get_contact: { estimatedSeconds: 3, isBackground: false, category: 'awaited' },
```

### 5. Add ACK message — `frontend/src/components/ToolRouter.tsx` → `ACK_MESSAGES`

```typescript
get_contact: 'Looking up that contact. Result coming shortly.',
```

Gemini hears this immediately while the tool executes. Add `"Result coming shortly."` to any awaited tool — it stops Gemini from retrying the call. Falls back to `Running get_contact.` if omitted.

---

## Tool category — which one to pick

| Category | Use when | Gemini experience |
|---|---|---|
| `'inline'` | Result in <1s (calculate, get_current_time, file reads) | Executes synchronously; result in toolResponse; Gemini speaks right away |
| `'awaited'` | User is waiting for data: reads, search, 1–15s | ACK spoken immediately; result injected with `turnComplete=true` → Gemini speaks it |
| `'background'` | Fire-and-forget writes or tasks >15s | ACK spoken; result as silent context (`turnComplete=false`); no follow-up speech |

Default to `'awaited'` for any new read tool.
Default to `'background'` for any new write tool.

---

## Key files

| File | Owns |
|---|---|
| `backend/orchestration.py` | Tool registry, per-user auth loading, tool execution, background task DB |
| `backend/api.py` | All HTTP endpoints, tool schemas (`local_tools`), timing (`TOOL_METADATA`), SJF `BackgroundQueue` |
| `backend/tools/` | Shared tool implementations (no credentials needed) |
| `backend/skills/` | Research-style tools (`web_search`, `deep_research`) |
| `backend/integrations/google/` | Gmail, Calendar, Chat, Drive — one file each |
| `backend/integrations/basecamp/` | Basecamp auth + tools |
| `frontend/src/components/ToolRouter.tsx` | Routes Gemini tool calls to backend; owns `TOOL_META`, `ACK_MESSAGES`, three-tier dispatch |
| `frontend/src/App.tsx` | Gemini Live config, **system prompt** (`defaultConfig.systemInstruction`), tool schema normalisation |
| `frontend/src/contexts/TurnStateContext.tsx` | State machine (IDLE → TOOL_EXECUTING → MODEL_THINKING → …); prevents 1007 crashes |
| `frontend/src/lib/voicekit-bridge.ts` | HTTP client wrapping all backend API calls |
| `process_engine/` | Standalone ProcessEngine module (ToolLibrary + TaskQueue + ProcessEngine) — reusable outside SAAS |

---

## Changing Gemini's behaviour — system prompt

Edit the `systemInstruction` text in `frontend/src/App.tsx` around line 96.

Pattern for adding a new behavioural rule:
```
TOOL_GROUP_NAME — one-line rule about when/how to use these tools.
  "User phrase example" → tool_a → tool_b → speak result
```

After editing, **disconnect and reconnect** the voice session in the UI — Gemini only reads the system prompt at session start.

The two-stage async protocol is already in the prompt. Do not remove:
- The STAGE 1 / STAGE 2 section (explains ACK vs real result)
- The "never retry" rule (prevents duplicate tool calls)

---

## Critical rules — what breaks the session

These cause Gemini Live **code-1007 disconnects** or silent failures:

1. **Empty toolResponse** — tool must never return `""` or `None`. Return `"No results found."` instead.

2. **Raw JSON with IDs** — never return `json.dumps(api_response)`. Gemini reads IDs and immediately chains tool calls with them, causing crashes. Return human-readable summaries only.

3. **Result over 2 500 chars** — ToolRouter truncates at 3 000 chars (inline) and 1 500 chars (SSE). Mid-sentence truncation causes hallucination. Summarise on the backend.

4. **Missing TOOL_META entry** — if a tool is not in `TOOL_META`, it defaults to `category: 'awaited'`, `estimatedSeconds: 10`. That is fine. But if you intend it as `'inline'`, the missing entry makes it go through the background queue unnecessarily.

5. **Chained tools with awaited first step** — if tool B needs tool A's data, wait for the `[tool_a result]` text to arrive before calling tool B. Add the chain to the system prompt so Gemini knows to wait.

---

## ProcessEngine — standalone module

`process_engine/` is a self-contained Python package that can be imported anywhere:

```python
from process_engine import ProcessEngine, ToolLibrary, TaskCategory

library = ToolLibrary()
library.register("get_contact", get_contact_fn, TaskCategory.AWAITED, 3.0, "Looking up that contact.")

engine = ProcessEngine(library)
await engine.start()

await engine.dispatch(
    tool_name="get_contact",
    args={"email": "jane@acme.com"},
    on_ack=lambda msg: send_tool_response(call_id, msg),   # fires instantly
    on_result=lambda r: inject_text(r.result, turn_complete=True),  # fires when done
)
```

Categories mirror ToolRouter: `TaskCategory.INLINE` / `AWAITED` / `BACKGROUND`.
`dispatch_many()` handles multiple parallel calls in one await.

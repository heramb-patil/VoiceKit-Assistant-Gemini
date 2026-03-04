# VoiceKit-Enhanced Gemini Live Console

**Frontend integration with VoiceKit backend orchestration.**

---

## What Was Added

This is Google's `multimodal-live-api-web-console` with VoiceKit integration:

### New Files

1. **`src/lib/voicekit-bridge.ts`** (340 lines)
   - HTTP/WebSocket client for VoiceKit backend
   - Tool execution, task delegation, notifications
   - Health checks and reconnection logic

2. **`src/components/ToolRouter.tsx`** (135 lines)
   - Routes tool calls to backend when appropriate
   - Classifies tools as backend vs. local
   - Handles complex task delegation

3. **`src/components/NotificationHandler.tsx`** (155 lines)
   - Displays background task results
   - Real-time WebSocket notifications
   - Follow-up question handling

4. **`src/components/NotificationHandler.scss`** (120 lines)
   - Notification panel styling
   - Mobile responsive design

5. **`src/App.voicekit.tsx`** (115 lines)
   - Modified App.tsx with VoiceKit integration
   - Backend health check
   - Component wiring

6. **`.env.local`**
   - VoiceKit backend URL configuration
   - User identity for demo

---

## Setup

### 1. Install Dependencies

```bash
npm install
```

### 2. Configure Environment

Edit `.env.local`:

```env
# VoiceKit backend (should already be running on port 8001)
REACT_APP_VOICEKIT_API_URL=http://localhost:8001

# User identity (for demo)
REACT_APP_USER_IDENTITY=demo@voicekit.local

# Gemini API key (get from https://aistudio.google.com/app/apikey)
REACT_APP_GEMINI_API_KEY=your-api-key-here
```

### 3. Start Development Server

```bash
# Option A: Use original App.tsx (no VoiceKit)
npm start

# Option B: Use VoiceKit-enhanced App (RECOMMENDED)
# First, backup original and use VoiceKit version:
mv src/App.tsx src/App.original.tsx
mv src/App.voicekit.tsx src/App.tsx
npm start
```

**Server will be available at:** `http://localhost:3000`

---

## Testing the Integration

### 1. Verify Backend Connection

1. Start frontend: `npm start`
2. Open `http://localhost:3000`
3. Look for green status indicator: "✓ VoiceKit Backend Connected"

### 2. Test Simple Tool Execution

Say: **"What time is it in Tokyo?"**

**Expected flow:**
1. Voice → Gemini Live
2. Gemini calls `get_current_time` tool
3. ToolRouter routes to backend
4. Backend executes tool
5. Result → Gemini speaks it

**Check console:** Should see `[ToolRouter] Executing tool via backend`

### 3. Test Background Task

Say: **"Research quantum computing"**

**Expected flow:**
1. Voice → Gemini Live
2. Gemini calls tool with "research" keyword
3. ToolRouter delegates to ProcessingEngine
4. Backend starts background task
5. NotificationHandler shows notification panel
6. After 3 seconds: Task completes
7. Gemini speaks the result

**Check UI:** Notification panel should appear in bottom-right

### 4. Test Complex Orchestration

Say: **"Draft an email about our Q1 results"**

**Expected flow:**
1. Delegated as complex task
2. Backend ProcessingEngine asks: "Who should I send this to?"
3. You answer: "Send it to the engineering team"
4. Backend completes email draft
5. Result delivered via notification

---

## Architecture

```
┌─────────────────────────────────────┐
│  Gemini Live Frontend (React)       │
│  - Voice I/O via Gemini API         │
│  - Audio: Web Audio API             │
│  - Visualizations: Altair           │
└─────────────┬───────────────────────┘
              │
              ├─ VoiceKitBridge (HTTP/WS)
              │
┌─────────────▼───────────────────────┐
│  VoiceKit Backend (Port 8001)       │
│  - Tool execution                   │
│  - Background tasks                 │
│  - ProcessingEngine                 │
│  - 50+ integrations                 │
└─────────────────────────────────────┘
```

---

## Component Details

### VoiceKitBridge

**Purpose:** Communication with backend

**Methods:**
- `executeTool(name, args)` - Execute tool via backend
- `delegateTask(task, tools)` - Start background task
- `pollTasks()` - Get pending results (fallback)
- `connectNotifications(callback)` - WebSocket notifications
- `checkHealth()` - Backend health check

**Singleton:** `initVoiceKitBridge()`, `getVoiceKitBridge()`

### ToolRouter

**Purpose:** Route function calls

**Logic:**
- **Backend tools:** Gmail, Calendar, Google Docs, web_search, etc.
- **Complex tasks:** Keywords like "research", "analyze", "draft"
- **Local tools:** Altair charts, UI controls

**Process:**
1. Listen to `client.on('toolcall')`
2. Check if backend tool or complex task
3. Execute via bridge OR let default handler process

### NotificationHandler

**Purpose:** Display results

**Features:**
- WebSocket notifications (real-time)
- HTTP polling (fallback)
- Notification panel (bottom-right)
- Auto-speaks results via Gemini

**UI:** Collapsible panel, last 10 notifications, timestamps

---

## Troubleshooting

### Backend Not Connected

**Status:** "⚠ VoiceKit Backend Unavailable"

**Fix:**
1. Check backend is running: `curl http://localhost:8001/gemini-live/health`
2. Check `.env.local` has correct `REACT_APP_VOICEKIT_API_URL`
3. Check CORS settings in backend config

### Tools Not Routing to Backend

**Console shows:** No `[ToolRouter]` logs

**Fix:**
1. Check ToolRouter is mounted (should see in React DevTools)
2. Check client is available: `useLiveAPIContext().client`
3. Check tool name matches `BACKEND_TOOLS` array

### Notifications Not Appearing

**No notification panel shows up**

**Fix:**
1. Check WebSocket connection: Browser DevTools → Network → WS tab
2. Check NotificationHandler is mounted
3. Try manual test: Delegate task via curl, check if notification shows

### CORS Errors

**Console shows:** CORS policy blocks request

**Fix:**
1. Backend `.env` → `GEMINI_LIVE_CORS_ORIGINS=["http://localhost:3000"]`
2. Restart backend server
3. Hard refresh frontend (Cmd+Shift+R)

---

## Development

### File Structure

```
frontend/
├── src/
│   ├── lib/
│   │   ├── voicekit-bridge.ts      # Backend client
│   │   └── genai-live-client.ts    # (original)
│   ├── components/
│   │   ├── ToolRouter.tsx          # Tool routing
│   │   ├── NotificationHandler.tsx # Notifications
│   │   └── NotificationHandler.scss
│   ├── App.tsx                     # Main app (original)
│   ├── App.voicekit.tsx            # VoiceKit-enhanced
│   └── App.voicekit.scss
├── package.json
└── .env.local
```

### Adding New Backend Tools

1. Add tool name to `BACKEND_TOOLS` array in `ToolRouter.tsx`
2. Restart frontend: `npm start`
3. Tool calls will now route to backend

### Customizing Notifications

Edit `NotificationHandler.scss`:
- Position: `.notification-handler { bottom: 20px; right: 20px; }`
- Colors: `.notification-task_complete { border-left-color: #4caf50; }`
- Size: `.notification-handler { width: 400px; }`

---

## Original Google Console

To use the original console without VoiceKit:

```bash
# Restore original App.tsx
mv src/App.tsx src/App.voicekit.tsx
mv src/App.original.tsx src/App.tsx

# Start
npm start
```

All VoiceKit files can be safely deleted without affecting original functionality.

---

## Next Steps

1. ✅ Backend running (port 8001)
2. ✅ Frontend running (port 3000)
3. ✅ VoiceKit integration complete
4. 🚀 **Test end-to-end voice flow**
5. 📊 A/B test latency vs. LiveKit pipeline
6. 🎨 Customize UI and branding

---

## Resources

- **Backend API:** http://localhost:8001
- **Frontend:** http://localhost:3000
- **API Docs:** http://localhost:8001/docs
- **Original Google README:** See `README.md`

---

**Status:** Phase 2 Complete - Frontend Integration Ready for Testing ✅

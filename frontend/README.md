# Frontend - To Be Added in Phase 2

## What Goes Here

Fork of Google's **multimodal-live-api-web-console** React application with VoiceKit bridge integration.

---

## Phase 2 Implementation Steps

### 1. Fork Google's Repository

```bash
# Clone Google's console
git clone https://github.com/google/multimodal-live-api-web-console.git temp-console

# Copy to this directory
cp -r temp-console/* ./
rm -rf temp-console

# Install dependencies
npm install
```

### 2. Add VoiceKit Bridge Files

Create the following files (see plan document for full code):

```
frontend/
├── src/
│   ├── lib/
│   │   ├── voicekit-bridge.ts      # Backend HTTP/WebSocket client
│   │   └── tool-router.tsx         # Route tool calls to backend
│   └── components/
│       └── NotificationHandler.tsx  # Display backend notifications
```

### 3. Configure Environment

Create `.env.local`:
```env
REACT_APP_VOICEKIT_API_URL=http://localhost:8001
REACT_APP_GEMINI_API_KEY=your-gemini-api-key
```

### 4. Wire Into App

Edit `src/App.tsx` to integrate:
- VoiceKitBridge initialization
- ToolRouter component
- NotificationHandler component

### 5. Test Integration

```bash
npm start
# Opens http://localhost:3000
```

Test flow:
1. User speaks → Gemini Live
2. Tool call → Backend via bridge
3. Result → Gemini Live speaks

---

## Key Integration Points

From Google's console, we extend:

1. **Tool execution** (`client.on("toolcall", ...)`)
   - Intercept function calls
   - Route to backend via bridge
   - Return results to Gemini

2. **Notifications** (WebSocket)
   - Connect to backend on mount
   - Display task completion results
   - Handle follow-up questions

3. **UI enhancements**
   - Task status indicator
   - Notification panel
   - Follow-up question modal

---

## Architecture

```
Google's Console (Gemini Live WebSocket)
    ↓
VoiceKitBridge (HTTP/WebSocket client)
    ↓
ToolRouter (routes tools to backend)
    ↓
Backend API (port 8001)
    ↓
VoiceKit Orchestration (existing)
```

---

## Documentation References

- **Google's Console**: https://github.com/google/multimodal-live-api-web-console
- **Gemini Live API**: https://ai.google.dev/api/multimodal-live
- **Implementation Plan**: See main plan document in project root

---

**Status:** Not yet implemented
**ETA:** Phase 2 (2-3 days)
**Prerequisites:** Phase 1 backend (✅ Complete)

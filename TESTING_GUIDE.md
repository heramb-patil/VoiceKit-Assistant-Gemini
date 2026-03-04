# Crash-Resistant System Testing Guide

## Quick Start

```bash
# Terminal 1: Start backend
cd gemini-live/backend
uvicorn src.main:app --port 8001

# Terminal 2: Start frontend
cd gemini-live/frontend
npm start

# Browser will open at http://localhost:3000
```

## Test Suite

### Test 1: State Machine Tracking ⏱️ 2 minutes

**Objective**: Verify turn state transitions are tracked correctly

**Steps**:
1. Open browser console (F12)
2. Click "Connect" to start Gemini Live
3. Watch for: `[TurnState] IDLE → USER_SPEAKING (connection opened)`
4. Speak: "Hello"
5. Watch for state transitions:
   ```
   [TurnState] IDLE → USER_SPEAKING
   [TurnState] USER_SPEAKING → MODEL_THINKING
   [TurnState] MODEL_THINKING → MODEL_SPEAKING
   [TurnState] MODEL_SPEAKING → IDLE
   ```

**Expected**: All transitions logged clearly, no crashes

**Pass Criteria**: ✅ State transitions occur in correct order

---

### Test 2: Concurrent Tool Batching ⏱️ 3 minutes

**Objective**: Verify multiple tools execute and batch responses atomically

**Steps**:
1. Connect to Gemini Live
2. Speak: "Check my email, calendar, and search for quantum computing news"
3. Watch console for:
   ```
   [ToolRouter] Processing function call: get_recent_emails
   [ToolRouter] Processing function call: get_todays_events
   [ToolRouter] Processing function call: web_search
   [Coordinator] Added response for email, batch size: 1
   [Coordinator] Added response for calendar, batch size: 2
   [Coordinator] Added response for web_search, batch size: 3
   [Coordinator] Flushing batch of 3 responses
   [Coordinator] Batch sent successfully
   ```

**Expected**:
- All 3 tools execute concurrently
- Responses batched into single send
- Zero code 1007 errors

**Pass Criteria**:
- ✅ All tools complete successfully
- ✅ Responses batched (batch size reaches 3)
- ✅ No WebSocket errors

---

### Test 3: Background Task Notifications (CRITICAL) ⏱️ 5 minutes

**Objective**: Verify background tasks don't crash during conversation

**Steps**:
1. Connect to Gemini Live
2. Speak: "Do comprehensive research on quantum computing"
3. Wait 3 seconds (research starts in background)
4. While research is running, speak: "What's 2 plus 2?"
5. Model responds: "That's 4"
6. Continue conversation: "Tell me a joke"
7. Research completes (~30 seconds total)
8. Watch console for:
   ```
   [SafeQueue] Enqueued (priority 5, mode visual): Your background research...
   [SafeQueue] State not ready (MODEL_SPEAKING), deferring voice delivery
   [SafeQueue] Delivering visual notification
   ```

**Expected**:
- Research runs in background
- Conversation continues smoothly
- Notification appears in panel (right side)
- NO voice interruption
- NO code 1007 crash

**Pass Criteria**:
- ✅ Research completes without crashing
- ✅ Visual notification appears in panel
- ✅ No voice interruption during conversation
- ✅ Can ask about research: "What did the research find?"

---

### Test 4: Circuit Breaker Recovery ⏱️ 3 minutes

**Objective**: Verify system handles backend disconnection gracefully

**Steps**:
1. Connect to Gemini Live
2. In backend terminal, press Ctrl+C (stop backend)
3. Try to execute a tool: "Check my email"
4. Watch console for:
   ```
   [CircuitBreaker] Circuit tripped (OPEN): WebSocket connection closed
   [CircuitBreaker] Failure count: 1
   [CircuitBreaker] Backoff delay: 5s
   [ToolRouter] Circuit breaker OPEN, aborting all tool calls
   ```
5. Wait 5 seconds
6. Restart backend: `uvicorn src.main:app --port 8001`
7. Watch for:
   ```
   [CircuitBreaker] Backoff period elapsed, attempting recovery (HALF_OPEN)
   [CircuitBreaker] Circuit reset (CLOSED) - connection recovered
   ```

**Expected**:
- Tool execution blocked when circuit OPEN
- 5-second backoff before retry
- Automatic recovery after restart

**Pass Criteria**:
- ✅ Operations blocked during disconnect
- ✅ Circuit opens on failure
- ✅ Circuit closes after recovery
- ✅ No cascading errors

---

### Test 5: Multiple Background Tasks (STRESS TEST) ⏱️ 10 minutes

**Objective**: Verify system handles multiple concurrent background tasks

**Steps**:
1. Connect to Gemini Live
2. Speak: "Research quantum computing" (30s task)
3. Wait 2 seconds
4. Speak: "Check my email" (5s task)
5. Wait 2 seconds
6. Speak: "What's on my calendar today?" (5s task)
7. While tasks running, speak: "What's the capital of France?"
8. Model responds: "Paris"
9. Continue conversation: "Tell me about the Eiffel Tower"
10. Tasks complete in order: email (5s), calendar (5s), research (30s)
11. Check notification panel - should show 3 completed tasks

**Expected Timeline**:
- t=0s: Research starts (30s)
- t=2s: Email check starts (5s)
- t=4s: Calendar check starts (5s)
- t=7s: Email completes → visual notification
- t=9s: Calendar completes → visual notification
- t=30s: Research completes → visual notification
- Throughout: Conversation continues smoothly

**Pass Criteria**:
- ✅ All 3 tasks complete successfully
- ✅ All 3 notifications appear in panel
- ✅ Zero code 1007 crashes
- ✅ Conversation never interrupted
- ✅ Can ask about any completed task

---

## Debugging Commands

### View State History
```javascript
// In browser console
// Note: You may need to expose this via window object first
// For now, just watch console logs
```

### View Circuit Breaker Status
```javascript
// In browser console (if exposed)
window.__circuitBreaker?.getStats()
// Returns: { state, failureCount, backoffMs, nextRetryTime }
```

### View Notification Queue
```javascript
// In browser console (if exposed)
window.__notificationQueue?.getQueue()
// Returns: Array of pending notifications
```

### View Active Tool Calls
```javascript
// In browser console (if exposed)
window.__turnState?.getActiveToolCalls()
// Returns: Set of active tool call IDs
```

---

## Common Issues & Solutions

### Issue: "Cannot send realtimeInput in state MODEL_SPEAKING"
**Cause**: State machine correctly blocking protocol violation
**Solution**: Working as intended! Check that notification uses visual-only mode

### Issue: Tool responses delayed by 100ms
**Cause**: Coordinator batch window
**Solution**: Normal behavior, ensures atomic batching

### Issue: Notifications not appearing
**Cause**: Safe queue waiting for IDLE state
**Solution**: Check if model is currently speaking, notification will appear when safe

### Issue: Circuit breaker stays OPEN
**Cause**: Backend connection unstable
**Solution**: Check backend logs, ensure it's running and accessible

---

## Performance Benchmarks

### Latency Added by Crash-Resistant System

| Component | Latency | Justification |
|-----------|---------|---------------|
| Turn State Machine | <1ms | In-memory FSM |
| Tool Response Coordinator | 0-100ms | Batch window (prevents crashes) |
| Safe Notification Queue | 0-500ms | Check interval (prevents crashes) |
| Circuit Breaker | 0ms | Only active during failures |

**Total overhead**: <1ms in normal operation, up to 600ms for notification delivery

**Benefit**: Zero crashes vs. potential infinite retry loops

---

## Success Criteria Summary

### Critical (Must Pass All)
- [ ] Test 1: State transitions logged correctly
- [ ] Test 2: Concurrent tools batch atomically
- [ ] Test 3: Background tasks don't crash conversation
- [ ] Test 4: Circuit breaker handles disconnection
- [ ] Test 5: Multiple background tasks handled gracefully

### Optional (Nice to Have)
- [ ] All tools complete under 5s (excluding deep_research)
- [ ] Notification delivery under 1s when state is IDLE
- [ ] Circuit breaker recovers within 5s of backend restart
- [ ] State history shows no invalid transitions

---

## Reporting Issues

If any test fails, please provide:

1. **Console logs** (full output from browser console)
2. **Steps to reproduce** (exact commands spoken)
3. **Expected vs. actual behavior**
4. **Network tab** (if WebSocket errors)
5. **Backend logs** (if tool execution errors)

Example bug report:
```
Test: Background Task Notifications (Test 3)
Issue: Code 1007 crash when research completes
Steps:
  1. Spoke "Research quantum computing"
  2. Spoke "What's 2+2" while research running
  3. Crash occurred at 30s mark
Console logs:
  [... paste logs ...]
Expected: Visual notification only
Actual: Code 1007 WebSocket close
```

---

## Automated Testing (Future)

```bash
# Run unit tests
cd frontend
npm test

# Run E2E tests (if configured)
npm run test:e2e

# Run stress tests
npm run test:stress
```

Currently: **Manual testing recommended** (see tests above)

Future: **Automated test suite** with Playwright/Cypress

---

## Checklist for Production Deployment

Before deploying to production, verify:

- [ ] All 5 tests pass successfully
- [ ] No console errors during normal operation
- [ ] Circuit breaker recovers from backend restarts
- [ ] Notifications appear in panel for all background tasks
- [ ] Conversation continues smoothly with concurrent tasks
- [ ] No code 1007 crashes observed in 1-hour stress test
- [ ] Backend logs show no errors
- [ ] Network tab shows no failed WebSocket reconnections

---

*Last updated: 2026-02-27*

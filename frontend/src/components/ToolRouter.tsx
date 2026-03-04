/**
 * Tool Router Component
 *
 * Routes Gemini Live function calls to VoiceKit backend.
 *
 * Fast/slow classification:
 * - Fast tools (calendar, web_search, etc.) execute inline via bridge.executeTool
 * - Slow tools (email, deep_research, etc.) submit to SJF background queue and
 *   return a placeholder ACK immediately so the conversation continues.
 *
 * Result delivery:
 * - Fast tool results are queued in pendingResults and injected when IDLE.
 * - Slow tool results arrive via SSE (GET /tasks/stream) and are similarly queued.
 */

import { useEffect, useRef } from 'react';
import { getVoiceKitBridge } from '../lib/voicekit-bridge';
import { ToolResponseCoordinator } from '../lib/tool-response-coordinator';
import { CircuitBreaker } from '../lib/circuit-breaker';
import { useTurnState, TurnState } from '../contexts/TurnStateContext';
import { TaskItem, TaskCompletionEvent } from '../types';

interface ToolRouterProps {
  client: any;
  setTasks: React.Dispatch<React.SetStateAction<TaskItem[]>>;
}

interface PendingResult {
  toolName: string;
  text: string;
  timestamp: number;
  /** If true, inject as context (turnComplete: false) — used for background SSE results
   *  so the real-time audio session is not disrupted. Model responds when user next speaks.
   *  If false/undefined, inject as a complete user turn (turnComplete: true) — fast tools
   *  where user is actively waiting for an immediate verbal response. */
  isContext?: boolean;
}

// ── Tool metadata (mirrors backend TOOL_METADATA) ────────────────────────────

interface ToolMeta {
  estimatedSeconds: number;
  isBackground: boolean;
}

const TOOL_META: Record<string, ToolMeta> = {
  // Calendar — inline, user is waiting
  get_todays_events:      { estimatedSeconds: 2,  isBackground: false },
  get_upcoming_events:    { estimatedSeconds: 3,  isBackground: false },
  create_event:           { estimatedSeconds: 3,  isBackground: false },
  check_availability:     { estimatedSeconds: 2,  isBackground: false },
  // Search — inline
  web_search:             { estimatedSeconds: 5,  isBackground: false },
  // Email reads — inline: user is actively waiting for the answer, must be spoken
  get_recent_emails:      { estimatedSeconds: 6,  isBackground: false },
  search_emails:          { estimatedSeconds: 8,  isBackground: false },
  get_email_details:      { estimatedSeconds: 4,  isBackground: false },
  // Email writes — background: fire-and-forget, confirmation is enough
  send_email:             { estimatedSeconds: 5,  isBackground: true  },
  reply_email:            { estimatedSeconds: 5,  isBackground: true  },
  // Chat
  list_chat_spaces:       { estimatedSeconds: 3,  isBackground: false },
  get_chat_messages:      { estimatedSeconds: 4,  isBackground: false },
  send_chat_message:      { estimatedSeconds: 4,  isBackground: true  },
  // Basecamp reads — inline
  list_basecamp_projects: { estimatedSeconds: 4,  isBackground: false },
  get_basecamp_todos:     { estimatedSeconds: 5,  isBackground: false },
  get_basecamp_messages:  { estimatedSeconds: 5,  isBackground: false },
  get_basecamp_checkins:  { estimatedSeconds: 8,  isBackground: false },
  // Basecamp writes — background
  create_basecamp_todo:   { estimatedSeconds: 5,  isBackground: true  },
  post_basecamp_message:  { estimatedSeconds: 5,  isBackground: true  },
  update_basecamp_todo:   { estimatedSeconds: 4,  isBackground: true  },
  post_basecamp_comment:  { estimatedSeconds: 4,  isBackground: true  },
  answer_basecamp_checkin:{ estimatedSeconds: 4,  isBackground: false },
  // Deep research — always background
  deep_research:          { estimatedSeconds: 60, isBackground: true  },
};

function getToolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? { estimatedSeconds: 10, isBackground: false };
}

// ── Tools handled locally in the browser (everything else routes to backend) ──
// This includes MCP server tools which are dynamically discovered at runtime —
// they will never be in a hardcoded list, so we use an exclusion approach.

const LOCAL_TOOLS = new Set([
  'render_altair',  // Vega chart rendering — handled by Altair.tsx
]);

// ── Acknowledgment messages ───────────────────────────────────────────────────

const ACK_MESSAGES: Record<string, string> = {
  get_recent_emails:   'Checking your inbox.',
  search_emails:       'Searching your emails.',
  send_email:          'Sending that email now.',
  get_email_details:   'Pulling up that email.',
  get_todays_events:   'Looking at your calendar.',
  get_upcoming_events: 'Checking your upcoming schedule.',
  create_event:        'Creating that event.',
  check_availability:  'Checking your availability.',
  list_chat_spaces:    'Looking up your chat spaces.',
  send_chat_message:   'Sending that message.',
  web_search:          'Searching for that now.',
  deep_research:       'Starting comprehensive research — takes about 60 seconds. Feel free to keep chatting!',
  get_current_time:    'Checking the time.',
  calculate:           'Calculating.',
  read_file:           'Reading that file.',
  list_files:          'Listing files.',
  create_file:         'Creating that file.',
  append_to_file:      'Appending to that file.',
  list_basecamp_projects: 'Looking up your Basecamp projects.',
  get_basecamp_todos:  'Fetching your Basecamp todos.',
  create_basecamp_todo:'Creating that todo in Basecamp.',
  get_basecamp_messages:'Fetching Basecamp messages.',
  post_basecamp_message:'Posting that message to Basecamp.',
  get_basecamp_checkins:'Checking your Basecamp check-ins.',
  answer_basecamp_checkin:'Submitting your check-in answer.',
};

function getAck(toolName: string): string {
  return ACK_MESSAGES[toolName] || `Running ${toolName}.`;
}

function formatResultForInjection(toolName: string, result: any): string {
  const success = result?.success !== false;

  if (!success) {
    return `The ${toolName} call failed: ${result?.error || 'unknown error'}.`;
  }

  if (result?.background) return '';  // handled by NotificationHandler / SSE

  const data = result?.result;
  if (!data) return `${toolName} completed with no data returned.`;

  const preview = typeof data === 'string'
    ? data.substring(0, 2000)
    : JSON.stringify(data).substring(0, 2000);

  return `[${toolName} result] ${preview}\n[Please relay this to the user and wait for their next instruction before calling any more tools.]`;
}

// ── Unique session ID helper ──────────────────────────────────────────────────

function makeSessionId(): string {
  return `sess_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ToolRouter({ client, setTasks }: ToolRouterProps) {
  const coordinatorRef = useRef<ToolResponseCoordinator | null>(null);
  const circuitBreakerRef = useRef<CircuitBreaker>(new CircuitBreaker());

  const pendingResults = useRef<PendingResult[]>([]);
  const deliveryInterval = useRef<NodeJS.Timeout | null>(null);

  // SSE connection refs
  const sessionIdRef = useRef<string>(makeSessionId());
  const sseRef = useRef<EventSource | null>(null);

  const { canSendRealtimeInput, canSendToolResponse, addToolCall, removeToolCall, currentState, transitionTo } = useTurnState();

  // ── Delivery loop: inject pending results into conversation when idle ────────
  useEffect(() => {
    if (!client) return;

    deliveryInterval.current = setInterval(() => {
      if (pendingResults.current.length === 0) return;
      if (!canSendRealtimeInput()) return;
      if (!circuitBreakerRef.current.checkState()) return;

      const next = pendingResults.current.shift()!;
      console.log(`[ToolRouter] Delivering result for ${next.toolName} (isContext=${next.isContext})`);

      try {
        if (next.isContext) {
          // Background (SSE) result — inject as context without completing the turn.
          // turnComplete: false means the model does NOT respond immediately; it incorporates
          // this context when the user next speaks. This preserves the real-time audio session
          // and avoids the "empty turn / silence" bug that occurs when clientContent with
          // turnComplete: true is mixed with realtimeInput in the same session.
          client.send({ text: next.text }, false);
          // Keep state IDLE so audio stream remains active
        } else {
          // Fast (inline) result — user is actively waiting; trigger immediate model response.
          client.send({ text: next.text });
          transitionTo(TurnState.MODEL_THINKING, 'injected tool result');
        }
      } catch (err) {
        console.error('[ToolRouter] Failed to deliver result:', err);
        pendingResults.current.unshift(next);  // re-queue at front
      }
    }, 800);

    return () => {
      if (deliveryInterval.current) clearInterval(deliveryInterval.current);
    };
  }, [client]);

  // ── SSE subscription: receive background task completions ───────────────────
  useEffect(() => {
    if (!client) return;

    const bridge = getVoiceKitBridge();
    if (!bridge) return;

    const sessionId = sessionIdRef.current;
    const sseUrl = `${bridge.apiBaseUrl}/gemini-live/tasks/stream?session_id=${encodeURIComponent(sessionId)}`;
    console.log('[ToolRouter] Connecting SSE stream:', sseUrl);

    const sse = new EventSource(sseUrl);

    sse.onmessage = (e) => {
      try {
        // Keep-alive comments are empty strings — skip
        if (!e.data || e.data.startsWith(':')) return;

        const event = JSON.parse(e.data) as {
          task_id: string;
          tool_name: string;
          status: 'running' | 'done' | 'failed';
          result: string | null;
          estimated_seconds: number;
        };
        console.log('[ToolRouter] SSE event:', event.task_id, event.status);

        if (event.status === 'running') {
          // Update task to show spinner in TaskPanel
          setTasks(prev => prev.map(t =>
            t.id === event.task_id
              ? { ...t, status: 'running', startedAt: new Date() }
              : t
          ));
          return;
        }

        // done / failed — update task and queue voice injection
        setTasks(prev => prev.map(t =>
          t.id === event.task_id
            ? { ...t, status: event.status as 'done' | 'failed', result: event.result ?? undefined, completedAt: new Date() }
            : t
        ));

        const result = event.result ?? '';
        // Use a short preview (120 chars) to prevent Gemini from seeing raw IDs and
        // auto-chaining additional tool calls, which causes "Operation is not implemented" crashes.
        const text = event.status === 'done'
          ? `[${event.tool_name} result] ${result.slice(0, 1500)}`
          : `Task failed: ${event.tool_name} — ${result.slice(0, 400)}`;

        // isContext: true — background result, inject without ending the turn so the
        // real-time audio session is not disrupted (see delivery loop for details).
        pendingResults.current.push({ toolName: event.tool_name, text, timestamp: Date.now(), isContext: true });
      } catch (err) {
        console.error('[ToolRouter] Failed to parse SSE event:', err);
      }
    };

    sse.onerror = () => {
      console.warn('[ToolRouter] SSE connection error — browser will auto-retry');
    };

    sseRef.current = sse;

    return () => {
      sse.close();
      sseRef.current = null;
    };
  }, [client, setTasks]);

  // ── Main tool call handler ───────────────────────────────────────────────────
  useEffect(() => {
    if (!client) return;

    const bridge = getVoiceKitBridge();
    if (!bridge) {
      console.warn('[ToolRouter] VoiceKit bridge not initialized');
      return;
    }

    coordinatorRef.current = new ToolResponseCoordinator(client, {
      canSendToolResponse,
      getCurrentState: () => currentState,
    });

    const cancelledCalls = new Set<string>();

    const handleClose = () => {
      circuitBreakerRef.current.tripCircuit('connection closed');
      coordinatorRef.current?.cancelAll();
    };

    const handleOpen = () => {
      circuitBreakerRef.current.reset();
    };

    const handleToolCallCancellation = (cancellation: any) => {
      console.log('[ToolRouter] Cancellation received:', cancellation);
      (cancellation.ids || []).forEach((id: string) => {
        cancelledCalls.add(id);
        coordinatorRef.current?.removeResponse(id);
        removeToolCall(id);
      });
    };

    const handleToolCall = async (toolCall: any) => {
      console.log('[ToolRouter] Tool call received:', toolCall);

      if (!circuitBreakerRef.current.checkState()) {
        console.warn('[ToolRouter] Circuit open, aborting');
        return;
      }

      const allCalls = (toolCall.functionCalls || []).filter((fc: any) => {
        if (LOCAL_TOOLS.has(fc.name)) return false;
        if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); return false; }
        return true;
      });

      if (allCalls.length === 0) return;

      const fastCalls = allCalls.filter((fc: any) => !getToolMeta(fc.name).isBackground);
      const slowCalls = allCalls.filter((fc: any) => getToolMeta(fc.name).isBackground);

      slowCalls.sort((a: any, b: any) =>
        getToolMeta(a.name).estimatedSeconds - getToolMeta(b.name).estimatedSeconds
      );

      // Register all calls with turn state
      allCalls.forEach((fc: any) => addToolCall(fc.id));

      // ── Fast/foreground calls ─────────────────────────────────────────────
      // Wait for real result, then send it AS the toolResponse.
      // Never send an ACK placeholder — Gemini would treat the placeholder as
      // the final answer and hallucinate over it.
      fastCalls.forEach((fc: any) => {
        bridge.executeTool(fc.name, fc.args).then((result: any) => {
          if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); removeToolCall(fc.id); return; }

          let output: string;
          if (result?.success === false) {
            output = result?.error || `${fc.name} failed`;
          } else {
            const data = result?.result ?? result;
            output = typeof data === 'string' ? data : JSON.stringify(data);
          }

          console.log(`[ToolRouter] Fast result ready for ${fc.name} (${output.length} chars)`);
          coordinatorRef.current?.addResponse(fc.id, {
            id: fc.id,
            name: fc.name,
            response: { output: output.substring(0, 3000) },
          });
          coordinatorRef.current?.forceFlush().then(() => removeToolCall(fc.id));
        }).catch((err: any) => {
          console.error(`[ToolRouter] ${fc.name} threw:`, err);
          coordinatorRef.current?.addResponse(fc.id, {
            id: fc.id,
            name: fc.name,
            response: { output: `Error running ${fc.name}: ${err?.message || 'unknown error'}` },
          });
          coordinatorRef.current?.forceFlush().then(() => removeToolCall(fc.id));
        });
      });

      // ── Slow/background calls ─────────────────────────────────────────────
      // ACK immediately (user doesn't wait), result arrives via SSE.
      if (slowCalls.length > 0) {
        slowCalls.forEach((fc: any) => {
          coordinatorRef.current?.addResponse(fc.id, {
            id: fc.id,
            name: fc.name,
            response: { output: getAck(fc.name) },
          });
        });

        await coordinatorRef.current?.forceFlush();
        slowCalls.forEach((fc: any) => removeToolCall(fc.id));

        for (const fc of slowCalls) {
          if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); continue; }
          try {
            const meta = getToolMeta(fc.name);
            const submitted = await bridge.submitBackgroundTool(
              fc.name, fc.args || {}, sessionIdRef.current,
            );
            if (submitted) {
              setTasks(prev => [...prev, {
                id: submitted.task_id,
                description: fc.name,
                toolName: fc.name,
                estimatedSeconds: meta.estimatedSeconds,
                status: 'pending',
                createdAt: new Date(),
              }]);
            } else {
              // Fallback: inline execution, inject via pendingResults
              bridge.executeTool(fc.name, fc.args).then((result: any) => {
                const text = formatResultForInjection(fc.name, result);
                if (text) pendingResults.current.push({ toolName: fc.name, text, timestamp: Date.now() });
              }).catch((err: any) => {
                pendingResults.current.push({
                  toolName: fc.name,
                  text: `The ${fc.name} call failed: ${err?.message || 'unknown error'}.`,
                  timestamp: Date.now(),
                });
              });
            }
          } catch (err: any) {
            console.error(`[ToolRouter] Error submitting ${fc.name}:`, err);
          }
        }
      }
    };

    client.on('open', handleOpen);
    client.on('close', handleClose);
    client.on('toolcall', handleToolCall);
    client.on('toolcallcancellation', handleToolCallCancellation);

    return () => {
      coordinatorRef.current?.cancelAll();
      pendingResults.current = [];
      client.off('open', handleOpen);
      client.off('close', handleClose);
      client.off('toolcall', handleToolCall);
      client.off('toolcallcancellation', handleToolCallCancellation);
    };
  }, [client, canSendToolResponse, addToolCall, removeToolCall, setTasks]);

  return null;
}

/**
 * Tool Router Component
 *
 * Routes Gemini Live function calls to VoiceKit backend.
 *
 * Three-tier classification:
 *   INLINE     — sub-second tools (calculate, get_time); execute synchronously,
 *                result sent as toolResponse so Gemini speaks it immediately.
 *   AWAITED    — read operations (emails, search, calendar, 3-10s); ACK as
 *                toolResponse immediately ("Checking your inbox."), execute in
 *                background, inject result with turnComplete=true so Gemini
 *                speaks it as soon as it arrives. User hears no silence.
 *   BACKGROUND — write/long operations (sends, deep_research); ACK immediately,
 *                execute in background, inject result as silent context
 *                (turnComplete=false). User does not wait.
 *
 * Result delivery:
 * - INLINE results come back in the toolResponse itself.
 * - AWAITED results arrive via SSE → pendingResults (isContext=false) → spoken.
 * - BACKGROUND results arrive via SSE → pendingResults (isContext=true) → silent.
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

type ToolCategory = 'inline' | 'awaited' | 'background';

interface ToolMeta {
  estimatedSeconds: number;
  /** @deprecated use category instead */
  isBackground: boolean;
  /**
   * inline     — trivially fast (<1s); execute synchronously, result in toolResponse
   * awaited    — user is waiting for the result (reads, search); ACK immediately,
   *              deliver result with turnComplete=true so Gemini speaks it
   * background — fire-and-forget (writes, long research); ACK + silent context
   */
  category: ToolCategory;
}

const TOOL_META: Record<string, ToolMeta> = {
  // ── Truly instant — inline ────────────────────────────────────────────────
  get_current_time:        { estimatedSeconds: 0.1, isBackground: false, category: 'inline'     },
  calculate:               { estimatedSeconds: 0.1, isBackground: false, category: 'inline'     },
  read_file:               { estimatedSeconds: 0.5, isBackground: false, category: 'inline'     },
  list_files:              { estimatedSeconds: 0.5, isBackground: false, category: 'inline'     },
  create_file:             { estimatedSeconds: 0.5, isBackground: false, category: 'inline'     },
  append_to_file:          { estimatedSeconds: 0.5, isBackground: false, category: 'inline'     },

  // ── User waits for result — awaited (ACK + async spoken result) ───────────
  // Calendar: 2-3s — short enough to ACK ("Looking at your calendar…")
  get_todays_events:       { estimatedSeconds: 2,   isBackground: false, category: 'awaited'    },
  get_upcoming_events:     { estimatedSeconds: 3,   isBackground: false, category: 'awaited'    },
  create_event:            { estimatedSeconds: 3,   isBackground: false, category: 'awaited'    },
  check_availability:      { estimatedSeconds: 2,   isBackground: false, category: 'awaited'    },
  // Search — user always waits for spoken answer
  web_search:              { estimatedSeconds: 5,   isBackground: false, category: 'awaited'    },
  // Email reads — 6-8s of silence before; now ACK immediately
  get_recent_emails:       { estimatedSeconds: 6,   isBackground: false, category: 'awaited'    },
  search_emails:           { estimatedSeconds: 8,   isBackground: false, category: 'awaited'    },
  get_email_details:       { estimatedSeconds: 4,   isBackground: false, category: 'awaited'    },
  // Chat reads
  list_chat_spaces:        { estimatedSeconds: 3,   isBackground: false, category: 'awaited'    },
  get_chat_messages:       { estimatedSeconds: 4,   isBackground: false, category: 'awaited'    },
  // Basecamp reads
  list_basecamp_projects:  { estimatedSeconds: 4,   isBackground: false, category: 'awaited'    },
  get_basecamp_todos:      { estimatedSeconds: 5,   isBackground: false, category: 'awaited'    },
  get_basecamp_messages:   { estimatedSeconds: 5,   isBackground: false, category: 'awaited'    },
  get_basecamp_checkins:   { estimatedSeconds: 8,   isBackground: false, category: 'awaited'    },
  answer_basecamp_checkin: { estimatedSeconds: 4,   isBackground: false, category: 'awaited'    },
  // Drive reads
  list_drive_files:        { estimatedSeconds: 3,   isBackground: false, category: 'awaited'    },

  // ── Fire-and-forget writes — background (ACK + silent context) ───────────
  send_email:              { estimatedSeconds: 5,   isBackground: true,  category: 'background' },
  reply_email:             { estimatedSeconds: 5,   isBackground: true,  category: 'background' },
  send_chat_message:       { estimatedSeconds: 4,   isBackground: true,  category: 'background' },
  create_basecamp_todo:    { estimatedSeconds: 5,   isBackground: true,  category: 'background' },
  post_basecamp_message:   { estimatedSeconds: 5,   isBackground: true,  category: 'background' },
  update_basecamp_todo:    { estimatedSeconds: 4,   isBackground: true,  category: 'background' },
  post_basecamp_comment:   { estimatedSeconds: 4,   isBackground: true,  category: 'background' },
  upload_to_drive:         { estimatedSeconds: 5,   isBackground: true,  category: 'background' },
  // Long research — always background
  deep_research:           { estimatedSeconds: 60,  isBackground: true,  category: 'background' },
};

function getToolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? { estimatedSeconds: 10, isBackground: false, category: 'awaited' };
}

// ── Tools handled locally in the browser (everything else routes to backend) ──
// This includes MCP server tools which are dynamically discovered at runtime —
// they will never be in a hardcoded list, so we use an exclusion approach.

const LOCAL_TOOLS = new Set([
  'render_altair',  // Vega chart rendering — handled by Altair.tsx
]);

// ── Acknowledgment messages ───────────────────────────────────────────────────

const ACK_MESSAGES: Record<string, string> = {
  get_recent_emails:      'Checking your inbox. Result coming shortly.',
  search_emails:          'Searching your emails. Result coming shortly.',
  send_email:             'Sending that email now.',
  get_email_details:      'Pulling up that email. Result coming shortly.',
  get_todays_events:      'Looking at your calendar. Result coming shortly.',
  get_upcoming_events:    'Checking your upcoming schedule. Result coming shortly.',
  create_event:           'Creating that event. Result coming shortly.',
  check_availability:     'Checking your availability. Result coming shortly.',
  list_chat_spaces:       'Looking up your chat spaces. Result coming shortly.',
  get_chat_messages:      'Fetching those messages. Result coming shortly.',
  send_chat_message:      'Sending that message.',
  web_search:             'Searching for that now. Result coming shortly.',
  deep_research:          'Starting comprehensive research — takes about 60 seconds. Feel free to keep chatting!',
  get_current_time:       'Checking the time.',
  calculate:              'Calculating.',
  read_file:              'Reading that file.',
  list_files:             'Listing files.',
  create_file:            'Creating that file.',
  append_to_file:         'Appending to that file.',
  list_basecamp_projects: 'Looking up your Basecamp projects. Result coming shortly.',
  get_basecamp_todos:     'Fetching your Basecamp todos. Result coming shortly.',
  create_basecamp_todo:   'Creating that todo in Basecamp.',
  get_basecamp_messages:  'Fetching Basecamp messages. Result coming shortly.',
  post_basecamp_message:  'Posting that message to Basecamp.',
  get_basecamp_checkins:  'Fetching your Basecamp check-ins. Result coming shortly — do not call this tool again.',
  answer_basecamp_checkin:'Submitting your check-in answer.',
  list_drive_files:       'Checking your Drive files. Result coming shortly.',
  upload_to_drive:        'Uploading to Drive.',
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

  /**
   * Task IDs that belong to AWAITED tools.
   * When their SSE result arrives, it is injected with isContext=false
   * (turnComplete=true) so Gemini speaks the result immediately.
   * Background task IDs are NOT in this set — they use isContext=true.
   */
  const awaitedTaskIds = useRef<Set<string>>(new Set());

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
        // Use a short preview to prevent Gemini from seeing raw IDs and
        // auto-chaining additional tool calls, which causes "Operation is not implemented" crashes.
        const text = event.status === 'done'
          ? `[${event.tool_name} result] ${result.slice(0, 1500)}`
          : `Task failed: ${event.tool_name} — ${result.slice(0, 400)}`;

        // AWAITED tasks: inject with isContext=false (turnComplete=true) so Gemini
        // responds immediately with the result — the user was waiting for it.
        // BACKGROUND tasks: inject with isContext=true (turnComplete=false) as
        // silent context — the user has moved on.
        const isAwaited = awaitedTaskIds.current.has(event.task_id);
        if (isAwaited) {
          awaitedTaskIds.current.delete(event.task_id);
          console.log(`[ToolRouter] SSE awaited result for ${event.tool_name} — will trigger immediate Gemini response`);
        }
        pendingResults.current.push({
          toolName: event.tool_name,
          text,
          timestamp: Date.now(),
          isContext: !isAwaited,  // false = turnComplete:true = Gemini speaks; true = silent context
        });
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

      // Three-tier classification
      const inlineCalls     = allCalls.filter((fc: any) => getToolMeta(fc.name).category === 'inline');
      const awaitedCalls    = allCalls.filter((fc: any) => getToolMeta(fc.name).category === 'awaited');
      const backgroundCalls = allCalls.filter((fc: any) => getToolMeta(fc.name).category === 'background');

      // Sort background by estimated duration (SJF — shortest first for queue priority)
      backgroundCalls.sort((a: any, b: any) =>
        getToolMeta(a.name).estimatedSeconds - getToolMeta(b.name).estimatedSeconds
      );

      // Register all calls with turn state machine
      allCalls.forEach((fc: any) => addToolCall(fc.id));

      // ── INLINE calls ───────────────────────────────────────────────────────
      // Execute synchronously, send real result as toolResponse.
      // Each runs in an isolated async closure; removeToolCall always fires.
      inlineCalls.forEach((fc: any) => {
        (async () => {
          let output = '';

          try {
            const result = await bridge.executeTool(fc.name, fc.args);
            if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); return; }
            if (result?.success === false) {
              output = result.error || `${fc.name} is unavailable right now.`;
            } else {
              const data = result?.result ?? result;
              const s = typeof data === 'string' ? data : JSON.stringify(data);
              output = s || `${fc.name} completed.`;
            }
          } catch (err: any) {
            if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); return; }
            output = `${fc.name} ran into a problem. Please try again.`;
            console.error(`[ToolRouter] ${fc.name} threw:`, err);
          }

          try {
            console.log(`[ToolRouter] Inline result ready for ${fc.name} (${output.length} chars)`);
            coordinatorRef.current?.addResponse(fc.id, {
              id: fc.id,
              name: fc.name,
              response: { output: output.substring(0, 3000) },
            });
            await (coordinatorRef.current?.forceFlush() ?? Promise.resolve());
          } catch (sendErr: any) {
            console.error(`[ToolRouter] Failed to send inline response for ${fc.name}:`, sendErr);
          } finally {
            removeToolCall(fc.id);
          }
        })().catch((err: any) => {
          console.error(`[ToolRouter] Unexpected error in inline tool ${fc.name}:`, err);
          removeToolCall(fc.id);
        });
      });

      // ── AWAITED calls ──────────────────────────────────────────────────────
      // User is waiting for the result but the tool is slow (3-10s).
      // ACK immediately as toolResponse → Gemini speaks "Checking your inbox."
      // Execute in background via SSE queue → when done, inject result with
      // isContext=false (turnComplete=true) so Gemini speaks it right away.
      if (awaitedCalls.length > 0) {
        try {
          awaitedCalls.forEach((fc: any) => {
            coordinatorRef.current?.addResponse(fc.id, {
              id: fc.id,
              name: fc.name,
              response: { output: getAck(fc.name) },
            });
          });
          await (coordinatorRef.current?.forceFlush() ?? Promise.resolve());
        } catch (ackErr: any) {
          console.error('[ToolRouter] Failed to send ACK for awaited tools:', ackErr);
        } finally {
          awaitedCalls.forEach((fc: any) => removeToolCall(fc.id));
        }

        for (const fc of awaitedCalls) {
          if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); continue; }
          try {
            const meta = getToolMeta(fc.name);
            const submitted = await bridge.submitBackgroundTool(
              fc.name, fc.args || {}, sessionIdRef.current,
            );
            if (submitted) {
              // Mark as awaited so SSE handler uses isContext=false (triggers speech)
              awaitedTaskIds.current.add(submitted.task_id);
              setTasks(prev => [...prev, {
                id: submitted.task_id,
                description: fc.name,
                toolName: fc.name,
                estimatedSeconds: meta.estimatedSeconds,
                status: 'pending',
                createdAt: new Date(),
              }]);
            } else {
              // Fallback: inline execution, inject as awaited (isContext=false)
              bridge.executeTool(fc.name, fc.args).then((result: any) => {
                const text = formatResultForInjection(fc.name, result);
                if (text) pendingResults.current.push({ toolName: fc.name, text, timestamp: Date.now(), isContext: false });
              }).catch((err: any) => {
                pendingResults.current.push({
                  toolName: fc.name,
                  text: `The ${fc.name} call failed: ${err?.message || 'unknown error'}.`,
                  timestamp: Date.now(),
                  isContext: false,
                });
              });
            }
          } catch (err: any) {
            console.error(`[ToolRouter] Error submitting awaited tool ${fc.name}:`, err);
          }
        }
      }

      // ── BACKGROUND calls ───────────────────────────────────────────────────
      // Fire-and-forget writes / long research.
      // ACK immediately, result injected as silent context (isContext=true).
      if (backgroundCalls.length > 0) {
        try {
          backgroundCalls.forEach((fc: any) => {
            coordinatorRef.current?.addResponse(fc.id, {
              id: fc.id,
              name: fc.name,
              response: { output: getAck(fc.name) },
            });
          });
          await (coordinatorRef.current?.forceFlush() ?? Promise.resolve());
        } catch (ackErr: any) {
          console.error('[ToolRouter] Failed to send ACK for background tools:', ackErr);
        } finally {
          backgroundCalls.forEach((fc: any) => removeToolCall(fc.id));
        }

        for (const fc of backgroundCalls) {
          if (cancelledCalls.has(fc.id)) { cancelledCalls.delete(fc.id); continue; }
          try {
            const meta = getToolMeta(fc.name);
            const submitted = await bridge.submitBackgroundTool(
              fc.name, fc.args || {}, sessionIdRef.current,
            );
            if (submitted) {
              // NOT added to awaitedTaskIds — SSE handler will use isContext=true
              setTasks(prev => [...prev, {
                id: submitted.task_id,
                description: fc.name,
                toolName: fc.name,
                estimatedSeconds: meta.estimatedSeconds,
                status: 'pending',
                createdAt: new Date(),
              }]);
            } else {
              // Fallback: inline execution, inject as background context
              bridge.executeTool(fc.name, fc.args).then((result: any) => {
                const text = formatResultForInjection(fc.name, result);
                if (text) pendingResults.current.push({ toolName: fc.name, text, timestamp: Date.now(), isContext: true });
              }).catch((err: any) => {
                pendingResults.current.push({
                  toolName: fc.name,
                  text: `The ${fc.name} call failed: ${err?.message || 'unknown error'}.`,
                  timestamp: Date.now(),
                  isContext: true,
                });
              });
            }
          } catch (err: any) {
            console.error(`[ToolRouter] Error submitting background tool ${fc.name}:`, err);
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

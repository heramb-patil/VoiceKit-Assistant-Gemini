/**
 * VoiceKit Backend Bridge
 *
 * Connects the Gemini Live frontend to VoiceKit's backend orchestration.
 * Handles HTTP requests and WebSocket notifications.
 *
 * SaaS changes:
 * - Constructor takes `getIdToken: () => string | null` instead of static userIdentity
 * - All fetch() calls send `Authorization: Bearer <token>`
 * - WebSocket URL uses `?token=<token>` query param (headers not supported in browser WS)
 * - `user_identity` removed from all request bodies (derived server-side from JWT)
 * - `userIdentity` getter decodes email from JWT for backward compat
 */

import { jwtDecode } from "jwt-decode";

export interface ToolExecuteRequest {
  tool_name: string;
  tool_args: Record<string, any>;
}

export interface ToolExecuteResponse {
  success: boolean;
  result: string;
  error?: string;
}

export interface TaskDelegateRequest {
  task_description: string;
  tool_names?: string[];
}

export interface TaskDelegateResponse {
  task_id: string;
  status: string;
}

export interface TaskResult {
  task_id: string;
  status: string;
  result: string;
  tool_name: string;
  created_at?: string;
  completed_at?: string;
}

export interface NotificationMessage {
  type: 'task_complete' | 'followup_question' | 'error';
  task_id?: string;
  result?: string;
  tool_name?: string;
  question?: string;
  error?: string;
}

export class VoiceKitBridge {
  readonly apiBaseUrl: string;
  private getIdToken: () => string | null;
  private ws: WebSocket | null = null;
  private notificationCallbacks: ((msg: NotificationMessage) => void)[] = [];
  private reconnectInterval: number = 5000;
  private reconnectTimer: NodeJS.Timeout | null = null;

  constructor(apiBaseUrl: string, getIdToken: () => string | null) {
    this.apiBaseUrl = apiBaseUrl;
    this.getIdToken = getIdToken;
  }

  /**
   * Returns the authenticated user's email decoded from the JWT.
   * Used for display/logging purposes only — do not trust for authorization.
   */
  get userIdentity(): string {
    const token = this.getIdToken();
    if (!token) return "";
    try {
      const decoded = jwtDecode<{ email?: string }>(token);
      return decoded.email ?? "";
    } catch {
      return "";
    }
  }

  /**
   * Build auth headers for all fetch requests.
   */
  private authHeaders(): Record<string, string> {
    const token = this.getIdToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  /**
   * Connect to WebSocket notifications
   */
  connectNotifications(onNotification: (msg: NotificationMessage) => void): void {
    this.notificationCallbacks.push(onNotification);

    if (this.ws?.readyState === WebSocket.OPEN) {
      console.log('[VoiceKit] WebSocket already connected');
      return;
    }

    this.connect();
  }

  private connect(): void {
    const token = this.getIdToken();
    if (!token) {
      console.warn('[VoiceKit] No auth token — skipping WebSocket connection');
      return;
    }

    const wsUrl = this.apiBaseUrl.replace('http://', 'ws://').replace('https://', 'wss://');
    const url = `${wsUrl}/gemini-live/notifications?token=${encodeURIComponent(token)}`;

    console.log('[VoiceKit] Connecting to WebSocket notifications...');

    try {
      this.ws = new WebSocket(url);

      this.ws.onopen = () => {
        console.log('[VoiceKit] WebSocket connected');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const msg: NotificationMessage = JSON.parse(event.data);
          console.log('[VoiceKit] Received notification:', msg);
          this.notificationCallbacks.forEach(cb => cb(msg));
        } catch (error) {
          console.error('[VoiceKit] Failed to parse notification:', error);
        }
      };

      this.ws.onerror = (error) => {
        console.error('[VoiceKit] WebSocket error:', error);
      };

      this.ws.onclose = () => {
        console.log('[VoiceKit] WebSocket closed, will attempt to reconnect...');
        this.ws = null;
        this.scheduleReconnect();
      };
    } catch (error) {
      console.error('[VoiceKit] Failed to create WebSocket:', error);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;

    this.reconnectTimer = setTimeout(() => {
      console.log('[VoiceKit] Attempting to reconnect...');
      this.reconnectTimer = null;
      this.connect();
    }, this.reconnectInterval);
  }

  /**
   * Disconnect WebSocket
   */
  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.notificationCallbacks = [];
  }

  /**
   * Submit a tool call to the SJF background queue.
   * Returns {task_id, estimated_seconds} immediately.
   */
  async submitBackgroundTool(
    toolName: string,
    args: Record<string, any>,
    sessionId: string,
  ): Promise<{ task_id: string; estimated_seconds: number } | null> {
    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/tool-submit`, {
        method: 'POST',
        headers: this.authHeaders(),
        body: JSON.stringify({
          tool_name: toolName,
          tool_args: args,
          session_id: sessionId,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('[VoiceKit] submitBackgroundTool failed:', error);
      return null;
    }
  }

  /**
   * Execute a tool via backend
   */
  async executeTool(toolName: string, args: Record<string, any>): Promise<ToolExecuteResponse> {
    const request: ToolExecuteRequest = {
      tool_name: toolName,
      tool_args: args,
    };

    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/tool-execute`, {
        method: 'POST',
        headers: this.authHeaders(),
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result: ToolExecuteResponse = await response.json();
      console.log('[VoiceKit] Tool execution result:', result);
      return result;
    } catch (error) {
      console.error('[VoiceKit] Tool execution failed:', error);
      return {
        success: false,
        result: '',
        error: error instanceof Error ? error.message : 'Unknown error',
      };
    }
  }

  /**
   * Delegate complex task to ProcessingEngine
   */
  async delegateTask(task: string, toolNames?: string[]): Promise<TaskDelegateResponse | null> {
    const request: TaskDelegateRequest = {
      task_description: task,
      tool_names: toolNames,
    };

    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/task-delegate`, {
        method: 'POST',
        headers: this.authHeaders(),
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result: TaskDelegateResponse = await response.json();
      console.log('[VoiceKit] Task delegated:', result);
      return result;
    } catch (error) {
      console.error('[VoiceKit] Task delegation failed:', error);
      return null;
    }
  }

  /**
   * Poll for pending task results (fallback if WebSocket unavailable)
   */
  async pollTasks(): Promise<TaskResult[]> {
    try {
      const response = await fetch(
        `${this.apiBaseUrl}/gemini-live/tasks?delivered=false`,
        { headers: this.authHeaders() }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      return data.pending_results || [];
    } catch (error) {
      console.error('[VoiceKit] Task polling failed:', error);
      return [];
    }
  }

  /**
   * Send follow-up answer to backend
   */
  async sendFollowUpResponse(responseText: string): Promise<boolean> {
    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/followup-response`, {
        method: 'POST',
        headers: this.authHeaders(),
        body: JSON.stringify({
          response_text: responseText,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      console.log('[VoiceKit] Follow-up response sent:', result);
      return result.success;
    } catch (error) {
      console.error('[VoiceKit] Follow-up response failed:', error);
      return false;
    }
  }

  /**
   * Check backend health
   */
  async checkHealth(): Promise<{ status: string; tool_count: number } | null> {
    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/health`, {
        headers: this.authHeaders(),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('[VoiceKit] Health check failed:', error);
      return null;
    }
  }

  /**
   * Fetch available tools from backend
   */
  async fetchTools(): Promise<any[]> {
    try {
      const response = await fetch(`${this.apiBaseUrl}/gemini-live/tools`, {
        headers: this.authHeaders(),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      console.log(`[VoiceKit] Fetched ${data.count} tools from backend`);
      return data.tools || [];
    } catch (error) {
      console.error('[VoiceKit] Failed to fetch tools:', error);
      return [];
    }
  }
}

// Singleton instance
let bridgeInstance: VoiceKitBridge | null = null;

export function initVoiceKitBridge(
  apiBaseUrl: string,
  getIdToken: () => string | null,
): VoiceKitBridge {
  if (bridgeInstance) {
    bridgeInstance.disconnect();
  }

  bridgeInstance = new VoiceKitBridge(apiBaseUrl, getIdToken);
  return bridgeInstance;
}

export function getVoiceKitBridge(): VoiceKitBridge | null {
  return bridgeInstance;
}

/**
 * IntegrationsPanel — OAuth connection status for Google and Basecamp.
 *
 * Shows connected/disconnected state for each integration with a Connect button.
 * On click: POSTs to /auth/{integration}/start → opens returned auth_url in a
 * new tab → polls /auth/status every 2s until connected → calls onAuthChanged().
 */

import { useState, useEffect, useRef, useCallback } from "react";
import "./IntegrationsPanel.scss";
import { useAuth } from "../contexts/AuthContext";

const VOICEKIT_API_URL =
  (process.env.REACT_APP_VOICEKIT_API_URL as string) || "http://localhost:8001";

// ── Types ──────────────────────────────────────────────────────────────────

type IntegrationStatus = "disconnected" | "pending" | "connected" | "error";

interface IntegrationInfo {
  status: IntegrationStatus;
  label?: string | null;
  error?: string | null;
}

interface AuthStatus {
  google: IntegrationInfo;
  basecamp: IntegrationInfo;
}

export interface IntegrationsPanelProps {
  onAuthChanged: () => void;
}

type Integration = keyof AuthStatus;

// ── Component ──────────────────────────────────────────────────────────────

export function IntegrationsPanel({ onAuthChanged }: IntegrationsPanelProps) {
  const { getIdToken } = useAuth();
  const [status, setStatus] = useState<AuthStatus>({
    google: { status: "disconnected" },
    basecamp: { status: "disconnected" },
  });
  const [collapsed, setCollapsed] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const authHeaders = useCallback((): Record<string, string> => {
    const token = getIdToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }, [getIdToken]);

  const fetchStatus = useCallback(async (): Promise<AuthStatus | null> => {
    try {
      const res = await fetch(`${VOICEKIT_API_URL}/gemini-live/auth/status`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const data: AuthStatus = await res.json();
        setStatus(data);
        return data;
      }
    } catch (e) {
      console.warn("[IntegrationsPanel] Failed to fetch auth status:", e);
    }
    return null;
  }, [authHeaders]);

  // Fetch status on mount
  useEffect(() => {
    fetchStatus();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchStatus]);

  const startPolling = useCallback(
    (integration: keyof AuthStatus) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        const data = await fetchStatus();
        if (!data) return;
        const s = data[integration].status;
        if (s === "connected") {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          onAuthChanged();
        } else if (s === "error") {
          clearInterval(pollRef.current!);
          pollRef.current = null;
        }
      }, 2000);
    },
    [fetchStatus, onAuthChanged]
  );

  const handleConnect = useCallback(
    async (integration: Integration) => {
      setStatus((prev) => ({
        ...prev,
        [integration]: { status: "pending" },
      }));
      try {
        const res = await fetch(
          `${VOICEKIT_API_URL}/gemini-live/auth/${integration}/start`,
          { method: "POST", headers: authHeaders() }
        );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${res.status}`);
        }
        const { auth_url } = await res.json();
        window.open(auth_url, "_blank");
        startPolling(integration);
      } catch (e) {
        console.error(`[IntegrationsPanel] Failed to start ${integration} auth:`, e);
        setStatus((prev) => ({
          ...prev,
          [integration]: { status: "error", error: String(e) },
        }));
      }
    },
    [startPolling]
  );

  const handleCancel = useCallback(
    async (integration: Integration) => {
      // Stop polling immediately
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      // Reset local state right away so the Connect button reappears
      setStatus((prev) => ({
        ...prev,
        [integration]: { status: "disconnected" },
      }));
      // Tell backend to close its callback server
      try {
        await fetch(`${VOICEKIT_API_URL}/gemini-live/auth/${integration}/cancel`, {
          method: "POST",
          headers: authHeaders(),
        });
      } catch (e) {
        // Best-effort — backend will timeout on its own anyway
      }
    },
    [authHeaders]
  );

  const handleDisconnect = useCallback(
    async (integration: Integration) => {
      // Optimistically update UI
      setStatus((prev) => ({
        ...prev,
        [integration]: { status: "disconnected" },
      }));
      try {
        await fetch(
          `${VOICEKIT_API_URL}/gemini-live/auth/${integration}/disconnect`,
          { method: "DELETE", headers: authHeaders() }
        );
        // Notify parent so tool list is refreshed
        onAuthChanged();
      } catch (e) {
        console.error(`[IntegrationsPanel] Disconnect ${integration} failed:`, e);
      }
    },
    [authHeaders, onAuthChanged]
  );

  return (
    <div className="integrations-panel">
      <div className="integrations-panel__header">
        <span>Integrations</span>
        <button
          className="integrations-panel__collapse-btn"
          onClick={() => setCollapsed(v => !v)}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "▲" : "▼"}
        </button>
      </div>
      {!collapsed && (
        <div className="integrations-panel__list">
          <IntegrationRow
            name="Google"
            info={status.google}
            onConnect={() => handleConnect("google")}
            onCancel={() => handleCancel("google")}
            onDisconnect={() => handleDisconnect("google")}
          />
          <IntegrationRow
            name="Basecamp"
            info={status.basecamp}
            onConnect={() => handleConnect("basecamp")}
            onCancel={() => handleCancel("basecamp")}
            onDisconnect={() => handleDisconnect("basecamp")}
          />
        </div>
      )}
    </div>
  );
}

// ── Row sub-component ──────────────────────────────────────────────────────

function IntegrationRow({
  name,
  info,
  onConnect,
  onCancel,
  onDisconnect,
}: {
  name: string;
  info: IntegrationInfo;
  onConnect: () => void;
  onCancel: () => void;
  onDisconnect: () => void;
}) {
  return (
    <div className="integrations-panel__row">
      <span
        className={`integrations-panel__dot integrations-panel__dot--${info.status}`}
        aria-hidden="true"
      />
      <span className="integrations-panel__name">{name}</span>
      <div className="integrations-panel__action">
        {info.status === "connected" && (
          <>
            <span className="integrations-panel__label" title={info.label || "Connected"}>
              {info.label || "Connected"}
            </span>
            <button
              className="integrations-panel__disconnect-btn"
              onClick={onDisconnect}
              title="Disconnect"
            >
              ✕
            </button>
          </>
        )}
        {info.status === "disconnected" && (
          <button className="integrations-panel__btn" onClick={onConnect}>
            Connect
          </button>
        )}
        {info.status === "pending" && (
          <>
            <span className="integrations-panel__pending">Waiting…</span>
            <button
              className="integrations-panel__cancel-btn"
              onClick={onCancel}
              title="Cancel"
            >
              ✕
            </button>
          </>
        )}
        {info.status === "error" && (
          <>
            <span
              className="integrations-panel__error-text"
              title={info.error || "Unknown error"}
            >
              Error
            </span>
            <button className="integrations-panel__btn" onClick={onConnect}>
              Retry
            </button>
          </>
        )}
      </div>
    </div>
  );
}

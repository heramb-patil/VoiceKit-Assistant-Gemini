/**
 * MCPServersPanel — lets users add/manage their own MCP tool servers.
 *
 * Two tabs:
 *   Popular — catalog of pre-built servers (GitHub, Slack, etc.)
 *   Custom  — manual command entry with tag-style args + KV env builder
 */

import { useState, useEffect, useCallback, useRef, KeyboardEvent } from "react";
import "./MCPServersPanel.scss";
import { useAuth } from "../contexts/AuthContext";

const VOICEKIT_API_URL =
  (process.env.REACT_APP_VOICEKIT_API_URL as string) || "http://localhost:8001";

// ── Types ──────────────────────────────────────────────────────────────────

interface MCPServer {
  id: string;
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  created_at: string | null;
  tool_count: number;
  tool_names?: string[];
}

interface CatalogAuthField {
  envKey: string;
  label: string;
  placeholder: string;
  helpUrl?: string;
  isPath?: boolean;
}

interface CatalogEntry {
  id: string;
  name: string;
  emoji: string;
  description: string;
  command: string;
  args: string[];
  authFields: CatalogAuthField[];
}

interface EnvRow {
  key: string;
  value: string;
}

// ── Catalog ────────────────────────────────────────────────────────────────

const MCP_CATALOG: CatalogEntry[] = [
  {
    id: "github",
    name: "GitHub",
    emoji: "🐙",
    description: "Read repos, issues, PRs, and code",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    authFields: [
      {
        envKey: "GITHUB_PERSONAL_ACCESS_TOKEN",
        label: "Personal Access Token",
        placeholder: "ghp_xxxxxxxxxxxx",
        helpUrl: "https://github.com/settings/tokens",
      },
    ],
  },
  {
    id: "filesystem",
    name: "Filesystem",
    emoji: "📁",
    description: "Read and write local files",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-filesystem"],
    authFields: [
      {
        envKey: "FILESYSTEM_PATH",
        label: "Folder path",
        placeholder: "/Users/you/Documents",
        isPath: true,
      },
    ],
  },
  {
    id: "slack",
    name: "Slack",
    emoji: "💬",
    description: "Read and post messages in Slack",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-slack"],
    authFields: [
      {
        envKey: "SLACK_BOT_TOKEN",
        label: "Bot Token",
        placeholder: "xoxb-xxxxxxxxxxxx",
        helpUrl: "https://api.slack.com/apps",
      },
      {
        envKey: "SLACK_TEAM_ID",
        label: "Team ID",
        placeholder: "T01234ABCDE",
      },
    ],
  },
  {
    id: "notion",
    name: "Notion",
    emoji: "📓",
    description: "Read and write Notion pages and databases",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-notion"],
    authFields: [
      {
        envKey: "NOTION_API_KEY",
        label: "Integration Token",
        placeholder: "secret_xxxxxxxxxxxx",
        helpUrl: "https://www.notion.so/my-integrations",
      },
    ],
  },
  {
    id: "linear",
    name: "Linear",
    emoji: "📐",
    description: "Manage Linear issues, projects, and cycles",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-linear"],
    authFields: [
      {
        envKey: "LINEAR_API_KEY",
        label: "API Key",
        placeholder: "lin_api_xxxxxxxxxxxx",
        helpUrl: "https://linear.app/settings/api",
      },
    ],
  },
  {
    id: "brave-search",
    name: "Brave Search",
    emoji: "🦁",
    description: "Search the web with Brave Search API",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-brave-search"],
    authFields: [
      {
        envKey: "BRAVE_API_KEY",
        label: "API Key",
        placeholder: "BSA...",
        helpUrl: "https://brave.com/search/api/",
      },
    ],
  },
  {
    id: "fetch",
    name: "Fetch (HTTP)",
    emoji: "🌐",
    description: "Fetch any URL and return its content",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-fetch"],
    authFields: [],
  },
  {
    id: "memory",
    name: "Memory",
    emoji: "🧠",
    description: "Persistent key-value memory across sessions",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-memory"],
    authFields: [],
  },
];

// ── Component ──────────────────────────────────────────────────────────────

export function MCPServersPanel() {
  const { getIdToken } = useAuth();
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [activeTab, setActiveTab] = useState<"popular" | "custom">("popular");

  // Catalog tab state
  const [expandedCatalogId, setExpandedCatalogId] = useState<string | null>(null);
  const [catalogAuthValues, setCatalogAuthValues] = useState<Record<string, Record<string, string>>>({});

  // Custom tab state
  const [customName, setCustomName] = useState("");
  const [customCommand, setCustomCommand] = useState("npx");
  const [argChips, setArgChips] = useState<string[]>([]);
  const [argInput, setArgInput] = useState("");
  const [envRows, setEnvRows] = useState<EnvRow[]>([{ key: "", value: "" }]);

  // Shared state
  const [addError, setAddError] = useState("");
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [tooltipServerId, setTooltipServerId] = useState<string | null>(null);

  const argInputRef = useRef<HTMLInputElement>(null);

  const authHeaders = useCallback((): Record<string, string> => {
    const token = getIdToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }, [getIdToken]);

  const fetchServers = useCallback(async () => {
    try {
      const res = await fetch(`${VOICEKIT_API_URL}/gemini-live/mcp-servers`, {
        headers: authHeaders(),
      });
      if (res.ok) setServers(await res.json());
    } catch (e) {
      console.warn("[MCPServersPanel] fetch failed:", e);
    }
  }, [authHeaders]);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  const handleToggle = async (server: MCPServer) => {
    try {
      await fetch(`${VOICEKIT_API_URL}/gemini-live/mcp-servers/${server.id}`, {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ enabled: !server.enabled }),
      });
      await fetchServers();
    } catch (e) {
      console.error("[MCPServersPanel] toggle failed:", e);
    }
  };

  const handleDelete = async (server: MCPServer) => {
    if (!window.confirm(`Remove MCP server "${server.name}"?`)) return;
    try {
      await fetch(`${VOICEKIT_API_URL}/gemini-live/mcp-servers/${server.id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      await fetchServers();
    } catch (e) {
      console.error("[MCPServersPanel] delete failed:", e);
    }
  };

  // ── Catalog tab ───────────────────────────────────────────────────────────

  const updateCatalogField = (entryId: string, envKey: string, value: string) => {
    setCatalogAuthValues(prev => ({
      ...prev,
      [entryId]: { ...(prev[entryId] || {}), [envKey]: value },
    }));
  };

  const submitCatalogEntry = async (entry: CatalogEntry) => {
    const values = catalogAuthValues[entry.id] || {};
    const args = [...entry.args];
    const env: Record<string, string> = {};

    for (const field of entry.authFields) {
      const val = (values[field.envKey] || "").trim();
      if (!val) {
        setAddError(`${field.label} is required.`);
        return;
      }
      if (field.isPath) {
        args.push(val);
      } else {
        env[field.envKey] = val;
      }
    }

    setAddError("");
    setLoading(true);
    try {
      const res = await fetch(`${VOICEKIT_API_URL}/gemini-live/mcp-servers`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name: entry.name, command: entry.command, args, env }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      setExpandedCatalogId(null);
      setCatalogAuthValues(prev => { const n = { ...prev }; delete n[entry.id]; return n; });
      await fetchServers();
    } catch (e) {
      setAddError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleCatalogConnect = (entry: CatalogEntry) => {
    setAddError("");
    if (entry.authFields.length === 0) {
      // No credentials needed — submit immediately
      submitCatalogEntry(entry);
    } else {
      setExpandedCatalogId(prev => (prev === entry.id ? null : entry.id));
    }
  };

  // ── Custom tab ────────────────────────────────────────────────────────────

  const handleArgKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === "Enter" || e.key === ",") && argInput.trim()) {
      e.preventDefault();
      const chip = argInput.trim().replace(/,$/, "");
      if (chip) {
        setArgChips(prev => [...prev, chip]);
        setArgInput("");
      }
    } else if (e.key === "Backspace" && !argInput && argChips.length > 0) {
      setArgChips(prev => prev.slice(0, -1));
    }
  };

  const removeArgChip = (index: number) => {
    setArgChips(prev => prev.filter((_, i) => i !== index));
  };

  const addEnvRow = () => setEnvRows(prev => [...prev, { key: "", value: "" }]);

  const updateEnvRow = (index: number, field: "key" | "value", value: string) => {
    setEnvRows(prev => prev.map((r, i) => i === index ? { ...r, [field]: value } : r));
  };

  const removeEnvRow = (index: number) => {
    setEnvRows(prev =>
      prev.length > 1 ? prev.filter((_, i) => i !== index) : [{ key: "", value: "" }]
    );
  };

  const handleCustomAdd = async () => {
    if (!customName.trim() || !customCommand.trim()) {
      setAddError("Name and command are required.");
      return;
    }
    setAddError("");
    setLoading(true);
    try {
      const env = Object.fromEntries(
        envRows.filter(r => r.key.trim()).map(r => [r.key.trim(), r.value])
      );
      const res = await fetch(`${VOICEKIT_API_URL}/gemini-live/mcp-servers`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ name: customName, command: customCommand, args: argChips, env }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      setCustomName("");
      setCustomCommand("npx");
      setArgChips([]);
      setArgInput("");
      setEnvRows([{ key: "", value: "" }]);
      await fetchServers();
    } catch (e) {
      setAddError(String(e));
    } finally {
      setLoading(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  const addedServerNames = new Set(servers.map(s => s.name));

  return (
    <div className="mcp-panel">
      <div className="mcp-panel__header">
        <span>MCP Servers</span>
        <div className="mcp-panel__header-right">
          {servers.length > 0 && !collapsed && (
            <span className="mcp-panel__count">{servers.length} added</span>
          )}
          <button
            className="mcp-panel__collapse-btn"
            onClick={() => setCollapsed(v => !v)}
            title={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? "▲" : "▼"}
          </button>
        </div>
      </div>

      {collapsed && (
        <div className="mcp-panel__collapsed-summary">
          {servers.length === 0 ? "No servers" : `${servers.length} server${servers.length > 1 ? "s" : ""}`}
        </div>
      )}

      {/* Tab bar + content + list (hidden when collapsed) */}
      {!collapsed && <>
      <div className="mcp-panel__tabs">
        <button
          className={`mcp-panel__tab${activeTab === "popular" ? " mcp-panel__tab--active" : ""}`}
          onClick={() => { setActiveTab("popular"); setAddError(""); }}
        >
          Popular
        </button>
        <button
          className={`mcp-panel__tab${activeTab === "custom" ? " mcp-panel__tab--active" : ""}`}
          onClick={() => { setActiveTab("custom"); setAddError(""); }}
        >
          Custom
        </button>
      </div>

      {/* Scrollable content */}
      <div className="mcp-panel__content">

        {/* ── Popular tab ── */}
        {activeTab === "popular" && (
          <div className="mcp-catalog">
            {MCP_CATALOG.map(entry => {
              const isExpanded = expandedCatalogId === entry.id;
              const isAdded = addedServerNames.has(entry.name);
              return (
                <div
                  key={entry.id}
                  className={`mcp-catalog__card${isExpanded ? " mcp-catalog__card--expanded" : ""}${isAdded ? " mcp-catalog__card--added" : ""}`}
                >
                  <div className="mcp-catalog__card-header">
                    <span className="mcp-catalog__emoji">{entry.emoji}</span>
                    <div className="mcp-catalog__meta">
                      <span className="mcp-catalog__name">{entry.name}</span>
                      <span className="mcp-catalog__desc">{entry.description}</span>
                    </div>
                    <button
                      className={`mcp-catalog__connect-btn${isAdded ? " mcp-catalog__connect-btn--added" : ""}`}
                      onClick={() => !isAdded && handleCatalogConnect(entry)}
                      disabled={isAdded || loading}
                    >
                      {isAdded ? "✓" : isExpanded ? "Cancel" : "Connect"}
                    </button>
                  </div>

                  {isExpanded && !isAdded && (
                    <div className="mcp-catalog__auth-form">
                      {entry.authFields.map(field => (
                        <div key={field.envKey} className="mcp-catalog__auth-field">
                          <div className="mcp-catalog__auth-label-row">
                            <label className="mcp-catalog__auth-label">{field.label}</label>
                            {field.helpUrl && (
                              <a
                                className="mcp-catalog__auth-help"
                                href={field.helpUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                Get token ↗
                              </a>
                            )}
                          </div>
                          <input
                            className="mcp-catalog__auth-input"
                            type={field.isPath ? "text" : "password"}
                            placeholder={field.placeholder}
                            value={catalogAuthValues[entry.id]?.[field.envKey] || ""}
                            onChange={e => updateCatalogField(entry.id, field.envKey, e.target.value)}
                            autoComplete="off"
                          />
                          {field.isPath && (
                            <span className="mcp-catalog__auth-hint">
                              Local folder path
                            </span>
                          )}
                        </div>
                      ))}
                      {addError && <div className="mcp-panel__error">{addError}</div>}
                      <button
                        className="mcp-catalog__auth-submit"
                        onClick={() => submitCatalogEntry(entry)}
                        disabled={loading}
                      >
                        {loading ? "Adding…" : "Add Server"}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ── Custom tab ── */}
        {activeTab === "custom" && (
          <div className="mcp-form">
            <div className="mcp-form__field">
              <label className="mcp-form__label">Name</label>
              <input
                className="mcp-form__input"
                placeholder="My Server"
                value={customName}
                onChange={e => setCustomName(e.target.value)}
              />
            </div>
            <div className="mcp-form__field">
              <label className="mcp-form__label">Command</label>
              <input
                className="mcp-form__input"
                placeholder="npx"
                value={customCommand}
                onChange={e => setCustomCommand(e.target.value)}
              />
            </div>
            <div className="mcp-form__field">
              <label className="mcp-form__label">Args</label>
              <div
                className="mcp-tag-input"
                onClick={() => argInputRef.current?.focus()}
              >
                {argChips.map((chip, i) => (
                  <span key={i} className="mcp-tag-input__chip">
                    {chip}
                    <button
                      className="mcp-tag-input__chip-remove"
                      onClick={e => { e.stopPropagation(); removeArgChip(i); }}
                    >×</button>
                  </span>
                ))}
                <input
                  ref={argInputRef}
                  className="mcp-tag-input__field"
                  placeholder={argChips.length === 0 ? "-y @mcp/server-… (Enter)" : ""}
                  value={argInput}
                  onChange={e => setArgInput(e.target.value)}
                  onKeyDown={handleArgKeyDown}
                  onBlur={() => {
                    if (argInput.trim()) {
                      setArgChips(prev => [...prev, argInput.trim()]);
                      setArgInput("");
                    }
                  }}
                />
              </div>
            </div>
            <div className="mcp-form__field">
              <label className="mcp-form__label">Environment</label>
              <div className="mcp-kv-builder">
                {envRows.map((row, i) => (
                  <div key={i} className="mcp-kv-builder__row">
                    <input
                      className="mcp-kv-builder__key"
                      placeholder="KEY"
                      value={row.key}
                      onChange={e => updateEnvRow(i, "key", e.target.value)}
                    />
                    <input
                      className="mcp-kv-builder__val"
                      placeholder="value"
                      value={row.value}
                      onChange={e => updateEnvRow(i, "value", e.target.value)}
                    />
                    <button
                      className="mcp-kv-builder__del"
                      onClick={() => removeEnvRow(i)}
                      title="Remove row"
                    >×</button>
                  </div>
                ))}
                <button className="mcp-kv-builder__add" onClick={addEnvRow}>
                  + Add Row
                </button>
              </div>
            </div>
            {addError && <div className="mcp-panel__error">{addError}</div>}
            <button
              className="mcp-form__submit"
              onClick={handleCustomAdd}
              disabled={loading}
            >
              {loading ? "Adding…" : "Add Server"}
            </button>
          </div>
        )}
      </div>

      {/* Server list — always visible below tabs */}
      <div className="mcp-panel__list">
        {servers.length === 0 && (
          <div className="mcp-panel__empty">No servers added yet.</div>
        )}
        {servers.map(srv => (
          <div
            key={srv.id}
            className={`mcp-panel__row${srv.enabled ? "" : " mcp-panel__row--disabled"}`}
            onMouseEnter={() => setTooltipServerId(srv.id)}
            onMouseLeave={() => setTooltipServerId(null)}
          >
            <span
              className={`mcp-panel__dot${srv.enabled ? " mcp-panel__dot--on" : ""}`}
              aria-hidden="true"
            />
            <div className="mcp-panel__info">
              <div className="mcp-panel__name-row">
                <span className="mcp-panel__name">{srv.name}</span>
                {srv.tool_count > 0 && (
                  <span className="mcp-panel__tool-badge">{srv.tool_count} tools</span>
                )}
              </div>
              <span className="mcp-panel__cmd">
                {srv.command} {srv.args.join(" ")}
              </span>
              {tooltipServerId === srv.id && srv.tool_names && srv.tool_names.length > 0 && (
                <div className="mcp-panel__tooltip">
                  {srv.tool_names.join(", ")}
                </div>
              )}
            </div>
            <div className="mcp-panel__actions">
              <button
                className="mcp-panel__toggle-btn"
                onClick={() => handleToggle(srv)}
                title={srv.enabled ? "Disable" : "Enable"}
              >
                {srv.enabled ? "On" : "Off"}
              </button>
              <button
                className="mcp-panel__del-btn"
                onClick={() => handleDelete(srv)}
                title="Remove"
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
      </>}
    </div>
  );
}

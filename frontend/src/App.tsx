/**
 * VoiceKit-Enhanced Gemini Live App
 *
 * Based on Google's multimodal-live-api-web-console with VoiceKit backend integration.
 *
 * Adds:
 * - VoiceKit bridge for backend orchestration
 * - Tool routing (backend vs local execution)
 * - Background task notifications
 */

import { useRef, useState, useEffect, useCallback } from "react";
import "./App.scss";
import { GoogleOAuthProvider, useGoogleOneTapLogin } from "@react-oauth/google";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { LiveAPIProvider, useLiveAPIContext } from "./contexts/LiveAPIContext";
import { TurnStateProvider, useTurnState, TurnState } from "./contexts/TurnStateContext";
import SidePanel from "./components/side-panel/SidePanel";
import { Altair } from "./components/altair/Altair";
import ControlTray from "./components/control-tray/ControlTray";
import { ToolRouter } from "./components/ToolRouter";
import { NotificationHandler } from "./components/NotificationHandler";
import { TaskPanel } from "./components/TaskPanel";
import { IntegrationsPanel } from "./components/IntegrationsPanel";
import { MCPServersPanel } from "./components/MCPServersPanel";
import { VoiceAura } from "./components/VoiceAura";
import { LoginPage } from "./components/LoginPage";
import cn from "classnames";
import { LiveClientOptions, TaskItem } from "./types";
import { VoiceKitBridge, initVoiceKitBridge } from "./lib/voicekit-bridge";
import { Modality, LiveConnectConfig, Type } from "@google/genai";

/**
 * Normalize JSON Schema types from lowercase (our backend format) to
 * the uppercase Type enum that Gemini Live requires ("STRING", "INTEGER", etc.)
 */
function normalizeSchema(schema: any): any {
  if (!schema || typeof schema !== "object") return schema;
  const result = { ...schema };

  // Convert type string to uppercase
  if (result.type && typeof result.type === "string") {
    const upper = result.type.toUpperCase();
    // Map to Type enum values
    const typeMap: Record<string, string> = {
      STRING: Type.STRING,
      INTEGER: Type.INTEGER,
      NUMBER: Type.NUMBER,
      BOOLEAN: Type.BOOLEAN,
      ARRAY: Type.ARRAY,
      OBJECT: Type.OBJECT,
    };
    result.type = typeMap[upper] || upper;
  }

  // Remove empty required arrays (Gemini rejects [])
  if (Array.isArray(result.required) && result.required.length === 0) {
    delete result.required;
  }

  // Remove parameters field entirely for tools with no properties
  if (result.properties && Object.keys(result.properties).length === 0) {
    delete result.properties;
  }

  // Recurse into properties
  if (result.properties) {
    result.properties = Object.fromEntries(
      Object.entries(result.properties).map(([k, v]) => [k, normalizeSchema(v)])
    );
  }

  return result;
}

const API_KEY = process.env.REACT_APP_GEMINI_API_KEY as string;
if (typeof API_KEY !== "string") {
  throw new Error("set REACT_APP_GEMINI_API_KEY in .env.local");
}

const VOICEKIT_API_URL = process.env.REACT_APP_VOICEKIT_API_URL || "http://localhost:8001";
const GOOGLE_CLIENT_ID = process.env.REACT_APP_GOOGLE_CLIENT_ID as string;

const apiOptions: LiveClientOptions = {
  apiKey: API_KEY,
};

// Default config for audio responses with UX-optimized instructions
const defaultConfig: LiveConnectConfig = {
  responseModalities: [Modality.AUDIO],
  speechConfig: {
    voiceConfig: { prebuiltVoiceConfig: { voiceName: "Aoede" } },
  },
  systemInstruction: {
    parts: [{
      text: `You are Kit, a voice assistant. You have access to the user's email, calendar, Basecamp, Google Drive, files, and web search.

HOW TOOL RESULTS WORK — This is critical. Tools are async. There are two stages:

  STAGE 1 — ACKNOWLEDGMENT: When you call a tool, the toolResponse is a short status phrase like "Checking your inbox.", "Fetching your check-ins.", "Looking up your calendar.", etc. This means the task has been submitted and is running. It is NOT the real data.
    - Speak one brief sentence to the user: "Checking your check-ins now." or "On it, fetching your emails."
    - DO NOT call the same tool again. DO NOT call any chained follow-up tool yet. The data is not ready.
    - WAIT.

  STAGE 2 — REAL RESULT: The actual data arrives as a text message in the format "[tool_name result] <data>". When you receive this:
    - This is the real answer. Speak it to the user naturally.
    - NOW you may call chained follow-up tools if needed (e.g. answer_basecamp_checkin after receiving the check-in data).

NEVER retry a tool that just returned a status phrase — it is already running. Retrying creates duplicate tasks.

CALL TOOLS IMMEDIATELY — Never say "let me check" or "one moment" before calling. Call the tool, then speak the Stage 1 acknowledgment phrase above.

CHAINING — only chain to the next tool AFTER you receive the [tool_name result] text with real data, not after the Stage 1 acknowledgment.
  "Read that email from X"       → search_emails (wait for result) → get_email_details (wait for result) → read aloud
  "Answer my Basecamp check-in"  → get_basecamp_checkins (wait for [result] text) → answer_basecamp_checkin → confirm aloud
  "Book a slot when I'm free"    → check_availability (wait for result) → create_event → confirm aloud
  "Post to project Y"            → list_basecamp_projects (wait for result) → post_basecamp_message → confirm aloud

SPEAK THE RESULT — read answers aloud naturally. Never say "see the panel", "I've added a summary", or reference any UI element.

STOP after answering. Don't call extra tools unless asked.

NEVER INVENT DATA — if a tool returns an error, read the error and stop. Never make up emails, events, or results. If Google or Basecamp isn't connected, say so.

DEEP RESEARCH — call deep_research immediately (no preamble). After the Stage 1 acknowledgment arrives, say "I've started that research, I'll let you know when it's done."

DRIVE ATTACHMENTS — send_email has an optional attach_drive_file parameter. Use it when the user says "email that research", "send the report", "attach that file". Pass the partial file name.`
    }]
  }
};

function AppContent() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoStream, setVideoStream] = useState<MediaStream | null>(null);
  const [backendStatus, setBackendStatus] = useState<"checking" | "connected" | "error">("checking");
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const bridgeRef = useRef<VoiceKitBridge | null>(null);

  // Auth — user identity comes from verified JWT, not env var
  const { user, getIdToken, signOut } = useAuth();

  // Get Gemini Live client from context
  const { client, setConfig, connected, connect, disconnect } = useLiveAPIContext();
  const shouldReconnectRef = useRef(false);

  // Get turn state manager
  const { transitionTo, getActiveToolCalls } = useTurnState();

  // Set default config with audio response on mount
  useEffect(() => {
    console.log('[VoiceKit] Setting default audio config');
    // Start with basic config, tools will be added later
    setConfig(defaultConfig);
  }, [setConfig]);

  // Wire turn state transitions from Gemini Live events
  useEffect(() => {
    if (!client) {
      return;
    }

    console.log('[TurnState] Wiring state transitions');

    // Connection events
    const handleOpen = () => {
      console.log('[TurnState] Connection opened');
      transitionTo(TurnState.IDLE, 'connection opened');
    };

    const handleClose = () => {
      console.warn('[TurnState] Connection closed');
      transitionTo(TurnState.DISCONNECTED, 'connection closed');
    };

    // Turn events
    const handleTurnComplete = () => {
      console.log('[TurnState] Turn complete');
      // Don't go IDLE if tool calls are still pending — Gemini fires turncomplete
      // immediately after toolcall, but our executor hasn't returned yet.
      // Transitioning to IDLE here would cause the coordinator to drop the response.
      if (getActiveToolCalls().size > 0) {
        console.log('[TurnState] Turn complete but tools pending — staying in TOOL_EXECUTING');
        return;
      }
      transitionTo(TurnState.IDLE, 'turn complete');
    };

    // Audio events (model speaking)
    const handleAudioStart = () => {
      console.log('[TurnState] Model started speaking');
      transitionTo(TurnState.MODEL_SPEAKING, 'audio started');
    };

    // Tool call events
    const handleToolCall = () => {
      console.log('[TurnState] Tool calls requested');
      transitionTo(TurnState.WAITING_TOOLS, 'tool call received');
    };

    // User speaking (if we can detect it)
    // Note: This might require additional event listeners from the audio input

    // Register event listeners
    client.on('open', handleOpen);
    client.on('close', handleClose);
    client.on('turncomplete', handleTurnComplete);
    client.on('audio', handleAudioStart);
    client.on('toolcall', handleToolCall);

    // Note: There's no explicit audio end event in Gemini Live,
    // so we rely on turncomplete to return to IDLE

    return () => {
      client.off('open', handleOpen);
      client.off('close', handleClose);
      client.off('turncomplete', handleTurnComplete);
      client.off('audio', handleAudioStart);
      client.off('toolcall', handleToolCall);
    };
  }, [client, transitionTo]); // transitionTo is now stable — this runs once per client

  // Apply a tool list to Gemini Live config (reused by initial load + re-auth)
  const applyTools = useCallback((tools: any[]) => {
    const functionDeclarations = tools.map((tool: any) => ({
      name: tool.name,
      description: tool.description || `Execute ${tool.name}`,
      ...(tool.parameters && Object.keys(tool.parameters.properties || {}).length > 0
        ? { parameters: normalizeSchema(tool.parameters) }
        : {}),
    }));
    console.log('[VoiceKit] Applying', functionDeclarations.length, 'tools to Gemini config');
    setConfig({
      ...defaultConfig,
      tools: [{ functionDeclarations }],
      systemInstruction: defaultConfig.systemInstruction,
    });
  }, [setConfig]);

  // After a disconnect triggered by auth change, reconnect once config is updated.
  // We can't call connect() immediately after setConfig() because connect() captures
  // config in a closure — the new config is only in the next render's connect().
  useEffect(() => {
    if (shouldReconnectRef.current && !connected) {
      shouldReconnectRef.current = false;
      connect().catch((e) => console.error('[VoiceKit] Reconnect after auth failed:', e));
    }
  }, [connected, connect]);

  // Called by IntegrationsPanel when auth succeeds — re-fetches tools so newly
  // available integrations are registered with the live Gemini session.
  const handleAuthChanged = useCallback(async () => {
    const bridge = bridgeRef.current;
    if (!bridge) return;
    try {
      const tools = await bridge.fetchTools();
      if (tools && tools.length > 0) {
        applyTools(tools);
        console.log('[VoiceKit] Tools reloaded after auth change:', tools.map((t: any) => t.name));
        // If a session is already active, disconnect now — the useEffect above will
        // reconnect once React has re-rendered with the updated config+tools.
        if (connected) {
          console.log('[VoiceKit] Session active — disconnecting to apply new tools');
          shouldReconnectRef.current = true;
          await disconnect();
        }
      }
    } catch (e) {
      console.error('[VoiceKit] Failed to reload tools after auth:', e);
    }
  }, [applyTools, connected, disconnect]);

  // Initialize VoiceKit bridge on mount (user.email comes from verified Google JWT)
  useEffect(() => {
    if (!user) return;

    console.log('[VoiceKit] Initializing bridge...');
    console.log('[VoiceKit] API URL:', VOICEKIT_API_URL);
    console.log('[VoiceKit] User Identity:', user.email);

    const bridge = initVoiceKitBridge(VOICEKIT_API_URL, getIdToken);
    bridgeRef.current = bridge;

    // Check backend health and fetch tools
    Promise.all([
      bridge.checkHealth(),
      bridge.fetchTools()
    ]).then(([health, tools]) => {
      if (health) {
        console.log('[VoiceKit] Backend connected:', health);
        setBackendStatus("connected");

        if (tools && tools.length > 0) {
          applyTools(tools);
          console.log('[VoiceKit] Total tools available:', tools.map((t: any) => t.name).join(', '));
        }
      } else {
        console.error('[VoiceKit] Backend health check failed');
        setBackendStatus("error");
      }
    }).catch(error => {
      console.error('[VoiceKit] Backend initialization failed:', error);
      setBackendStatus("error");
    });

    return () => {
      bridge.disconnect();
    };
  }, [applyTools, user, getIdToken]);

  return (
    <div className="App">
      <div className="streaming-console">
        <SidePanel />
        <main>
          <div className="main-app-area">
            {/* VoiceKit Backend Status Indicator + user info */}
            <div className={`voicekit-status voicekit-status-${backendStatus}`}>
              {backendStatus === "checking" && "⏳ Connecting to VoiceKit..."}
              {backendStatus === "connected" && `✓ Connected — ${user?.email}`}
              {backendStatus === "error" && "⚠ VoiceKit Backend Unavailable"}
              {backendStatus !== "checking" && (
                <button
                  className="voicekit-signout-btn"
                  onClick={signOut}
                  title="Sign out"
                >
                  Sign out
                </button>
              )}
            </div>

            {/* Voice state aura */}
            <VoiceAura />

            {/* Original Altair visualizations */}
            <Altair />

            {/* Video stream */}
            <video
              className={cn("stream", {
                hidden: !videoRef.current || !videoStream,
              })}
              ref={videoRef}
              autoPlay
              playsInline
            />
          </div>

          <ControlTray
            videoRef={videoRef}
            supportsVideo={true}
            onVideoStreamChange={setVideoStream}
            enableEditingSettings={true}
          >
            {/* Custom buttons can go here */}
          </ControlTray>
        </main>

        {/* VoiceKit Integration Components (invisible, logic-only) */}
        {client && backendStatus === "connected" && (
          <>
            <ToolRouter client={client} setTasks={setTasks} />
            <NotificationHandler client={client} />
          </>
        )}

        {/* Background task status panel */}
        <TaskPanel tasks={tasks} />

        {/* Integration auth status + connect buttons */}
        <IntegrationsPanel onAuthChanged={handleAuthChanged} />

        {/* Per-user MCP server management */}
        <MCPServersPanel />
      </div>
    </div>
  );
}

/** Renders the voice app only when the user is authenticated. */
function AuthGate() {
  const { user, signIn } = useAuth();

  // Silent background token refresh via Google One Tap.
  // auto_select: true = fully invisible if the user has one Google account active.
  // Fires shortly after mount and gets a fresh ID token (~1hr lifetime), keeping
  // the session alive without the user ever seeing a sign-in prompt.
  useGoogleOneTapLogin({
    onSuccess: signIn,
    onError: () => console.log('[Auth] One Tap silent re-auth unavailable'),
    auto_select: true,
    cancel_on_tap_outside: false,
  });

  if (!user) {
    return <LoginPage />;
  }
  return (
    <LiveAPIProvider options={apiOptions}>
      <TurnStateProvider>
        <AppContent />
      </TurnStateProvider>
    </LiveAPIProvider>
  );
}

function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID || ""}>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </GoogleOAuthProvider>
  );
}

export default App;

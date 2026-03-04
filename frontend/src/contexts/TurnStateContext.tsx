import React, { createContext, useContext, useRef, useState, useCallback } from 'react';

/**
 * Turn State Machine for Gemini Live Protocol Compliance
 *
 * Gemini Live operates on a strict turn-based protocol:
 * - Only send realtimeInput when state is IDLE
 * - Only send tool responses when state is WAITING_TOOLS or TOOL_EXECUTING
 * - Sending messages in wrong state causes code 1007 crashes
 */

export enum TurnState {
  IDLE = "IDLE",                     // Safe to send realtimeInput
  USER_SPEAKING = "USER_SPEAKING",   // User is talking, don't interrupt
  MODEL_THINKING = "MODEL_THINKING", // Model processing, don't interrupt
  MODEL_SPEAKING = "MODEL_SPEAKING", // Model speaking, don't interrupt
  WAITING_TOOLS = "WAITING_TOOLS",   // Waiting for tool responses (safe to send)
  TOOL_EXECUTING = "TOOL_EXECUTING", // Tools running, don't send input
  DISCONNECTED = "DISCONNECTED"      // Connection lost, abort all
}

interface StateHistoryEntry {
  state: TurnState;
  timestamp: number;
  reason?: string;
}

interface TurnStateContextValue {
  currentState: TurnState;
  transitionTo: (newState: TurnState, reason?: string) => void;
  canSendRealtimeInput: () => boolean;
  canSendToolResponse: () => boolean;
  getActiveToolCalls: () => Set<string>;
  addToolCall: (id: string) => void;
  removeToolCall: (id: string) => void;
  getStateHistory: () => StateHistoryEntry[];
  isConnected: () => boolean;
}

const TurnStateContext = createContext<TurnStateContextValue | null>(null);

export function TurnStateProvider({ children }: { children: React.ReactNode }) {
  const [currentState, setCurrentState] = useState<TurnState>(TurnState.IDLE);
  // Ref mirrors state but is always synchronously up-to-date.
  // This prevents stale closures in transitionTo during rapid audio events.
  const currentStateRef = useRef<TurnState>(TurnState.IDLE);
  const activeToolCalls = useRef<Set<string>>(new Set());
  const stateHistory = useRef<StateHistoryEntry[]>([]);

  /**
   * Transition to new state with validation and history tracking.
   * Uses ref for prev-state check so it never has a stale closure —
   * no dependency on currentState, so this function is stable forever.
   */
  const transitionTo = useCallback((newState: TurnState, reason?: string) => {
    const prevState = currentStateRef.current;  // always fresh

    // Skip redundant transitions
    if (prevState === newState) {
      return;
    }

    // Validate transition (log warnings for unexpected transitions)
    const isValidTransition = validateTransition(prevState, newState);
    if (!isValidTransition) {
      console.warn(`[TurnState] Unusual transition: ${prevState} → ${newState}${reason ? ` (${reason})` : ''}`);
    }

    console.log(`[TurnState] ${prevState} → ${newState}${reason ? ` (${reason})` : ''}`);

    // Record history (keep last 50 entries)
    stateHistory.current.push({
      state: newState,
      timestamp: Date.now(),
      reason
    });
    if (stateHistory.current.length > 50) {
      stateHistory.current.shift();
    }

    // Update ref synchronously before React commits state update
    currentStateRef.current = newState;
    setCurrentState(newState);
  }, []); // stable — never rebuilt

  /**
   * Check if we can send realtimeInput (only when IDLE).
   * Reads from ref so always reflects current state even mid-render.
   */
  const canSendRealtimeInput = useCallback((): boolean => {
    return currentStateRef.current === TurnState.IDLE;
  }, []); // stable

  /**
   * Check if we can send tool responses.
   * Reads from ref so always reflects current state even mid-render.
   */
  const canSendToolResponse = useCallback((): boolean => {
    const s = currentStateRef.current;
    return s === TurnState.WAITING_TOOLS || s === TurnState.TOOL_EXECUTING;
  }, []); // stable

  /**
   * Add tool call to active set and update state
   */
  const addToolCall = useCallback((id: string) => {
    console.log(`[TurnState] Adding tool call: ${id}`);
    activeToolCalls.current.add(id);
    if (currentStateRef.current !== TurnState.TOOL_EXECUTING) {
      transitionTo(TurnState.TOOL_EXECUTING, `tool ${id} started`);
    }
  }, [transitionTo]); // stable (transitionTo is stable)

  /**
   * Remove tool call from active set and update state if all complete
   */
  const removeToolCall = useCallback((id: string) => {
    console.log(`[TurnState] Removing tool call: ${id}`);
    activeToolCalls.current.delete(id);
    if (activeToolCalls.current.size === 0) {
      transitionTo(TurnState.MODEL_THINKING, 'all tools complete');
    }
  }, [transitionTo]); // stable

  /**
   * Get state history for debugging
   */
  const getStateHistory = useCallback((): StateHistoryEntry[] => {
    return [...stateHistory.current];
  }, []);

  /**
   * Check if connection is active
   */
  const isConnected = useCallback((): boolean => {
    return currentStateRef.current !== TurnState.DISCONNECTED;
  }, []); // stable

  const value: TurnStateContextValue = {
    currentState,
    transitionTo,
    canSendRealtimeInput,
    canSendToolResponse,
    getActiveToolCalls: () => activeToolCalls.current,
    addToolCall,
    removeToolCall,
    getStateHistory,
    isConnected
  };

  return (
    <TurnStateContext.Provider value={value}>
      {children}
    </TurnStateContext.Provider>
  );
}

/**
 * Hook to access turn state context
 */
export function useTurnState() {
  const context = useContext(TurnStateContext);
  if (!context) {
    throw new Error('useTurnState must be used within TurnStateProvider');
  }
  return context;
}

/**
 * Validate state transition (Finite State Automaton)
 * Returns true for expected transitions, false for unusual ones
 */
function validateTransition(from: TurnState, to: TurnState): boolean {
  const validTransitions: Record<TurnState, TurnState[]> = {
    [TurnState.IDLE]: [
      TurnState.USER_SPEAKING,
      TurnState.DISCONNECTED
    ],
    [TurnState.USER_SPEAKING]: [
      TurnState.MODEL_THINKING,
      TurnState.IDLE,
      TurnState.DISCONNECTED
    ],
    [TurnState.MODEL_THINKING]: [
      TurnState.MODEL_SPEAKING,
      TurnState.WAITING_TOOLS,
      TurnState.IDLE,
      TurnState.DISCONNECTED
    ],
    [TurnState.MODEL_SPEAKING]: [
      TurnState.IDLE,
      TurnState.DISCONNECTED
    ],
    [TurnState.WAITING_TOOLS]: [
      TurnState.TOOL_EXECUTING,
      TurnState.MODEL_THINKING,
      TurnState.DISCONNECTED
    ],
    [TurnState.TOOL_EXECUTING]: [
      TurnState.WAITING_TOOLS,
      TurnState.MODEL_THINKING,
      TurnState.IDLE,
      TurnState.DISCONNECTED
    ],
    [TurnState.DISCONNECTED]: [
      TurnState.IDLE // Reconnection
    ]
  };

  const allowedStates = validTransitions[from] || [];
  return allowedStates.includes(to);
}

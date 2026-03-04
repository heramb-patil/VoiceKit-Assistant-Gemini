/**
 * VoiceAura — animated visual indicator of the current conversation state.
 *
 * States:
 *   IDLE            → slow breathing pulse (dim blue)
 *   MODEL_SPEAKING  → sonar rings radiating outward (cyan)
 *   USER_SPEAKING   → reactive pulse (amber)
 *   THINKING/TOOLS  → spinning arcs (purple)
 *   DISCONNECTED    → grey, no animation
 */

import { useTurnState, TurnState } from "../contexts/TurnStateContext";
import "./VoiceAura.scss";

export function VoiceAura() {
  const { currentState } = useTurnState();

  let modifier = "";
  if (currentState === TurnState.MODEL_SPEAKING) modifier = "voice-aura--speaking";
  else if (currentState === TurnState.USER_SPEAKING) modifier = "voice-aura--listening";
  else if (
    currentState === TurnState.MODEL_THINKING ||
    currentState === TurnState.WAITING_TOOLS ||
    currentState === TurnState.TOOL_EXECUTING
  ) modifier = "voice-aura--thinking";
  else if (currentState === TurnState.IDLE) modifier = "voice-aura--idle";
  else modifier = "voice-aura--disconnected";

  return (
    <div className={`voice-aura ${modifier}`} aria-hidden="true">
      <div className="voice-aura__ring voice-aura__ring--1" />
      <div className="voice-aura__ring voice-aura__ring--2" />
      <div className="voice-aura__ring voice-aura__ring--3" />
      <div className="voice-aura__ring voice-aura__ring--4" />
      <div className="voice-aura__core" />
    </div>
  );
}

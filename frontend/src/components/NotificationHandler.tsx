/**
 * Notification Handler Component
 *
 * Receives and displays backend orchestration results via WebSocket.
 * Handles task completion notifications and follow-up questions.
 *
 * Crash-resistant features:
 * - Safe Notification Queue: State-aware delivery with priority
 * - Visual-first approach: All notifications shown visually immediately
 * - Voice notifications only when state allows
 */

import { useEffect, useState, useRef } from 'react';
import { getVoiceKitBridge, NotificationMessage } from '../lib/voicekit-bridge';
import { SafeNotificationQueue, SafeNotification } from '../lib/safe-notification-queue';
import { useTurnState } from '../contexts/TurnStateContext';
import './NotificationHandler.scss';

interface NotificationHandlerProps {
  client: any; // Gemini Live client
}

interface Notification {
  id: string;
  message: NotificationMessage;
  timestamp: Date;
}

/**
 * Build a short voice-friendly summary for a completed background task.
 * Full content is saved to file by the backend — voice only gets a teaser.
 */
function buildVoiceAnnouncement(msg: NotificationMessage): string {
  if (msg.type === 'task_complete' && msg.result) {
    // deep_research — extract topic, give short teaser only
    const researchMatch = msg.result.match(/Research complete on ['"]?([^'".\n]+)['"]?/i);
    if (researchMatch || msg.tool_name === 'deep_research') {
      const topic = researchMatch ? researchMatch[1].trim() : 'your topic';
      return `Also — your deep research on "${topic}" just finished and I've saved the full report to a file. Want me to go over the key findings?`;
    }

    // Generic background task — keep it short
    const brief = msg.result.replace(/\n/g, ' ').substring(0, 80);
    return `Just a heads-up — a background task finished: ${brief}. Let me know if you want details.`;
  }

  if (msg.type === 'followup_question' && msg.question) {
    return msg.question;
  }

  if (msg.type === 'error' && msg.error) {
    return `Heads up — a background task ran into an error: ${msg.error}`;
  }

  return '';
}

export function NotificationHandler({ client }: NotificationHandlerProps) {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [showPanel, setShowPanel] = useState(false);

  const processedTasksRef = useRef(new Set<string>());
  const safeQueueRef = useRef<SafeNotificationQueue | null>(null);

  // Use refs so the queue always reads live state without being recreated
  const { canSendRealtimeInput, currentState } = useTurnState();
  const canSendRef = useRef(canSendRealtimeInput);
  const currentStateRef = useRef(currentState);
  canSendRef.current = canSendRealtimeInput;
  currentStateRef.current = currentState;

  // Create queue once per client — stable, not recreated on every state change
  useEffect(() => {
    const bridge = getVoiceKitBridge();
    if (!bridge || !client) return;

    console.log('[NotificationHandler] Initialising');

    safeQueueRef.current = new SafeNotificationQueue(client, {
      canSendRealtimeInput: () => canSendRef.current(),
      getCurrentState: () => currentStateRef.current,
    });

    safeQueueRef.current.setVisualNotificationHandler((notification: SafeNotification) => {
      setNotifications((prev) => [{
        id: notification.id,
        message: { type: 'task_complete' as const, result: notification.content },
        timestamp: new Date(notification.timestamp),
      }, ...prev].slice(0, 10));
      setShowPanel(true);
    });

    const handleNotification = (msg: NotificationMessage) => {
      console.log('[NotificationHandler] Received:', msg.type, msg.task_id);

      if (msg.task_id && processedTasksRef.current.has(msg.task_id)) return;
      if (msg.task_id) processedTasksRef.current.add(msg.task_id);

      // Always show visually
      setNotifications((prev) => [{
        id: `${Date.now()}_${Math.random()}`,
        message: msg,
        timestamp: new Date(),
      }, ...prev].slice(0, 10));
      setShowPanel(true);

      const voiceText = buildVoiceAnnouncement(msg);
      if (!voiceText) return;

      // Priority: follow-up questions (2) > errors (3) > task completions (4)
      const priority =
        msg.type === 'followup_question' ? 2 :
        msg.type === 'error'             ? 3 : 4;

      safeQueueRef.current?.enqueue({
        priority,
        deliveryMode: 'both',   // Visual immediately + voice at next IDLE gap
        content: voiceText,
        timestamp: Date.now(),
      });
    };

    bridge.connectNotifications(handleNotification);

    const pollInterval = setInterval(async () => {
      const tasks = await bridge.pollTasks();
      tasks.forEach((task) => {
        if (!processedTasksRef.current.has(task.task_id) && task.result) {
          handleNotification({ type: 'task_complete', task_id: task.task_id, result: task.result });
        }
      });
    }, 3000);

    return () => {
      clearInterval(pollInterval);
      safeQueueRef.current?.clear();
    };
  }, [client]); // Only recreate when client changes, not on every state transition

  const clearNotifications = () => {
    setNotifications([]);
    setShowPanel(false);
  };

  const formatTimestamp = (date: Date): string => {
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  if (notifications.length === 0) {
    return null;
  }

  return (
    <div className={`notification-handler ${showPanel ? 'visible' : ''}`}>
      <div className="notification-header">
        <h3>Background Tasks</h3>
        <div className="notification-controls">
          <button
            onClick={() => setShowPanel(!showPanel)}
            className="toggle-button"
            title={showPanel ? 'Hide' : 'Show'}
          >
            {showPanel ? '▼' : '▲'}
          </button>
          <button onClick={clearNotifications} className="clear-button" title="Clear all">
            ×
          </button>
        </div>
      </div>

      {showPanel && (
        <div className="notification-list">
          {notifications.map((notification) => (
            <div key={notification.id} className={`notification notification-${notification.message.type}`}>
              <div className="notification-time">{formatTimestamp(notification.timestamp)}</div>
              <div className="notification-content">
                {notification.message.type === 'task_complete' && (
                  <>
                    <div className="notification-title">✓ Task Completed</div>
                    <div className="notification-body">{notification.message.result}</div>
                    {notification.message.task_id && (
                      <div className="notification-meta">Task ID: {notification.message.task_id}</div>
                    )}
                  </>
                )}

                {notification.message.type === 'followup_question' && (
                  <>
                    <div className="notification-title">❓ Question</div>
                    <div className="notification-body">{notification.message.question}</div>
                  </>
                )}

                {notification.message.type === 'error' && (
                  <>
                    <div className="notification-title">⚠️ Error</div>
                    <div className="notification-body">{notification.message.error}</div>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

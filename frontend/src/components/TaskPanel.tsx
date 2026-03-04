/**
 * TaskPanel — Background Task Status Panel
 *
 * Displays background tasks submitted to the SJF queue.
 * Sort order: running → pending (SJF by estimatedSeconds) → done/failed
 *
 * Visual features:
 * - Animated progress ring for running tasks
 * - Estimated time badge (~Xs) for pending tasks
 * - Collapsible panel
 */

import { useState, useMemo } from 'react';
import { TaskItem } from '../types';
import './TaskPanel.scss';

interface TaskPanelProps {
  tasks: TaskItem[];
}

// ── Progress Ring SVG ─────────────────────────────────────────────────────────

function ProgressRing({ size = 20, strokeWidth = 2.5 }: { size?: number; strokeWidth?: number }) {
  const r = (size - strokeWidth) / 2;
  const cx = size / 2;
  return (
    <svg
      className="task-panel__ring"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-label="Running"
    >
      {/* Track */}
      <circle cx={cx} cy={cx} r={r} fill="none" strokeWidth={strokeWidth} className="task-panel__ring-track" />
      {/* Animated arc */}
      <circle cx={cx} cy={cx} r={r} fill="none" strokeWidth={strokeWidth} className="task-panel__ring-arc" />
    </svg>
  );
}

// ── Status icon ───────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: TaskItem['status'] }) {
  if (status === 'running')  return <ProgressRing />;
  if (status === 'done')     return <span className="task-panel__icon task-panel__icon--done"   aria-label="Done">✓</span>;
  if (status === 'failed')   return <span className="task-panel__icon task-panel__icon--failed" aria-label="Failed">✕</span>;
  // pending
  return <span className="task-panel__icon task-panel__icon--pending" aria-label="Pending">·</span>;
}

// ── Sort helper ───────────────────────────────────────────────────────────────

function sortTasks(tasks: TaskItem[]): TaskItem[] {
  const order = { running: 0, pending: 1, done: 2, failed: 2 } as const;
  return [...tasks].sort((a, b) => {
    const oa = order[a.status] ?? 3;
    const ob = order[b.status] ?? 3;
    if (oa !== ob) return oa - ob;
    // Within pending: SJF (shorter first)
    if (a.status === 'pending' && b.status === 'pending') {
      return a.estimatedSeconds - b.estimatedSeconds;
    }
    // Within done/failed: newest first
    return (b.createdAt?.getTime() ?? 0) - (a.createdAt?.getTime() ?? 0);
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

export function TaskPanel({ tasks }: TaskPanelProps) {
  const [collapsed, setCollapsed] = useState(false);

  const sorted = useMemo(() => sortTasks(tasks), [tasks]);

  const runningCount = tasks.filter(t => t.status === 'running').length;
  const pendingCount = tasks.filter(t => t.status === 'pending').length;

  if (tasks.length === 0) return null;

  return (
    <div className={`task-panel ${collapsed ? 'task-panel--collapsed' : ''}`}>
      <div className="task-panel__header" onClick={() => setCollapsed(c => !c)}>
        <span className="task-panel__title">
          Background Tasks
          {(runningCount + pendingCount) > 0 && (
            <span className="task-panel__badge">{runningCount + pendingCount}</span>
          )}
        </span>
        <button
          className="task-panel__toggle"
          aria-label={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '▲' : '▼'}
        </button>
      </div>

      {!collapsed && (
        <ul className="task-panel__list">
          {sorted.map(task => (
            <li key={task.id} className={`task-panel__item task-panel__item--${task.status}`}>
              <div className="task-panel__item-left">
                <StatusIcon status={task.status} />
                <span className="task-panel__tool-name">{task.toolName}</span>
              </div>

              <div className="task-panel__item-right">
                {task.status === 'pending' && (
                  <span className="task-panel__est" title="Estimated duration">
                    ~{task.estimatedSeconds}s
                  </span>
                )}
                {task.status === 'running' && (
                  <span className="task-panel__running-label">running</span>
                )}
                {task.status === 'done' && task.result && (
                  <span
                    className="task-panel__result"
                    title={task.result}
                  >
                    {task.result.slice(0, 60)}{task.result.length > 60 ? '…' : ''}
                  </span>
                )}
                {task.status === 'failed' && task.result && (
                  <span className="task-panel__error" title={task.result}>
                    {task.result.slice(0, 60)}{task.result.length > 60 ? '…' : ''}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

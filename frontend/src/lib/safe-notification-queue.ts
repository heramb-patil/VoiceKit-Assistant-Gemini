/**
 * Safe Notification Queue - Priority Queue with State Guards
 *
 * Problem: Sending voice notifications during active turns causes
 * protocol violations and crashes.
 *
 * Solution: Priority queue that only delivers notifications when
 * state machine allows, with visual-first approach.
 *
 * Algorithm:
 * 1. Maintain min-heap sorted by (priority, timestamp)
 * 2. Background processor checks every 500ms:
 *    - If state is IDLE and deliveryMode includes voice:
 *      - Send via realtimeInput
 *    - Visual notifications always delivered immediately
 * 3. On delivery failure: increment retries, re-enqueue with lower priority
 * 4. On max retries: log error, discard notification
 */

export interface SafeNotification {
  id: string;
  priority: number; // 1 = highest, 5 = lowest
  deliveryMode: "visual" | "voice" | "both";
  content: string;
  timestamp: number;
  retries: number;
  maxRetries: number;
}

interface StateTracker {
  canSendRealtimeInput: () => boolean;
  getCurrentState: () => string;
}

export class SafeNotificationQueue {
  private queue: SafeNotification[] = [];
  private processing: boolean = false;
  private processingInterval: NodeJS.Timeout | null = null;
  private readonly maxRetries: number = 3;
  private readonly processingIntervalMs: number = 500;

  // Callbacks for UI updates
  private onVisualNotification?: (notification: SafeNotification) => void;

  constructor(
    private client: any,
    private stateTracker: StateTracker
  ) {}

  /**
   * Set callback for visual notifications
   */
  setVisualNotificationHandler(handler: (notification: SafeNotification) => void): void {
    this.onVisualNotification = handler;
  }

  /**
   * Enqueue notification with priority (min-heap insertion)
   */
  enqueue(notification: Omit<SafeNotification, 'id' | 'retries' | 'maxRetries'>): string {
    const id = `notif-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const full: SafeNotification = {
      ...notification,
      id,
      retries: 0,
      maxRetries: this.maxRetries
    };

    // Insert in priority order (lower number = higher priority)
    // Secondary sort by timestamp (older first)
    const insertIndex = this.queue.findIndex(n =>
      n.priority > full.priority ||
      (n.priority === full.priority && n.timestamp > full.timestamp)
    );

    if (insertIndex === -1) {
      this.queue.push(full);
    } else {
      this.queue.splice(insertIndex, 0, full);
    }

    console.log(`[SafeQueue] Enqueued (priority ${full.priority}, mode ${full.deliveryMode}): ${full.content.substring(0, 50)}...`);
    console.log(`[SafeQueue] Queue size: ${this.queue.length}`);

    // Deliver visual notifications immediately
    if (full.deliveryMode === "visual" || full.deliveryMode === "both") {
      this.deliverVisual(full);
    }

    // Start processing if not already running
    if (!this.processing) {
      this.startProcessing();
    }

    return id;
  }

  /**
   * Process queue (background loop)
   */
  private async startProcessing(): Promise<void> {
    if (this.processing) {
      return;
    }

    this.processing = true;
    console.log('[SafeQueue] Started processing loop');

    // Use interval instead of while loop to prevent blocking
    this.processingInterval = setInterval(async () => {
      if (this.queue.length === 0) {
        // No more items, stop processing
        this.stopProcessing();
        return;
      }

      // Process next item
      await this.processNext();
    }, this.processingIntervalMs);
  }

  /**
   * Stop processing loop
   */
  private stopProcessing(): void {
    if (this.processingInterval) {
      clearInterval(this.processingInterval);
      this.processingInterval = null;
    }
    this.processing = false;
    console.log('[SafeQueue] Stopped processing loop');
  }

  /**
   * Process next notification in queue
   */
  private async processNext(): Promise<void> {
    if (this.queue.length === 0) {
      return;
    }

    const notification = this.queue[0]; // Peek at highest priority

    // Visual-only notifications are already delivered, just remove
    if (notification.deliveryMode === "visual") {
      this.queue.shift();
      console.log('[SafeQueue] Visual notification already delivered, removed from queue');
      return;
    }

    // Try to deliver voice component
    const success = await this.deliverVoice(notification);

    if (success) {
      // Remove from queue
      this.queue.shift();
      console.log('[SafeQueue] Voice delivery successful, removed from queue');
    } else {
      // Increment retries
      notification.retries++;

      if (notification.retries >= notification.maxRetries) {
        console.warn(`[SafeQueue] Max retries (${notification.maxRetries}) reached, discarding:`, notification.content.substring(0, 50));
        this.queue.shift();
      } else {
        // Move to back of queue (lower priority)
        this.queue.shift();
        notification.priority = Math.min(notification.priority + 1, 5); // Degrade priority
        this.queue.push(notification);
        console.log(`[SafeQueue] Retry ${notification.retries}/${notification.maxRetries}, moved to back with priority ${notification.priority}`);
      }
    }
  }

  /**
   * Deliver visual notification immediately (always safe)
   */
  private deliverVisual(notification: SafeNotification): void {
    console.log('[SafeQueue] Delivering visual notification');
    if (this.onVisualNotification) {
      this.onVisualNotification(notification);
    }
  }

  /**
   * Deliver voice notification (state-aware)
   */
  private async deliverVoice(notification: SafeNotification): Promise<boolean> {
    // Check if state allows voice input
    if (!this.stateTracker.canSendRealtimeInput()) {
      console.log(`[SafeQueue] State not ready (${this.stateTracker.getCurrentState()}), deferring voice delivery`);
      return false;
    }

    try {
      console.log('[SafeQueue] Sending voice notification via realtimeInput');
      await this.client.send({ realtimeInput: { text: notification.content } });
      console.log('[SafeQueue] Voice delivery successful');
      return true;
    } catch (error) {
      console.error('[SafeQueue] Voice delivery failed:', error);
      return false;
    }
  }

  /**
   * Clear all notifications
   */
  clear(): void {
    const count = this.queue.length;
    this.queue = [];
    this.stopProcessing();
    console.log(`[SafeQueue] Cleared ${count} notifications`);
  }

  /**
   * Get queue size (for debugging)
   */
  getQueueSize(): number {
    return this.queue.length;
  }

  /**
   * Get queue contents (for debugging)
   */
  getQueue(): SafeNotification[] {
    return [...this.queue];
  }

  /**
   * Remove specific notification by ID
   */
  remove(id: string): boolean {
    const index = this.queue.findIndex(n => n.id === id);
    if (index !== -1) {
      this.queue.splice(index, 1);
      console.log(`[SafeQueue] Removed notification ${id}`);
      return true;
    }
    return false;
  }
}

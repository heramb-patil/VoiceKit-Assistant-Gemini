/**
 * Notification Queue - Proper implementation
 *
 * Uses a FIFO queue with turn-based delivery to prevent WebSocket crashes.
 * Only sends notifications when Gemini is idle (turn complete + connected).
 */

export interface QueuedNotification {
  id: string;
  text: string;
  priority: number; // Lower = higher priority
  timestamp: number;
  retries: number;
}

export class NotificationQueue {
  private queue: QueuedNotification[] = [];
  private isProcessing: boolean = false;
  private maxRetries: number = 3;
  private deliveryDelay: number = 1000; // 1 second between notifications

  constructor() {
    console.log('[NotificationQueue] Initialized');
  }

  /**
   * Add notification to queue
   */
  enqueue(text: string, priority: number = 5): string {
    const notification: QueuedNotification = {
      id: `notif-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      text,
      priority,
      timestamp: Date.now(),
      retries: 0,
    };

    // Insert in priority order (lower priority number = higher priority)
    const insertIndex = this.queue.findIndex(n => n.priority > priority);
    if (insertIndex === -1) {
      this.queue.push(notification);
    } else {
      this.queue.splice(insertIndex, 0, notification);
    }

    console.log(`[NotificationQueue] Enqueued (priority ${priority}):`, text.substring(0, 50));
    console.log(`[NotificationQueue] Queue size: ${this.queue.length}`);

    return notification.id;
  }

  /**
   * Start processing queue (non-blocking)
   */
  async startProcessing(
    client: any,
    isClientReady: () => boolean
  ): Promise<void> {
    if (this.isProcessing) {
      console.log('[NotificationQueue] Already processing');
      return;
    }

    this.isProcessing = true;
    console.log('[NotificationQueue] Started processing');

    while (this.queue.length > 0) {
      const notification = this.queue[0]; // Peek at next

      try {
        // Wait for client to be ready (turn complete + connected)
        await this.waitForClientReady(isClientReady, 30000); // 30s timeout

        // Send notification
        console.log('[NotificationQueue] Delivering:', notification.text.substring(0, 50));

        client.send({
          realtimeInput: {
            text: notification.text,
          },
        });

        // Successfully sent - remove from queue
        this.queue.shift();
        console.log('[NotificationQueue] Delivered successfully');

        // Add delay before next notification
        await this.delay(this.deliveryDelay);

      } catch (error: any) {
        console.error('[NotificationQueue] Delivery failed:', error);

        // Increment retries
        notification.retries++;

        if (notification.retries >= this.maxRetries) {
          // Max retries reached - discard
          console.warn('[NotificationQueue] Max retries reached, discarding:', notification.text.substring(0, 50));
          this.queue.shift();
        } else {
          // Move to back of queue for retry
          this.queue.shift();
          this.queue.push(notification);
          console.log(`[NotificationQueue] Retry ${notification.retries}/${this.maxRetries}, moved to back of queue`);
        }

        // Wait before retrying
        await this.delay(2000);
      }
    }

    this.isProcessing = false;
    console.log('[NotificationQueue] Processing complete');
  }

  /**
   * Wait for client to be ready
   */
  private async waitForClientReady(
    isClientReady: () => boolean,
    timeout: number
  ): Promise<void> {
    const startTime = Date.now();

    return new Promise((resolve, reject) => {
      const checkReady = () => {
        if (isClientReady()) {
          resolve();
        } else if (Date.now() - startTime > timeout) {
          reject(new Error('Timeout waiting for client to be ready'));
        } else {
          setTimeout(checkReady, 500); // Check every 500ms
        }
      };
      checkReady();
    });
  }

  /**
   * Simple delay helper
   */
  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * Get queue size
   */
  size(): number {
    return this.queue.length;
  }

  /**
   * Clear queue
   */
  clear(): void {
    this.queue = [];
    console.log('[NotificationQueue] Cleared');
  }

  /**
   * Get queue contents (for debugging)
   */
  getQueue(): QueuedNotification[] {
    return [...this.queue];
  }
}

/**
 * Tool Response Coordinator - Atomic Batch Sender
 *
 * Problem: Sending tool responses one-by-one during concurrent execution
 * causes race conditions and protocol violations.
 *
 * Solution: Batch concurrent tool responses and send atomically when
 * state machine allows.
 *
 * Algorithm:
 * 1. Collect concurrent responses in Map
 * 2. Set timer for maxBatchWait (100ms)
 * 3. When timer fires OR all expected tools complete:
 *    - Check if state allows tool response
 *    - Send all responses atomically via single sendToolResponse()
 * 4. Clear batch after successful send
 */

interface ToolResponse {
  id: string;
  name: string;
  response: any;
}

interface StateTracker {
  canSendToolResponse: () => boolean;
  getCurrentState: () => string;
}

export class ToolResponseCoordinator {
  private pendingResponses: Map<string, ToolResponse> = new Map();
  private batchTimeout: NodeJS.Timeout | null = null;
  private readonly maxBatchWait: number = 100; // 100ms
  private flushing: boolean = false;
  private retryCount: number = 0;
  private readonly maxRetries: number = 50; // Max 50 retries (5 seconds total)
  private batchStartTime: number = 0;

  constructor(
    private client: any,
    private stateTracker: StateTracker
  ) {}

  /**
   * Add response to batch (non-blocking)
   */
  addResponse(toolId: string, response: ToolResponse): void {
    this.pendingResponses.set(toolId, response);
    console.log(`[Coordinator] Added response for ${toolId}, batch size: ${this.pendingResponses.size}`);

    // Initialize batch start time if this is the first response
    if (this.batchStartTime === 0) {
      this.batchStartTime = Date.now();
    }

    // Start batch timer if not already running
    if (!this.batchTimeout && !this.flushing) {
      this.batchTimeout = setTimeout(() => this.flushBatch(), this.maxBatchWait);
    }
  }

  /**
   * Flush batch atomically (only when state allows)
   */
  private async flushBatch(): Promise<void> {
    // Prevent concurrent flush attempts
    if (this.flushing) {
      console.log('[Coordinator] Already flushing, skipping');
      return;
    }

    if (this.pendingResponses.size === 0) {
      this.batchTimeout = null;
      this.resetRetryState();
      return;
    }

    this.flushing = true;

    // Check if we can send tool responses
    if (!this.stateTracker.canSendToolResponse()) {
      // Check retry limits
      this.retryCount++;
      const elapsedMs = Date.now() - this.batchStartTime;
      const maxWaitMs = 10000; // 10 seconds max wait

      if (this.retryCount >= this.maxRetries || elapsedMs >= maxWaitMs) {
        console.error(
          `[Coordinator] Max retries/time exceeded, cancelling batch\n` +
          `  Retries: ${this.retryCount}/${this.maxRetries}\n` +
          `  Elapsed: ${elapsedMs}ms/${maxWaitMs}ms\n` +
          `  State: ${this.stateTracker.getCurrentState()}`
        );
        // Cancel the batch - state is stuck or model speaking too long
        this.pendingResponses.clear();
        this.batchTimeout = null;
        this.flushing = false;
        this.resetRetryState();
        return;
      }

      console.log(
        `[Coordinator] State not ready (${this.stateTracker.getCurrentState()}), ` +
        `retry ${this.retryCount}/${this.maxRetries}, elapsed ${elapsedMs}ms`
      );

      // Retry in 100ms
      this.batchTimeout = setTimeout(() => {
        this.flushing = false;
        this.flushBatch();
      }, 100);
      return;
    }

    // Convert Map to array for sendToolResponse
    const functionResponses = Array.from(this.pendingResponses.values());
    console.log(`[Coordinator] Flushing batch of ${functionResponses.length} responses`);

    try {
      await this.client.sendToolResponse({ functionResponses });
      console.log('[Coordinator] Batch sent successfully');

      // Clear batch
      this.pendingResponses.clear();
      this.batchTimeout = null;
      this.resetRetryState();
    } catch (error) {
      console.error('[Coordinator] Failed to send batch:', error);
      // On error, clear anyway to prevent infinite loop
      this.pendingResponses.clear();
      this.batchTimeout = null;
      this.resetRetryState();
    } finally {
      this.flushing = false;
    }
  }

  /**
   * Reset retry state
   */
  private resetRetryState(): void {
    this.retryCount = 0;
    this.batchStartTime = 0;
  }

  /**
   * Force immediate flush (for testing or urgent cases)
   */
  async forceFlush(): Promise<void> {
    if (this.batchTimeout) {
      clearTimeout(this.batchTimeout);
      this.batchTimeout = null;
    }
    await this.flushBatch();
  }

  /**
   * Cancel all pending (on disconnect/error)
   */
  cancelAll(): void {
    console.warn(`[Coordinator] Cancelling ${this.pendingResponses.size} pending responses`);
    this.pendingResponses.clear();
    if (this.batchTimeout) {
      clearTimeout(this.batchTimeout);
      this.batchTimeout = null;
    }
    this.flushing = false;
    this.resetRetryState();
  }

  /**
   * Remove specific response by ID (when tool call is cancelled)
   */
  removeResponse(toolId: string): boolean {
    const removed = this.pendingResponses.delete(toolId);
    if (removed) {
      console.log(`[Coordinator] Removed response for ${toolId}, batch size now: ${this.pendingResponses.size}`);

      // If batch is now empty, cancel the timer
      if (this.pendingResponses.size === 0 && this.batchTimeout) {
        clearTimeout(this.batchTimeout);
        this.batchTimeout = null;
        this.resetRetryState();
        console.log('[Coordinator] Batch now empty, cancelled timer');
      }
    }
    return removed;
  }

  /**
   * Get current batch size (for debugging)
   */
  getBatchSize(): number {
    return this.pendingResponses.size;
  }

  /**
   * Check if coordinator is currently flushing
   */
  isFlushing(): boolean {
    return this.flushing;
  }
}

/**
 * Circuit Breaker - Fail-Fast Pattern
 *
 * Problem: Connection failures during tool/notification delivery cause
 * cascading errors and resource leaks.
 *
 * Solution: Circuit breaker with exponential backoff prevents operations
 * when connection is unstable.
 *
 * Algorithm: Exponential backoff
 * 1. On connection close:
 *    - Set state = OPEN
 *    - Abort all pending operations
 *    - Calculate backoff: min(2^failureCount * baseTimeout, maxTimeout) ms
 * 2. After backoff delay:
 *    - Transition to HALF_OPEN
 *    - Allow one operation to test recovery
 * 3. On successful operation in HALF_OPEN:
 *    - Reset to CLOSED
 * 4. On failure in HALF_OPEN:
 *    - failureCount++, return to OPEN
 */

export enum CircuitState {
  CLOSED = "CLOSED",       // Normal operation
  OPEN = "OPEN",           // Connection failed, abort all
  HALF_OPEN = "HALF_OPEN"  // Attempting recovery
}

export interface CircuitBreakerStats {
  state: CircuitState;
  failureCount: number;
  lastFailureTime: number;
  backoffMs: number;
  nextRetryTime: number;
}

export class CircuitBreaker {
  private state: CircuitState = CircuitState.CLOSED;
  private failureCount: number = 0;
  private lastFailureTime: number = 0;
  private readonly baseTimeout: number = 5000; // 5 seconds base
  private readonly maxTimeout: number = 30000; // 30 seconds max
  private recoveryAttempts: number = 0;

  // Callbacks
  private onStateChange?: (state: CircuitState) => void;

  /**
   * Set callback for state changes
   */
  setStateChangeHandler(handler: (state: CircuitState) => void): void {
    this.onStateChange = handler;
  }

  /**
   * Check if operations should proceed
   */
  checkState(): boolean {
    if (this.state === CircuitState.CLOSED) {
      return true;
    }

    if (this.state === CircuitState.OPEN) {
      // Check if recovery timeout elapsed
      const elapsed = Date.now() - this.lastFailureTime;
      const backoffDelay = this.getBackoffDelay();

      if (elapsed >= backoffDelay) {
        console.log('[CircuitBreaker] Backoff period elapsed, attempting recovery (HALF_OPEN)');
        this.transitionTo(CircuitState.HALF_OPEN);
        return true; // Allow one operation to test
      }

      // Still in timeout
      const remainingMs = backoffDelay - elapsed;
      console.log(`[CircuitBreaker] Circuit OPEN, ${Math.ceil(remainingMs / 1000)}s until retry`);
      return false;
    }

    // HALF_OPEN: allow operations to test recovery
    return true;
  }

  /**
   * Called on connection close or operation failure
   */
  tripCircuit(reason?: string): void {
    const wasOpen = this.state === CircuitState.OPEN;

    this.failureCount++;
    this.lastFailureTime = Date.now();
    this.transitionTo(CircuitState.OPEN);

    const backoffDelay = this.getBackoffDelay();
    console.warn(
      `[CircuitBreaker] Circuit tripped (OPEN)${reason ? `: ${reason}` : ''}\n` +
      `  Failure count: ${this.failureCount}\n` +
      `  Backoff delay: ${backoffDelay / 1000}s`
    );

    // Only increment recovery attempts if we were already open
    // (indicates repeated failures during recovery)
    if (wasOpen) {
      this.recoveryAttempts++;
      console.warn(`[CircuitBreaker] Recovery attempt ${this.recoveryAttempts} failed`);
    }
  }

  /**
   * Called after successful operation in HALF_OPEN
   */
  reset(): void {
    console.log('[CircuitBreaker] Circuit reset (CLOSED) - connection recovered');
    this.state = CircuitState.CLOSED;
    this.failureCount = 0;
    this.recoveryAttempts = 0;
    this.transitionTo(CircuitState.CLOSED);
  }

  /**
   * Force state change (for testing or manual recovery)
   */
  forceState(newState: CircuitState): void {
    console.log(`[CircuitBreaker] Forced state change: ${this.state} → ${newState}`);
    this.transitionTo(newState);
  }

  /**
   * Transition to new state and notify listeners
   */
  private transitionTo(newState: CircuitState): void {
    const prevState = this.state;
    this.state = newState;

    if (this.onStateChange && prevState !== newState) {
      this.onStateChange(newState);
    }
  }

  /**
   * Calculate exponential backoff delay
   * Formula: min(2^failureCount * baseTimeout, maxTimeout)
   */
  private getBackoffDelay(): number {
    const exponentialDelay = Math.pow(2, this.failureCount - 1) * this.baseTimeout;
    return Math.min(exponentialDelay, this.maxTimeout);
  }

  /**
   * Get current statistics (for debugging/monitoring)
   */
  getStats(): CircuitBreakerStats {
    return {
      state: this.state,
      failureCount: this.failureCount,
      lastFailureTime: this.lastFailureTime,
      backoffMs: this.getBackoffDelay(),
      nextRetryTime: this.lastFailureTime + this.getBackoffDelay()
    };
  }

  /**
   * Get current state
   */
  getState(): CircuitState {
    return this.state;
  }

  /**
   * Check if circuit is open
   */
  isOpen(): boolean {
    return this.state === CircuitState.OPEN;
  }

  /**
   * Check if circuit is closed (healthy)
   */
  isClosed(): boolean {
    return this.state === CircuitState.CLOSED;
  }

  /**
   * Get time until next retry (milliseconds)
   */
  getTimeUntilRetry(): number {
    if (this.state !== CircuitState.OPEN) {
      return 0;
    }
    const elapsed = Date.now() - this.lastFailureTime;
    const remaining = this.getBackoffDelay() - elapsed;
    return Math.max(0, remaining);
  }

  /**
   * Get recovery attempts count
   */
  getRecoveryAttempts(): number {
    return this.recoveryAttempts;
  }
}

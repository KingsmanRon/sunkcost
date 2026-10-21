// Bounded event buffer with a timer-driven flusher (§4 C2/C3), mirroring sunkcost.buffer.
//
// put() never blocks and never throws: when the buffer is full the oldest event is dropped and
// counted, and the count travels in the next batch's dropped_events. The flusher sends at most one
// batch at a time, every intervalMs or as soon as batchSize events are queued, and backs off with
// jitter (capped at 60 s) after a failure. Its timers are unref'd, so they never keep a process
// alive; the client flushes at exit instead.

import { formatTimestampUs } from "./core/time.js";
import type { Transport } from "./transport.js";
import { SDK_NAME, SDK_VERSION } from "./version.js";
import type { IngestEvent } from "./wire.js";

export const BASE_BACKOFF_S = 0.5;
export const MAX_BACKOFF_S = 60;

const encoder = new TextEncoder();

/** flush() waits on ordinary (ref'd) timers: its caller, or the exit hook, asked to wait, up to a bound. */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface BufferOptions {
  capacity?: number;
  batchSize?: number;
  intervalMs?: number;
  clockUs?: () => number;
  random?: () => number;
}

export class EventBuffer {
  private readonly queue: IngestEvent[] = [];
  private pending: IngestEvent[] | null = null; // batch taken but not yet accepted
  private dropped = 0; // dropped since the last accepted batch
  private attempt = 0;
  private nextSendMs = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending: Promise<void> | null = null;
  private stopped = false;
  private readonly capacity: number;
  private readonly batchSize: number;
  private readonly intervalMs: number;
  private readonly clockUs: () => number;
  private readonly random: () => number;
  sentEvents = 0;
  droppedTotal = 0;
  failedSends = 0;

  constructor(
    private readonly transport: Transport,
    options: BufferOptions = {},
  ) {
    this.capacity = options.capacity ?? 10_000;
    this.batchSize = options.batchSize ?? 500;
    this.intervalMs = options.intervalMs ?? 2_000;
    this.clockUs = options.clockUs ?? (() => Date.now() * 1000);
    this.random = options.random ?? Math.random;
  }

  start(): void {
    this.schedule(this.intervalMs);
  }

  // -- producer side (agent code) ------------------------------------------------------------

  put(event: IngestEvent): void {
    if (this.queue.length >= this.capacity) {
      this.queue.shift();
      this.dropped += 1;
      this.droppedTotal += 1;
    }
    this.queue.push(event);
    if (this.queue.length >= this.batchSize && !this.stopped) this.schedule(0);
  }

  get queued(): number {
    return this.queue.length + (this.pending?.length ?? 0);
  }

  // -- flusher ---------------------------------------------------------------------------------

  private schedule(delayMs: number): void {
    if (this.stopped) return;
    if (this.timer !== null) {
      if (delayMs > 0) return; // a tick is already due sooner or at the interval
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.tick();
    }, delayMs);
    this.timer.unref();
  }

  private async tick(): Promise<void> {
    await this.flushOnce();
    this.schedule(this.intervalMs);
  }

  /** Send queued batches until the queue is empty or a send has to back off. */
  private flushOnce(): Promise<void> {
    this.sending ??= this.drain().finally(() => {
      this.sending = null;
    });
    return this.sending;
  }

  private async drain(): Promise<void> {
    for (;;) {
      if (Date.now() < this.nextSendMs) return;
      if (this.pending === null) {
        if (this.queue.length === 0) return;
        this.pending = this.queue.splice(0, Math.min(this.batchSize, this.queue.length));
      }
      const batch = this.pending;
      const dropped = this.dropped;
      const body = encoder.encode(
        JSON.stringify({
          schema_version: "1",
          sent_at: formatTimestampUs(this.clockUs()),
          sdk: { name: SDK_NAME, version: SDK_VERSION },
          dropped_events: dropped,
          events: batch,
        }),
      );
      let status: string;
      let retryAfterS: number | null = null;
      try {
        const result = await this.transport.sendBatch(body);
        status = result.status;
        retryAfterS = result.retryAfterS ?? null;
      } catch {
        status = "retry"; // a transport bug must never kill the flusher
      }
      if (status === "ok") {
        this.dropped -= dropped;
        this.sentEvents += batch.length;
        this.pending = null;
        this.attempt = 0;
      } else if (status === "drop") {
        this.dropped += batch.length;
        this.droppedTotal += batch.length;
        this.failedSends += 1;
        this.pending = null;
        this.attempt = 0;
      } else {
        this.failedSends += 1;
        this.attempt += 1;
        const cap = Math.min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2 ** this.attempt);
        let delay = this.random() * cap;
        if (retryAfterS !== null) delay = Math.max(delay, Math.min(retryAfterS, MAX_BACKOFF_S));
        this.nextSendMs = Date.now() + delay * 1000;
        return;
      }
    }
  }

  // -- lifecycle -------------------------------------------------------------------------------

  /**
   * Send now and wait up to timeoutMs for the buffer to drain (retrying with backoff). The bound
   * holds even while a send hangs: the wait races the deadline instead of the request.
   */
  async flush(timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) return this.queued === 0;
      await Promise.race([this.flushOnce(), sleep(remaining)]);
      if (this.queued === 0) return true;
      const now = Date.now();
      if (now >= deadline) return false;
      await sleep(Math.max(10, Math.min(this.nextSendMs - now, deadline - now, 50)));
    }
  }

  /** Flush (bounded) and stop the timer. Returns whether everything was delivered. */
  async close(timeoutMs: number): Promise<boolean> {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.stopped = true;
    return this.flush(timeoutMs);
  }
}

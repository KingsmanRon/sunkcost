// The public client: Sunkcost and Run (§4 C2/C3), mirroring sunkcost.client in camelCase.
//
// Every public method is fail-open. An internal exception is counted in stats.sdkErrors and
// check() then returns CONTINUE with reason sdk_error; nothing throws into agent code. check() is
// synchronous and reads only in-memory state: the run's feature state and the bundle manager's
// current bundle and override. It never performs I/O.

import { randomBytes } from "node:crypto";
import { inspect } from "node:util";

import { EventBuffer } from "./buffer.js";
import { loadTrustedKeys, type Bundle } from "./bundle.js";
import { BundleManager, type Logger } from "./bundles.js";
import { argsHash } from "./core/argsHash.js";
import { CanonicalJSONError } from "./core/canonical.js";
import { FeatureState, type StepRecord } from "./core/features.js";
import { isHoldout } from "./core/holdout.js";
import { formatTimestampUs, utcDate } from "./core/time.js";
import { NO_BUNDLE, SDK_ERROR, decide, effectiveMode, type Decision } from "./decision.js";
import { RunAborted } from "./errors.js";
import { isUuid7, uuid7 } from "./ids.js";
import { DEFAULT_TRUSTED_KEYS } from "./keys.js";
import { moneyStr, toMoney, type Nanos } from "./money.js";
import { PriceTable } from "./prices.js";
import { HttpTransport, type Transport } from "./transport.js";
import type { IngestEvent, Outcome } from "./wire.js";

const WORKLOAD = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SOURCE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
const RUN_STATUSES = ["completed", "aborted", "errored"] as const;
const OUTCOMES: readonly string[] = ["success", "failure", "abandoned"];
const MAX_TEXT = 256;
const MAX_EXTERNAL_REF = 128;
const MAX_TOKENS = 1_000_000_000;
const MAX_LATENCY_MS = 86_400_000;
const MAX_STEPS = 1_000_000;

export type RunStatus = (typeof RUN_STATUSES)[number];

/** Microseconds since the epoch. Replaceable in tests through the client's `clockUs` option. */
const systemClockUs = (): number => Date.now() * 1000;

/** Wire-safe identifier text: no NUL, 1..limit UTF-16 units; anything else is null. */
function text(value: unknown, limit = MAX_TEXT): string | null {
  let s: string;
  if (typeof value === "string") s = value;
  else if (typeof value === "number" || typeof value === "bigint" || typeof value === "boolean") s = String(value);
  else return null;
  s = s.replaceAll("\u0000", "").slice(0, limit);
  return s === "" ? null : s;
}

function count(value: unknown, high: number): number {
  if (typeof value !== "number" || !Number.isInteger(value)) return 0;
  return Math.min(Math.max(value, 0), high);
}

export interface Stats {
  sdkErrors: number;
  runsStarted: number;
  checks: number;
  aborts: number;
  eventsDiscardedOffline: number;
}

export interface SunkcostOptions {
  /** Default: env SUNKCOST_API_KEY. */
  apiKey?: string | undefined;
  /** Default: env SUNKCOST_ENDPOINT. */
  endpoint?: string | undefined;
  /** {keyId: base64 Ed25519 public key}. Default: the keys pinned in this package. */
  trustedKeys?: Readonly<Record<string, string>> | undefined;
  /** Default: env SUNKCOST_ARGS_SALT, else a random per-process salt. Never sent anywhere. */
  argsHashSalt?: string | undefined;
  workloads?: readonly string[];
  /** Default: env SUNKCOST_CACHE_DIR. */
  cacheDir?: string | undefined;
  bufferSize?: number;
  flushIntervalMs?: number;
  flushBatchSize?: number;
  bundleRefreshMs?: number;
  closeTimeoutMs?: number;
  transport?: Transport;
  prices?: PriceTable;
  /** Default: console. Pass null to silence the SDK. */
  logger?: Logger | null;
  clockUs?: () => number;
}

export interface StepInput {
  kind: "model" | "tool";
  model?: string | null;
  inputTokens?: number;
  outputTokens?: number;
  /** USD; computed from the price table when omitted. Use a string for exact amounts. */
  costUsd?: string | number | bigint | null;
  toolName?: string | null;
  /** HMAC-hashed here; never sent. */
  toolArgs?: unknown;
  error?: boolean;
  latencyMs?: number | null;
}

const consoleLogger: Logger = { warn: (m) => { console.warn(m); } };

/** One client per process. Each Run is meant for one agent at a time. */
export class Sunkcost {
  readonly stats: Stats = { sdkErrors: 0, runsStarted: 0, checks: 0, aborts: 0, eventsDiscardedOffline: 0 };
  readonly bundles: BundleManager;
  /** @internal */ readonly salt: string;
  /** @internal */ readonly prices: PriceTable;
  /** @internal */ readonly clockUs: () => number;
  private readonly buffer: EventBuffer | null = null;
  private readonly transport: Transport | null;
  private readonly closeTimeoutMs: number;
  private closed = false;

  constructor(options: SunkcostOptions = {}) {
    const logger = options.logger === undefined ? consoleLogger : options.logger;
    const log = logger ?? undefined;
    this.closeTimeoutMs = options.closeTimeoutMs ?? 3_000;
    this.clockUs = options.clockUs ?? systemClockUs;
    const apiKey = options.apiKey ?? process.env.SUNKCOST_API_KEY;
    const endpoint = options.endpoint ?? process.env.SUNKCOST_ENDPOINT;
    let salt = options.argsHashSalt ?? process.env.SUNKCOST_ARGS_SALT;
    if (!salt) {
      // Hashes stay comparable within this process, which is all the features need.
      log?.warn("sunkcost: no argsHashSalt (SUNKCOST_ARGS_SALT); using a random per-process salt");
      salt = randomBytes(32).toString("hex");
    }
    this.salt = salt;
    const keys = loadTrustedKeys(options.trustedKeys ?? DEFAULT_TRUSTED_KEYS);
    let transport = options.transport ?? null;
    if (transport === null && apiKey && endpoint) transport = new HttpTransport(endpoint, apiKey);
    else if (transport === null) {
      log?.warn("sunkcost: no apiKey/endpoint; running offline (decisions only, events discarded)");
    }
    this.transport = transport;
    this.prices = options.prices ?? PriceTable.bundled();
    const cacheDir = options.cacheDir ?? process.env.SUNKCOST_CACHE_DIR ?? null;
    this.bundles = new BundleManager(transport, keys, {
      refreshMs: options.bundleRefreshMs ?? 300_000,
      cacheDir: cacheDir === "" ? null : cacheDir,
      clockUs: this.clockUs,
      ...(log ? { logger: log } : {}),
    });
    if (transport !== null) {
      this.buffer = new EventBuffer(transport, {
        capacity: options.bufferSize ?? 10_000,
        batchSize: options.flushBatchSize ?? 500,
        intervalMs: options.flushIntervalMs ?? 2_000,
        clockUs: this.clockUs,
      });
      this.buffer.start();
    }
    for (const workload of options.workloads ?? []) this.bundles.register(workload);
    this.bundles.start();
    registerExit(this);
  }

  /** @internal */
  emit(event: IngestEvent): void {
    if (this.buffer === null || this.closed) {
      this.stats.eventsDiscardedOffline += 1;
      return;
    }
    this.buffer.put(event);
  }

  startRun(workload: string, options: { runId?: string | null; externalRef?: string | null } = {}): Run {
    try {
      return new Run(this, workload, options.runId ?? null, options.externalRef ?? null);
    } catch {
      this.stats.sdkErrors += 1;
      return Run.degraded(this);
    }
  }

  /** Report a run's outcome after the fact (the API's /v1/outcomes also accepts it). */
  outcome(runId: string, outcome: Outcome, source = "sdk"): void {
    try {
      if (!isUuid7(runId) || !OUTCOMES.includes(outcome) || !SOURCE.test(source)) {
        throw new RangeError(`invalid outcome report for run ${runId}`);
      }
      this.emit({ type: "outcome", run_id: runId, outcome, source, reported_at: formatTimestampUs(this.clockUs()) });
    } catch {
      this.stats.sdkErrors += 1;
    }
  }

  /** Fetch every registered workload's bundle now (for scripts and tests). */
  async refreshBundles(): Promise<void> {
    await Promise.all(this.bundles.workloads.map((w) => this.bundles.refresh(w)));
  }

  /** Send buffered events now; resolves false if they could not all be delivered in time. */
  flush(timeoutMs = 3_000): Promise<boolean> {
    return this.buffer === null ? Promise.resolve(true) : this.buffer.flush(timeoutMs);
  }

  get droppedEvents(): number {
    return this.buffer?.droppedTotal ?? 0;
  }

  /** @internal */
  get eventBuffer(): EventBuffer | null {
    return this.buffer;
  }

  /** Flush (bounded, default 3 s) and stop background timers. Safe to call twice. */
  async close(timeoutMs?: number): Promise<boolean> {
    if (this.closed) return true;
    this.closed = true;
    unregisterExit(this);
    let ok = true;
    try {
      this.bundles.stop();
      if (this.buffer !== null) ok = await this.buffer.close(timeoutMs ?? this.closeTimeoutMs);
      this.transport?.close();
    } catch {
      this.stats.sdkErrors += 1;
      ok = false;
    }
    return ok;
  }
}

/** One agent run. Call check() before each step and recordStep() after it. */
export class Run {
  readonly runId: string;
  readonly workload: string;
  readonly externalRef: string | null;
  readonly startedAtUs: number;
  /** The bundle this run is evaluated against: the one active when it started (ADR-0002 §8). */
  readonly bundle: Bundle | null;
  readonly holdout: boolean;
  private readonly client: Sunkcost;
  private readonly degradedRun: boolean;
  private readonly state: FeatureState;
  private lastDecision: Decision | null = null;
  private total: Nanos = 0n;
  private wouldAbortStep: number | null = null;
  private costThroughWouldAbort: Nanos | null = null;
  private abortedStep: number | null = null;
  private ended = false;

  /** @internal */
  constructor(
    client: Sunkcost,
    workload: string,
    runId: string | null,
    externalRef: string | null,
    degraded = false,
  ) {
    this.client = client;
    this.degradedRun = degraded;
    this.startedAtUs = client.clockUs();
    this.state = new FeatureState(this.startedAtUs);
    if (degraded) {
      this.workload = "";
      this.runId = uuid7();
      this.externalRef = null;
      this.bundle = null;
      this.holdout = false;
      return;
    }
    if (typeof workload !== "string" || !WORKLOAD.test(workload)) {
      throw new RangeError(`invalid workload name ${workload}`);
    }
    this.workload = workload;
    let id = runId === null ? null : runId.toLowerCase();
    if (id !== null && !isUuid7(id)) {
      client.stats.sdkErrors += 1;
      id = null;
    }
    this.runId = id ?? uuid7(Math.floor(this.startedAtUs / 1000));
    this.externalRef = text(externalRef, MAX_EXTERNAL_REF);
    client.bundles.register(workload);
    const [bundle, override] = client.bundles.get(workload);
    this.bundle = bundle;
    this.holdout = bundle !== null && isHoldout(this.runId, bundle.holdoutRate);
    client.stats.runsStarted += 1;
    client.emit({
      type: "run_start",
      run_id: this.runId,
      workload,
      external_ref: this.externalRef,
      started_at: formatTimestampUs(this.startedAtUs),
      bundle_id: bundle?.bundleId ?? null,
      bundle_version: bundle?.version ?? null,
      mode: effectiveMode(bundle, override, this.startedAtUs),
      holdout: this.holdout,
    });
  }

  /** @internal A run that only ever answers CONTINUE/sdk_error and emits nothing. */
  static degraded(client: Sunkcost): Run {
    return new Run(client, "", null, null, true);
  }

  get steps(): number {
    return this.state.k;
  }

  /** Total recorded cost as a decimal string. */
  get totalCostUsd(): string {
    return moneyStr(this.total);
  }

  // -- hot path ------------------------------------------------------------------------------

  /** Decide whether to run the next step. Synchronous, no I/O; never throws. */
  check(): Decision {
    if (this.degradedRun) return SDK_ERROR;
    const client = this.client;
    try {
      if (this.ended) return this.lastDecision ?? NO_BUNDLE;
      const override = client.bundles.override(this.workload);
      const d = decide(this.bundle, override, this.holdout, this.state, client.clockUs());
      const k = this.state.k;
      if (d.wouldAbort && this.wouldAbortStep === null) {
        this.wouldAbortStep = k;
        this.costThroughWouldAbort = this.total;
      }
      if (d.action === "ABORT" && this.abortedStep === null) this.abortedStep = k;
      this.lastDecision = d;
      client.stats.checks += 1;
      if (d.action === "ABORT") client.stats.aborts += 1;
      return d;
    } catch {
      client.stats.sdkErrors += 1;
      return SDK_ERROR;
    }
  }

  /** Record a completed step. Tool arguments are HMAC-hashed here and never sent. */
  recordStep(step: StepInput): void {
    if (this.degradedRun) return;
    const client = this.client;
    try {
      if (this.ended) throw new Error("recordStep after end()");
      const kind = step.kind as string; // callers in plain JavaScript can pass anything
      if (kind !== "model" && kind !== "tool") throw new RangeError("kind must be 'model' or 'tool'");
      const tsUs = client.clockUs();
      const model = text(step.model);
      const toolName = text(step.toolName);
      const tin = count(step.inputTokens ?? 0, MAX_TOKENS);
      const tout = count(step.outputTokens ?? 0, MAX_TOKENS);
      const given = step.costUsd ?? null;
      let cost = given === null ? null : toMoney(given);
      if (given !== null && cost === null) client.stats.sdkErrors += 1; // invalid: recorded as unknown
      if (cost === null && given === null && model !== null) {
        cost = client.prices.cost(model, tin, tout, utcDate(tsUs));
      }
      let hashed: string | null = null;
      if (step.toolArgs !== undefined && step.toolArgs !== null) {
        try {
          hashed = argsHash(client.salt, step.toolArgs);
        } catch (error) {
          if (!(error instanceof CanonicalJSONError)) throw error;
          hashed = argsHash(client.salt, inspect(step.toolArgs, { depth: 8, sorted: true }));
        }
      }
      const costStr = cost === null ? null : moneyStr(cost);
      const record: StepRecord = {
        kind,
        model,
        toolName,
        argsHash: hashed,
        inputTokens: tin,
        outputTokens: tout,
        cost: costStr === null ? 0 : Number(costStr),
        error: Boolean(step.error),
        tsUs,
      };
      if (this.state.k >= MAX_STEPS) throw new RangeError("run exceeds 1,000,000 steps");
      this.state.update(record);
      const seq = this.state.k;
      if (cost !== null) this.total += cost;
      const decision = this.lastDecision;
      this.lastDecision = null;
      client.emit({
        type: "step",
        run_id: this.runId,
        seq,
        ts: formatTimestampUs(tsUs),
        kind,
        model,
        tool_name: toolName,
        args_hash: hashed,
        input_tokens: tin,
        output_tokens: tout,
        cost_usd: costStr,
        error: Boolean(step.error),
        latency_ms: step.latencyMs === undefined || step.latencyMs === null ? null : count(step.latencyMs, MAX_LATENCY_MS),
        decision: decision === null ? null : decision.stepJson(),
      });
    } catch {
      client.stats.sdkErrors += 1;
    }
  }

  /** End the run; emits run_end with its decision summary. Later calls are ignored. */
  end(status: RunStatus = "completed"): void {
    if (this.degradedRun || this.ended) return;
    const client = this.client;
    try {
      let s: RunStatus = status;
      if (!RUN_STATUSES.includes(s)) {
        client.stats.sdkErrors += 1;
        s = "errored";
      }
      this.ended = true;
      client.emit({
        type: "run_end",
        run_id: this.runId,
        ended_at: formatTimestampUs(client.clockUs()),
        status: s,
        total_cost_usd: moneyStr(this.total),
        steps: this.state.k,
        would_abort_step: this.wouldAbortStep,
        cost_through_would_abort_usd: this.costThroughWouldAbort === null ? null : moneyStr(this.costThroughWouldAbort),
        aborted_step: this.abortedStep,
      });
    } catch {
      client.stats.sdkErrors += 1;
    }
  }

  outcome(outcome: Outcome, source = "sdk"): void {
    if (this.degradedRun) return;
    this.client.outcome(this.runId, outcome, source);
  }

  /**
   * Run `body` and end the run with the matching status: completed, aborted (a RunAborted), or
   * errored. The error is rethrown.
   */
  async use<T>(body: (run: Run) => Promise<T> | T): Promise<T> {
    try {
      const result = await body(this);
      this.end("completed");
      return result;
    } catch (error) {
      this.end(error instanceof RunAborted ? "aborted" : "errored");
      throw error;
    }
  }
}

// -- process exit: bounded flush of every open client (§4 C2/C3) ----------------------------------
// "beforeExit" fires when the event loop empties (the SDK's timers are unref'd), and async work
// scheduled there runs before the process exits. An explicit process.exit() skips it; call
// client.close() first in that case (ADR-0008).

const clients = new Set<Sunkcost>();
let exitHooked = false;

function onBeforeExit(): void {
  for (const client of [...clients]) void client.close();
}

function registerExit(client: Sunkcost): void {
  clients.add(client);
  if (!exitHooked) {
    process.on("beforeExit", onBeforeExit);
    exitHooked = true;
  }
}

function unregisterExit(client: Sunkcost): void {
  clients.delete(client);
}

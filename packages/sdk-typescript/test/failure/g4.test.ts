// G4 (Node half): failure injection against a real local HTTP server (§10, §13). Mirrors
// packages/sdk-python/tests/failure/test_g4_failure.py scenario for scenario: whatever the API
// does, the agent completes every run and the decisions match §10.

import { spawn } from "node:child_process";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { type AddressInfo, createServer as createNetServer, Socket } from "node:net";
import { gunzipSync } from "node:zlib";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Sunkcost, type SunkcostOptions } from "../../src/client.js";
import { FeatureState } from "../../src/core/features.js";
import type { Decision } from "../../src/decision.js";
import { TEST_TRUSTED_KEYS, makeBundle, sign, type BundleOptions } from "../../src/testing.js";
import type { BundleResponse, IngestBatch, IngestEvent } from "../../src/wire.js";

const WORKLOAD = "ci.fix_tests";
const STEPS = 12;
/** Well above scheduler jitter; any wait on network I/O would exceed it (the slow API holds 10 s). */
const SLOW_CALL_MS = 500;

class FakeApi {
  behaviour: "ok" | "error" | "slow" = "ok";
  delayMs = 10_000;
  readonly responses = new Map<string, BundleResponse>();
  readonly batches: IngestBatch[] = [];
  requests = 0;
  url = "";
  private readonly server: Server;
  private readonly sockets = new Set<Socket>();

  constructor() {
    this.server = createServer((req, res) => {
      void this.handle(req).then(([status, body]) => {
        if (res.destroyed) return;
        res.writeHead(status, body ? { "content-type": "application/json" } : {});
        res.end(body);
      });
    });
    this.server.on("connection", (s) => {
      this.sockets.add(s);
      s.on("close", () => this.sockets.delete(s));
    });
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.url = `http://127.0.0.1:${String((this.server.address() as AddressInfo).port)}`;
  }

  private async handle(req: IncomingMessage): Promise<[number, string]> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    this.requests += 1;
    if (this.behaviour === "slow") await new Promise((r) => setTimeout(r, this.delayMs));
    if (this.behaviour === "error") return [500, ""];
    if (req.method === "POST") {
      let body = Buffer.concat(chunks);
      if (req.headers["content-encoding"] === "gzip") body = gunzipSync(body);
      this.batches.push(JSON.parse(body.toString("utf8")) as IngestBatch);
      return [202, ""];
    }
    const workload = new URL(req.url ?? "/", "http://x").searchParams.get("workload") ?? "";
    const doc = this.responses.get(workload);
    return doc ? [200, JSON.stringify(doc)] : [404, ""];
  }

  get events(): IngestEvent[] {
    return this.batches.flatMap((b) => b.events);
  }

  async stop(): Promise<void> {
    for (const s of this.sockets) s.destroy();
    await new Promise<void>((resolve) => this.server.close(() => { resolve(); }));
  }
}

// -- harness ----------------------------------------------------------------------------------

/** Network calls started while an SDK call was on the stack (the agent's path). */
let inSdkCall = false;
let agentPathIo = 0;
function descriptor(target: object, key: string): PropertyDescriptor {
  const d = Object.getOwnPropertyDescriptor(target, key);
  if (!d) throw new Error(`no property ${key}`);
  return d;
}

const realConnect = descriptor(Socket.prototype, "connect");
const realFetch = globalThis.fetch;

function guardNetwork(): void {
  Object.defineProperty(Socket.prototype, "connect", {
    ...realConnect,
    value: function connect(this: Socket, ...args: unknown[]): unknown {
      if (inSdkCall) agentPathIo += 1;
      return Reflect.apply(realConnect.value as (...a: unknown[]) => unknown, this, args);
    },
  });
  globalThis.fetch = (...args: Parameters<typeof fetch>) => {
    if (inSdkCall) agentPathIo += 1;
    return realFetch(...args);
  };
}

function restoreNetwork(): void {
  Object.defineProperty(Socket.prototype, "connect", realConnect);
  globalThis.fetch = realFetch;
}

function timed<T>(fn: () => T, slowest: { ms: number }): T {
  inSdkCall = true;
  const t0 = performance.now();
  try {
    return fn();
  } finally {
    slowest.ms = Math.max(slowest.ms, performance.now() - t0);
    inSdkCall = false;
  }
}

/** A fake agent: up to STEPS tool steps, honouring ABORT. Returns its decisions and slowest SDK call. */
function agent(client: Sunkcost, failing = true): { decisions: Decision[]; slowestMs: number; runId: string } {
  const slowest = { ms: 0 };
  const ioBefore = agentPathIo;
  const run = timed(() => client.startRun(WORKLOAD), slowest);
  const decisions: Decision[] = [];
  for (let i = 0; i < STEPS; i++) {
    const d = timed(() => run.check(), slowest);
    decisions.push(d);
    if (d.abort) break;
    timed(() => { run.recordStep({ kind: "tool", toolName: "run_tests", toolArgs: { k: 1 }, error: failing }); }, slowest);
  }
  timed(() => { run.end(decisions.at(-1)?.abort ? "aborted" : "completed"); }, slowest);
  expect(agentPathIo - ioBefore, "an SDK call started network I/O on the agent's path").toBe(0);
  return { decisions, slowestMs: slowest.ms, runId: run.runId };
}

const nowUs = () => Date.now() * 1000;
function signed(options: Partial<BundleOptions> & { override?: "shadow" | null } = {}): BundleResponse {
  const { override = null, ...bundle } = options;
  return sign(makeBundle(WORKLOAD, { issuedAtUs: nowUs(), threshold: "0.9", minSteps: 2, ...bundle }), { override });
}

async function freePort(): Promise<number> {
  const s = createNetServer();
  await new Promise<void>((resolve) => s.listen(0, "127.0.0.1", resolve));
  const port = (s.address() as AddressInfo).port;
  await new Promise((resolve) => s.close(resolve));
  return port;
}

async function waitFor(predicate: () => boolean, timeoutMs = 10_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((r) => setTimeout(r, 20));
  }
  return predicate();
}

let api: FakeApi;
const clients: Sunkcost[] = [];
function makeClient(options: SunkcostOptions = {}): Sunkcost {
  const client = new Sunkcost({
    apiKey: "sc_test_key",
    endpoint: api.url,
    trustedKeys: TEST_TRUSTED_KEYS,
    argsHashSalt: "g4",
    flushIntervalMs: 50,
    workloads: [WORKLOAD],
    logger: null,
    ...options,
  });
  clients.push(client);
  return client;
}

beforeEach(async () => {
  api = new FakeApi();
  await api.start();
  guardNetwork();
});

afterEach(async () => {
  restoreNetwork();
  await Promise.all(clients.splice(0).map((c) => c.close(500)));
  await api.stop();
});

// -- §10 rows ---------------------------------------------------------------------------------

describe("G4 failure injection (Node)", () => {
  it("API unreachable: no bundle, never aborts, closing is bounded", async () => {
    const client = makeClient({ endpoint: `http://127.0.0.1:${String(await freePort())}` });
    await client.refreshBundles();
    const { decisions, slowestMs } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(new Set(decisions.map((d) => d.reason))).toEqual(new Set(["no_bundle"]));
    expect(slowestMs).toBeLessThan(SLOW_CALL_MS);
    expect(client.bundles.fetchErrors).toBeGreaterThanOrEqual(1);
    const t0 = Date.now();
    expect(await client.close(1000)).toBe(false); // nothing could be delivered...
    expect(Date.now() - t0).toBeLessThan(1500); // ...and closing is still bounded
  });

  it("API unreachable: the cached bundle is enforced until it expires", async () => {
    api.responses.set(WORKLOAD, signed({ ttlS: 2 }));
    const client = makeClient();
    await client.refreshBundles();
    await api.stop(); // the API goes away
    await client.refreshBundles();
    expect(agent(client).decisions.at(-1)?.reason).toBe("above_threshold");
    await new Promise((r) => setTimeout(r, 2100));
    const { decisions } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(new Set(decisions.map((d) => d.reason))).toEqual(new Set(["bundle_expired"]));
    api = new FakeApi(); // for afterEach
    await api.start();
  });

  it("API slow (10 s): the agent never waits, and close() is bounded at 3 s", async () => {
    api.behaviour = "slow";
    api.delayMs = 10_000;
    const client = makeClient();
    const { decisions, slowestMs } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(slowestMs).toBeLessThan(SLOW_CALL_MS);
    const t0 = Date.now();
    await client.close(); // default bounded wait: 3 s
    expect(Date.now() - t0).toBeLessThan(3500);
  });

  it("API 500s: retried with backoff, then delivered in order", async () => {
    api.behaviour = "error";
    const client = makeClient();
    const { runId, decisions, slowestMs } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(slowestMs).toBeLessThan(SLOW_CALL_MS);
    expect(await waitFor(() => (client.eventBuffer?.failedSends ?? 0) >= 2)).toBe(true);
    api.behaviour = "ok";
    expect(await client.flush(70_000)).toBe(true); // backoff is capped at 60 s
    const sent = api.events.filter((e) => e.run_id === runId);
    expect(sent[0]?.type).toBe("run_start");
    expect(sent.at(-1)?.type).toBe("run_end");
    expect(sent).toHaveLength(STEPS + 2);
  }, 90_000);

  it("bad signature: refused, and the previous bundle is kept", async () => {
    const client = makeClient();
    const bad = signed();
    bad.bundle.decision.threshold = "0.0"; // tampered after signing
    api.responses.set(WORKLOAD, bad);
    await client.refreshBundles();
    expect(client.bundles.rejected).toBeGreaterThanOrEqual(1);
    expect(new Set(agent(client).decisions.map((d) => d.reason))).toEqual(new Set(["no_bundle"]));
    api.responses.set(WORKLOAD, signed({ version: 3 }));
    await client.refreshBundles();
    const rejected = client.bundles.rejected;
    api.responses.set(WORKLOAD, { ...signed({ version: 4 }), signature: bad.signature });
    await client.refreshBundles();
    expect(client.bundles.rejected).toBeGreaterThan(rejected);
    expect(agent(client).decisions.at(-1)?.bundleVersion).toBe(3);
  });

  it("expired bundle: refused", async () => {
    api.responses.set(WORKLOAD, signed({ issuedAtUs: nowUs() - 8 * 86_400 * 1_000_000 }));
    const client = makeClient();
    await client.refreshBundles();
    const { decisions } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(new Set(decisions.map((d) => d.reason))).toEqual(new Set(["no_bundle"]));
  });

  it("rollback attempt: an older, validly signed version is refused", async () => {
    api.responses.set(WORKLOAD, signed({ version: 7, mode: "shadow" }));
    const client = makeClient();
    await client.refreshBundles();
    api.responses.set(WORKLOAD, signed({ version: 6, mode: "enforce" }));
    await client.refreshBundles();
    const { decisions } = agent(client);
    expect(decisions.at(-1)?.bundleVersion).toBe(7);
    expect(decisions.some((d) => d.abort)).toBe(false); // still the shadow bundle
  });

  it("override shadow: aborts stop at the next refresh", async () => {
    api.responses.set(WORKLOAD, signed());
    const client = makeClient();
    await client.refreshBundles();
    expect(agent(client).decisions.at(-1)?.abort).toBe(true);
    api.responses.set(WORKLOAD, signed({ override: "shadow" }));
    await client.refreshBundles();
    const { decisions } = agent(client);
    expect(decisions).toHaveLength(STEPS);
    expect(decisions.at(-1)?.reason).toBe("override_shadow");
  });

  it("buffer overflow: the oldest events are dropped and reported", async () => {
    api.behaviour = "error";
    const client = makeClient({ bufferSize: 20, flushBatchSize: 5 });
    for (let i = 0; i < 10; i++) expect(agent(client, false).decisions).toHaveLength(STEPS);
    expect(client.droppedEvents).toBeGreaterThan(0);
    api.behaviour = "ok";
    expect(await client.flush(70_000)).toBe(true);
    expect(api.batches.reduce((n, b) => n + b.dropped_events, 0)).toBe(client.droppedEvents);
  }, 90_000);

  it("sdk_error injection: internal failures never reach the agent", async () => {
    api.responses.set(WORKLOAD, signed());
    const client = makeClient();
    await client.refreshBundles();
    const update = descriptor(FeatureState.prototype, "update");
    const vector = descriptor(FeatureState.prototype, "vector");
    try {
      FeatureState.prototype.update = () => {
        throw new Error("injected");
      };
      const first = agent(client);
      expect(first.decisions).toHaveLength(STEPS); // state never advances, so it never aborts
      expect(client.stats.sdkErrors).toBe(STEPS);
      FeatureState.prototype.vector = () => {
        throw new Error("injected");
      };
      const { decisions } = agent(client);
      expect(decisions).toHaveLength(STEPS);
      expect(new Set(decisions.map((d) => `${d.action}/${d.reason}`))).toEqual(new Set(["CONTINUE/sdk_error"]));
    } finally {
      Object.defineProperty(FeatureState.prototype, "update", update);
      Object.defineProperty(FeatureState.prototype, "vector", vector);
    }
  });
});

// -- process exit ------------------------------------------------------------------------------

const CHILD = `
import { Sunkcost } from "./dist/index.js";
import { TEST_TRUSTED_KEYS } from "./dist/testing.js";
const client = new Sunkcost({ apiKey: "k", endpoint: process.argv[1], trustedKeys: TEST_TRUSTED_KEYS,
  argsHashSalt: "s", flushIntervalMs: 30000, logger: null });
for (let i = 0; i < Number(process.argv[2]); i++) {
  const run = client.startRun("ci.fix_tests");
  run.check();
  run.recordStep({ kind: "tool", toolName: "t" });
  run.end();
}
console.log("agent done");
// returns without close(): the beforeExit hook must flush, within its 3 s bound
`;

function child(url: string, runs: number): Promise<{ code: number | null; stdout: string; stderr: string; ms: number }> {
  const t0 = Date.now();
  return new Promise((resolve) => {
    const proc = spawn(process.execPath, ["--input-type=module", "-e", CHILD, url, String(runs)], {
      cwd: new URL("../../", import.meta.url),
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d: Buffer) => (stdout += d.toString()));
    proc.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
    proc.on("close", (code) => { resolve({ code, stdout, stderr, ms: Date.now() - t0 }); });
  });
}

describe("G4 process exit (Node, built package)", () => {
  it("flushes pending events when the process exits", async () => {
    const r = await child(api.url, 50);
    expect(r.code, r.stderr).toBe(0);
    expect(r.stdout).toContain("agent done");
    expect(api.events).toHaveLength(50 * 3);
  }, 30_000);

  it("bounds the exit flush when the API hangs mid-flush", async () => {
    api.behaviour = "slow";
    api.delayMs = 20_000;
    const r = await child(api.url, 50);
    expect(r.code, r.stderr).toBe(0);
    expect(r.stdout).toContain("agent done");
    expect(r.ms).toBeLessThan(3000 + 5000); // 3 s flush bound plus start-up and teardown
  }, 30_000);
});

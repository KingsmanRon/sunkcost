import { afterEach, describe, expect, it } from "vitest";

import { Sunkcost, type SunkcostOptions } from "../src/client.js";
import { FeatureState, stepFromWire } from "../src/core/features.js";
import { parseTimestampUs } from "../src/core/time.js";
import { RunAborted, findAbort } from "../src/errors.js";
import { isUuid7 } from "../src/ids.js";
import { MemoryTransport, TEST_TRUSTED_KEYS, demoClient, eventsOf, makeBundle, sign, type BundleOptions } from "../src/testing.js";
import type { IngestEvent, RunEndEvent, RunStartEvent, StepEvent } from "../src/wire.js";
import { specFiles, specJson } from "./helpers.js";
import { schemaErrors } from "./schema.js";

const W = "support.billing_dispute";
const clients: Sunkcost[] = [];
afterEach(async () => {
  await Promise.all(clients.splice(0).map((c) => c.close(500)));
});

function client(transport: MemoryTransport, options: SunkcostOptions = {}): Sunkcost {
  const c = new Sunkcost({ transport, trustedKeys: TEST_TRUSTED_KEYS, argsHashSalt: "test-salt", logger: null, flushIntervalMs: 20, workloads: [W], ...options });
  clients.push(c);
  return c;
}

async function serving(bundle: Partial<BundleOptions> = {}, override: "shadow" | null = null) {
  const transport = new MemoryTransport();
  transport.serve(W, sign(makeBundle(W, { issuedAtUs: Date.now() * 1000, minSteps: 2, ...bundle }), { override }));
  const c = client(transport);
  await c.refreshBundles();
  return { transport, c };
}

function failingAgent(c: Sunkcost, steps = 10) {
  const run = c.startRun(W);
  const decisions = [];
  for (let i = 0; i < steps; i++) {
    const d = run.check();
    decisions.push(d);
    if (d.abort) break;
    run.recordStep({ kind: "tool", toolName: "run_tests", toolArgs: { attempt: 1 }, error: true });
  }
  run.end(decisions.at(-1)?.abort ? "aborted" : "completed");
  return { run, decisions };
}

async function delivered(c: Sunkcost, transport: MemoryTransport): Promise<IngestEvent[]> {
  expect(await c.flush(2000)).toBe(true);
  for (const batch of transport.batches) expect(schemaErrors("ingest-batch.v1", batch)).toEqual([]);
  return transport.events;
}

describe("Sunkcost client (§4 C2/C3)", () => {
  it("aborts an enforced run and reports it in schema-valid events", async () => {
    const { transport, c } = await serving();
    const { run, decisions } = failingAgent(c);
    expect(decisions.map((d) => d.reason)).toEqual(["below_min_steps", "below_min_steps", "above_threshold"]);
    await delivered(c, transport);
    const start = eventsOf(transport, run.runId, ["run_start"])[0] as RunStartEvent;
    const steps = eventsOf(transport, run.runId, ["step"]) as StepEvent[];
    const end = eventsOf(transport, run.runId, ["run_end"])[0] as RunEndEvent;
    expect([start.mode, start.bundle_version, start.holdout]).toEqual(["enforce", 1, false]);
    expect(steps.map((s) => s.decision?.reason)).toEqual(["below_min_steps", "below_min_steps"]);
    expect(steps.every((s) => s.args_hash?.startsWith("hmac-sha256:"))).toBe(true);
    expect([end.status, end.steps, end.would_abort_step, end.aborted_step]).toEqual(["aborted", 2, 2, 2]);
    expect(c.stats.aborts).toBe(1);
  });

  it("computes the same features the worker reads back from the events", async () => {
    const { transport, c } = await serving({ mode: "shadow", minSteps: 0, weights: { repeat_action_rate: "1.5", log_cost: "0.7" } });
    const run = c.startRun(W);
    const scores: (number | null)[] = [];
    for (let i = 0; i < 8; i++) {
      scores.push(run.check().score);
      run.recordStep(i % 3 ? { kind: "model", model: "gpt-5", inputTokens: 1000 * i, outputTokens: 50 } : { kind: "tool", toolName: "search", toolArgs: { q: i % 2 } });
    }
    run.end();
    await delivered(c, transport);
    const state = new FeatureState(run.startedAtUs);
    const bundle = run.bundle;
    if (!bundle) throw new Error("no bundle");
    (eventsOf(transport, run.runId, ["step"]) as StepEvent[]).forEach((s, i) => {
      expect(bundle.model.score(state.vector())).toBe(scores[i]);
      expect(Number(s.decision?.score)).toBe(scores[i]);
      state.update(stepFromWire(s));
    });
  });

  it("prices model steps from the table and keeps explicit costs exact", async () => {
    const { transport, c } = await serving();
    const run = c.startRun(W);
    run.recordStep({ kind: "model", model: "claude-sonnet-4-5", inputTokens: 1_000_000, outputTokens: 1000 });
    run.recordStep({ kind: "model", model: "unknown-model", inputTokens: 10 });
    run.recordStep({ kind: "tool", toolName: "x", costUsd: "0.1" });
    run.recordStep({ kind: "tool", toolName: "x", costUsd: 0.2 });
    run.end();
    await delivered(c, transport);
    const costs = (eventsOf(transport, run.runId, ["step"]) as StepEvent[]).map((s) => s.cost_usd);
    // An alias of claude-sonnet-4-5-20250929 at $3 in / $15 out per 1M tokens: 3 + 0.015.
    expect(costs).toEqual(["3.015", null, "0.1", "0.2"]);
    expect(run.totalCostUsd).toBe("3.315"); // exact: 0.1 + 0.2 is not 0.30000000000000004 here
  });

  it("keeps a run on its start bundle but applies an override at once", async () => {
    const { transport, c } = await serving();
    const run = c.startRun(W);
    transport.serve(W, sign(makeBundle(W, { issuedAtUs: Date.now() * 1000, version: 2, minSteps: 2 }), { override: "shadow" }));
    await c.refreshBundles();
    expect(run.bundle?.version).toBe(1);
    for (let i = 0; i < 3; i++) run.recordStep({ kind: "tool", toolName: "t", error: true });
    const d = run.check();
    expect([d.action, d.reason, d.mode, d.bundleVersion]).toEqual(["CONTINUE", "override_shadow", "shadow", 1]);
    expect(c.startRun(W).bundle?.version).toBe(2);
  });

  it("never aborts a holdout run", async () => {
    const { c } = await serving({ holdoutRate: "1" });
    const { run, decisions } = failingAgent(c);
    expect(run.holdout).toBe(true);
    expect(decisions).toHaveLength(10);
    expect(decisions.at(-1)?.reason).toBe("holdout");
  });

  it("runs without a bundle in shadow and still reports", async () => {
    const transport = new MemoryTransport();
    const c = client(transport);
    await c.refreshBundles();
    const { run, decisions } = failingAgent(c, 3);
    expect(decisions.map((d) => d.reason)).toEqual(["no_bundle", "no_bundle", "no_bundle"]);
    await delivered(c, transport);
    expect((eventsOf(transport, run.runId, ["run_start"])[0] as RunStartEvent).mode).toBe("shadow");
    expect(eventsOf(transport, run.runId)).toHaveLength(5);
  });

  it("ends runs with the matching status in use()", async () => {
    const { transport, c } = await serving();
    const ok = c.startRun(W);
    await ok.use(() => 1);
    const aborted = c.startRun(W);
    await expect(aborted.use(() => { throw new Error("wrapped", { cause: new RunAborted(aborted.check(), aborted.runId) }); })).rejects.toThrow();
    const stopped = c.startRun(W);
    await expect(stopped.use(() => { throw new RunAborted(stopped.check(), stopped.runId); })).rejects.toBeInstanceOf(RunAborted);
    await delivered(c, transport);
    const status = (id: string) => (eventsOf(transport, id, ["run_end"])[0] as RunEndEvent).status;
    expect([status(ok.runId), status(aborted.runId), status(stopped.runId)]).toEqual(["completed", "errored", "aborted"]);
    expect(findAbort(new Error("x", { cause: new Error("y", { cause: new RunAborted(ok.check(), ok.runId) }) }))?.runId).toBe(ok.runId);
  });

  it("turns an internal exception in check() into sdk_error", async () => {
    const { c } = await serving();
    const run = c.startRun(W);
    (c.bundles as unknown as { override: () => never }).override = () => {
      throw new Error("boom");
    };
    const d = run.check();
    expect([d.action, d.reason]).toEqual(["CONTINUE", "sdk_error"]);
    expect(c.stats.sdkErrors).toBe(1);
  });

  it("never throws on bad arguments", async () => {
    const { transport, c } = await serving();
    const run = c.startRun(W);
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    const bad = [
      { kind: "thought" },
      { kind: "tool", toolArgs: circular },
      { kind: "tool", toolArgs: { when: new Date(0), n: 1n } },
      { kind: "model", inputTokens: -5, outputTokens: 1.5, costUsd: "lots" },
      { kind: "tool", latencyMs: Number.NaN },
    ];
    for (const step of bad) expect(() => { run.recordStep(step as never); }).not.toThrow();
    expect(() => { run.end("exploded" as never); }).not.toThrow();
    expect(() => { c.outcome("not-a-run", "success"); }).not.toThrow();
    await delivered(c, transport);
    expect(c.stats.sdkErrors).toBeGreaterThanOrEqual(4);
  });

  it("degrades an invalid workload and replaces an invalid run id", async () => {
    const { transport, c } = await serving();
    const degraded = c.startRun("no spaces allowed");
    expect(degraded.check().reason).toBe("sdk_error");
    degraded.recordStep({ kind: "tool" });
    degraded.end();
    const run = c.startRun(W, { runId: "123", externalRef: "x".repeat(500) });
    expect(isUuid7(run.runId)).toBe(true);
    expect(run.externalRef).toHaveLength(128);
    run.recordStep({ kind: "tool", toolName: "t".repeat(300) });
    run.end();
    const events = await delivered(c, transport);
    expect(events.some((e) => e.run_id === degraded.runId)).toBe(false);
    expect((eventsOf(transport, run.runId, ["step"])[0] as StepEvent).tool_name).toHaveLength(256);
  });

  it("decides offline with no network", () => {
    const c = demoClient(W, { minSteps: 1 });
    clients.push(c);
    const { decisions } = failingAgent(c);
    expect(decisions.at(-1)?.abort).toBe(true);
    expect(c.stats.eventsDiscardedOffline).toBeGreaterThan(0);
  });

  it("makes no transport call in check()", async () => {
    const { transport, c } = await serving();
    const run = c.startRun(W);
    const before = { fetches: transport.fetches, batches: transport.batches.length };
    for (let i = 0; i < 100; i++) run.check();
    expect({ fetches: transport.fetches, batches: transport.batches.length }).toEqual(before);
  });

  it("reads every ingest-batch fixture with the same step parser", () => {
    for (const file of specFiles("fixtures/valid/").filter((f) => f.includes("ingest-batch.v1--"))) {
      const batch = specJson(file) as { events: IngestEvent[] };
      expect(schemaErrors("ingest-batch.v1", batch)).toEqual([]);
      for (const e of batch.events) {
        if (e.type === "step") expect(stepFromWire(e).tsUs).toBe(parseTimestampUs(e.ts));
      }
    }
  });
});

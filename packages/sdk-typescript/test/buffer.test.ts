import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { EventBuffer } from "../src/buffer.js";
import { loadTrustedKeys } from "../src/bundle.js";
import { BundleManager } from "../src/bundles.js";
import { classify } from "../src/transport.js";
import { MemoryTransport, TEST_TRUSTED_KEYS, makeBundle, sign } from "../src/testing.js";
import type { IngestEvent } from "../src/wire.js";

const KEYS = loadTrustedKeys(TEST_TRUSTED_KEYS);
const W = "w";
const now = () => Date.now() * 1000;
const event = (i: number): IngestEvent => ({
  type: "outcome",
  run_id: `01a0cdb5-3500-7a3c-9e41-${i.toString().padStart(12, "0")}`,
  outcome: "success",
  source: "test",
  reported_at: "2026-10-21T00:00:00.000000Z",
});
const buffers: EventBuffer[] = [];
const dirs: string[] = [];
afterEach(async () => {
  await Promise.all(buffers.splice(0).map((b) => b.close(100)));
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

function buffer(transport: MemoryTransport, options: ConstructorParameters<typeof EventBuffer>[1] = {}): EventBuffer {
  const b = new EventBuffer(transport, { intervalMs: 10, random: () => 0, ...options });
  buffers.push(b);
  return b;
}

describe("event buffer", () => {
  it("classifies HTTP statuses like the Python SDK", () => {
    expect([200, 202, 408, 425, 429, 500, 503, 400, 401, 413, 422].map(classify)).toEqual([
      "ok", "ok", "retry", "retry", "retry", "retry", "retry", "drop", "drop", "drop", "drop",
    ]);
  });

  it("drops the oldest events on overflow and reports the count", async () => {
    const transport = new MemoryTransport();
    const b = buffer(transport, { capacity: 3 });
    for (let i = 0; i < 5; i++) b.put(event(i));
    expect(await b.flush(1000)).toBe(true);
    const batch = transport.batches[0];
    expect(batch?.dropped_events).toBe(2);
    expect(batch?.events.map((e) => e.run_id.slice(-1))).toEqual(["2", "3", "4"]);
    expect(b.droppedTotal).toBe(2);
  });

  it("retries with backoff, then delivers in order", async () => {
    const transport = new MemoryTransport();
    transport.sendResults.push({ status: "retry" }, { status: "retry", retryAfterS: 0 });
    const b = buffer(transport);
    b.start();
    for (let i = 0; i < 3; i++) b.put(event(i));
    expect(await b.flush(3000)).toBe(true);
    expect(b.failedSends).toBe(2);
    expect(transport.events.map((e) => e.run_id.slice(-1))).toEqual(["0", "1", "2"]);
  });

  it("drops a permanently rejected batch and counts it", async () => {
    const transport = new MemoryTransport();
    transport.sendResults.push({ status: "drop", httpStatus: 422 });
    const b = buffer(transport, { batchSize: 2 });
    for (let i = 0; i < 3; i++) b.put(event(i));
    expect(await b.flush(1000)).toBe(true);
    expect(transport.batches).toHaveLength(1);
    expect(transport.batches[0]?.dropped_events).toBe(2); // the lost batch is reported in the next
    expect(b.droppedTotal).toBe(2);
  });

  it("respects the batch size", async () => {
    const transport = new MemoryTransport();
    const b = buffer(transport, { batchSize: 4 });
    for (let i = 0; i < 10; i++) b.put(event(i));
    expect(await b.flush(1000)).toBe(true);
    expect(transport.batches.map((x) => x.events.length)).toEqual([4, 4, 2]);
  });
});

describe("bundle manager", () => {
  function manager(transport: MemoryTransport, cacheDir: string | null = null): BundleManager {
    return new BundleManager(transport, KEYS, { cacheDir, refreshMs: 60_000 });
  }

  it("uses ETags, and a 404 clears the bundle", async () => {
    const transport = new MemoryTransport();
    transport.serve(W, sign(makeBundle(W, { issuedAtUs: now() })));
    const m = manager(transport);
    expect(await m.refresh(W)).toBe(true);
    expect(await m.refresh(W)).toBe(false); // 304
    transport.serve(W, null);
    expect(await m.refresh(W)).toBe(true);
    expect(m.get(W)).toEqual([null, null]);
    m.stop();
  });

  it("keeps the previous bundle when a new one is rejected or the fetch fails", async () => {
    const transport = new MemoryTransport();
    transport.serve(W, sign(makeBundle(W, { issuedAtUs: now(), version: 2 })));
    const m = manager(transport);
    await m.refresh(W);
    const tampered = sign(makeBundle(W, { issuedAtUs: now(), version: 3 }));
    tampered.bundle.mode = "shadow";
    transport.serve(W, tampered);
    expect(await m.refresh(W)).toBe(false);
    expect(m.rejected).toBe(1);
    transport.serve(W, sign(makeBundle(W, { issuedAtUs: now(), version: 1 }))); // a rollback
    expect(await m.refresh(W)).toBe(false);
    transport.fetchBundle = () => Promise.reject(new Error("network down"));
    expect(await m.refresh(W)).toBe(false);
    expect(m.fetchErrors).toBe(1);
    expect(m.get(W)[0]?.version).toBe(2);
    m.stop();
  });

  it("survives a restart through the disk cache and keeps the version floor", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sunkcost-cache-"));
    dirs.push(dir);
    const transport = new MemoryTransport();
    transport.serve(W, sign(makeBundle(W, { issuedAtUs: now(), version: 5 })));
    const first = manager(transport, dir);
    await first.refresh(W);
    first.stop();

    const offline = new BundleManager(null, KEYS, { cacheDir: dir });
    offline.register(W);
    expect(offline.get(W)[0]?.version).toBe(5);
    expect(() => { offline.install(W, sign(makeBundle(W, { issuedAtUs: now(), version: 4 }))); }).toThrow(/rollback/);
  });
});

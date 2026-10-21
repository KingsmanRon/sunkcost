// G3 (Node half): check() latency at step 200 must be < 0.5 ms at p99, with no network.
//
//   pnpm --filter sunkcost run bench [-- --out ../../docs/PERF.md]
//
// Mirrors packages/sdk-python/perf/bench_check.py: one offline client with a signed enforce-mode
// bundle using all 12 features, on a run that has already recorded 200 steps. Every socket connect
// and fetch() is intercepted while timing, so a network call fails the gate. Runs against the built
// package (dist/). Exit status 0 means the gate passed; results become the Node section of PERF.md.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import net from "node:net";
import os from "node:os";

import { FEATURE_NAMES, SDK_VERSION, Sunkcost } from "../dist/index.js";
import { TEST_TRUSTED_KEYS, makeBundle, sign } from "../dist/testing.js";

const P99_LIMIT_MS = 0.5;
const PREFIX_STEPS = 200;
const SAMPLES = 50_000;
const WARMUP = 5_000;
const WORKLOAD = "perf.check";
const START = "<!-- node:start -->";
const END = "<!-- node:end -->";

const args = process.argv.slice(2);
const outIndex = args.indexOf("--out");
const out = new URL(outIndex >= 0 ? `file://${args[outIndex + 1]}` : "../../../docs/PERF.md", import.meta.url);

function percentile(sorted, q) {
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))));
  return Number(sorted[i]) / 1e6;
}

function measure() {
  const client = new Sunkcost({ trustedKeys: TEST_TRUSTED_KEYS, argsHashSalt: "perf", logger: null });
  const weights = Object.fromEntries(FEATURE_NAMES.map((name, i) => [name, (0.05 * (i + 1)).toFixed(2)]));
  // A threshold the score never reaches, so every check runs the full path to below_threshold.
  const bundle = makeBundle(WORKLOAD, { issuedAtUs: Date.now() * 1000, weights, threshold: "1.0", minSteps: 3 });
  client.bundles.install(WORKLOAD, sign(bundle));
  const run = client.startRun(WORKLOAD);
  if (run.bundle === null) throw new Error("bundle not installed");

  const recordNs = [];
  for (let i = 0; i < PREFIX_STEPS; i++) {
    const t0 = process.hrtime.bigint();
    if (i % 3 === 0) {
      run.recordStep({ kind: "model", model: "claude-sonnet-4-5", inputTokens: 1200 + 40 * i, outputTokens: 180 });
    } else {
      run.recordStep({ kind: "tool", toolName: `tool_${String(i % 7)}`, toolArgs: { q: i % 11, x: 1.5 }, error: i % 9 === 0 });
    }
    recordNs.push(process.hrtime.bigint() - t0);
  }
  if (run.steps !== PREFIX_STEPS) throw new Error(`recorded ${String(run.steps)} steps`);

  const connects = [];
  const originalConnect = net.Socket.prototype.connect;
  const originalFetch = globalThis.fetch;
  net.Socket.prototype.connect = function guarded(...a) {
    connects.push(a[0]);
    throw new Error("network call during check()");
  };
  globalThis.fetch = (...a) => {
    connects.push(a[0]);
    throw new Error("network call during check()");
  };
  let d;
  const samples = new Array(SAMPLES);
  try {
    for (let i = 0; i < WARMUP; i++) run.check();
    for (let i = 0; i < SAMPLES; i++) {
      const t0 = process.hrtime.bigint();
      d = run.check();
      samples[i] = process.hrtime.bigint() - t0;
    }
  } finally {
    net.Socket.prototype.connect = originalConnect;
    globalThis.fetch = originalFetch;
  }
  if (d.reason !== "below_threshold") throw new Error(`unexpected decision ${JSON.stringify(d)}`);
  const sdkErrors = client.stats.sdkErrors;
  void client.close(100);
  const byValue = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  samples.sort(byValue);
  recordNs.sort(byValue);
  return {
    p50: percentile(samples, 0.5),
    p90: percentile(samples, 0.9),
    p99: percentile(samples, 0.99),
    p999: percentile(samples, 0.999),
    max: Number(samples[samples.length - 1]) / 1e6,
    recordP99: percentile(recordNs, 0.99),
    connects: connects.length,
    sdkErrors,
  };
}

function render(r, passed) {
  const when = new Date().toISOString().slice(0, 10);
  const f = (x) => x.toFixed(4);
  return `${START}
## TypeScript SDK (\`sunkcost\` ${SDK_VERSION})

Measured on ${when} with \`make bench-sdk\` (\`packages/sdk-typescript/bench/check.mjs\`):
Node.js ${process.versions.node} on ${os.type()} ${os.release()} (${os.arch()}),
against the built package. One run with ${String(PREFIX_STEPS)} recorded steps, an enforce-mode bundle
using all 12 features, ${SAMPLES.toLocaleString("en-US")} timed \`check()\` calls after ${WARMUP.toLocaleString("en-US")} warm-up calls.

| Metric | Value |
|---|---|
| \`check()\` p50 | ${f(r.p50)} ms |
| \`check()\` p90 | ${f(r.p90)} ms |
| \`check()\` p99 | **${f(r.p99)} ms** (limit ${String(P99_LIMIT_MS)} ms) |
| \`check()\` p99.9 | ${f(r.p999)} ms |
| \`check()\` max | ${f(r.max)} ms |
| \`recordStep()\` p99 over the 200 recorded steps | ${f(r.recordP99)} ms |
| Network calls attempted during \`check()\` | ${String(r.connects)} |

**G3 (Node): ${passed ? "PASS" : "FAIL"}.**
${END}`;
}

function write(section) {
  const header =
    "# Performance\n\nMeasured, not claimed (ARCHITECTURE.md §13 G3, S2). Each section is " +
    "regenerated by `make bench-sdk`; do not edit by hand.\n\n";
  let text = existsSync(out) ? readFileSync(out, "utf8") : header;
  const pattern = new RegExp(`${START}[\\s\\S]*?${END}`);
  text = pattern.test(text) ? text.replace(pattern, () => section) : `${text.trimEnd()}\n\n${section}`;
  writeFileSync(out, `${text.trimEnd()}\n`);
}

const result = measure();
const passed = result.p99 < P99_LIMIT_MS && result.connects === 0 && result.sdkErrors === 0;
write(render(result, passed));
console.log(
  `check() at step ${String(PREFIX_STEPS)}: p50 ${result.p50.toFixed(4)} ms, p99 ${result.p99.toFixed(4)} ms, ` +
    `max ${result.max.toFixed(4)} ms; network calls ${String(result.connects)}; G3 Node ${passed ? "PASS" : "FAIL"}`,
);
process.exitCode = passed ? 0 : 1;

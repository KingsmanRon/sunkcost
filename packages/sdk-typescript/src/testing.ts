// Helpers for tests and offline examples: an in-memory API and a bundle signer (mirrors
// sunkcost.testing). The signer uses the PUBLIC RFC 8032 §7.1 TEST 1 key (key_id rfc8032-test-1).
// It is not trusted by default; pass TEST_TRUSTED_KEYS explicitly. Never use it for real bundles.

import { createHash } from "node:crypto";

import { Sunkcost, type SunkcostOptions } from "./client.js";
import { canonicalJson } from "./core/canonical.js";
import { FEATURE_NAMES } from "./core/features.js";
import { formatTimestampUs } from "./core/time.js";
import { ed25519Sign } from "./ed25519.js";
import { uuid7 } from "./ids.js";
import type { FetchResult, SendResult, Transport } from "./transport.js";
import type { BundleDoc, BundleResponse, IngestBatch, IngestEvent, Mode } from "./wire.js";

export const TEST_KEY_ID = "rfc8032-test-1";
export const TEST_SEED = Uint8Array.from(
  Buffer.from("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "hex"),
);
export const TEST_PUBLIC_KEY_B64 = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=";
export const TEST_TRUSTED_KEYS: Readonly<Record<string, string>> = Object.freeze({
  [TEST_KEY_ID]: TEST_PUBLIC_KEY_B64,
});

export interface BundleOptions {
  version?: number;
  mode?: Mode;
  threshold?: string;
  minSteps?: number;
  holdoutRate?: string;
  intercept?: string;
  /** Feature name → decimal-string weight. Default: {error_streak: "2.0"}. */
  weights?: Readonly<Record<string, string>>;
  issuedAtUs: number;
  ttlS?: number;
  bundleId?: string;
}

/** A bundle.v1 document. */
export function makeBundle(workload: string, options: BundleOptions): BundleDoc {
  const weights = options.weights ?? { error_streak: "2.0" };
  const names = FEATURE_NAMES.filter((n) => n in weights);
  return {
    schema_version: "1",
    bundle_id: options.bundleId ?? uuid7(Math.floor(options.issuedAtUs / 1000)),
    org_id: "01a03336-8088-7f77-9846-a07c2bc1bf65",
    project_id: "01a03336-8470-7088-a957-b18d3cd2c076",
    workload_id: "01a03336-7ca0-7e66-8735-9f6b1ab0ae54",
    workload,
    version: options.version ?? 1,
    feature_spec: "features.v1",
    mode: options.mode ?? "enforce",
    holdout_rate: options.holdoutRate ?? "0",
    model: {
      type: "logistic_v1",
      intercept: options.intercept ?? "0.0",
      features: names,
      weights: names.map((n) => weights[n] ?? "0"),
      means: names.map(() => "0.0"),
      stds: names.map(() => "1.0"),
    },
    decision: { threshold: options.threshold ?? "0.5", min_steps: options.minSteps ?? 3, target_recall: "0.95", delta: "0.05" },
    metrics: { n_train: 0, n_calib_success: 0, test_false_abort_rate: "0.0", test_savings_share: "0.0", auroc: "0.5" },
    issued_at: formatTimestampUs(options.issuedAtUs),
    expires_at: formatTimestampUs(options.issuedAtUs + (options.ttlS ?? 7 * 86_400) * 1_000_000),
  };
}

/** Wrap a bundle in a signed bundle-response.v1. */
export function sign(
  bundle: BundleDoc,
  { override = null, seed = TEST_SEED }: { override?: "shadow" | null; seed?: Uint8Array } = {},
): BundleResponse {
  const signature = ed25519Sign(canonicalJson(bundle), seed);
  return {
    schema_version: "1",
    bundle: structuredClone(bundle),
    key_id: TEST_KEY_ID,
    signature: Buffer.from(signature).toString("base64"),
    override,
  };
}

/** An in-process stand-in for the API: records batches and serves bundle responses. */
export class MemoryTransport implements Transport {
  readonly batches: IngestBatch[] = [];
  readonly responses = new Map<string, BundleResponse>();
  fetches = 0;
  /** Scripted results, consumed in order. */
  readonly sendResults: SendResult[] = [];

  serve(workload: string, response: BundleResponse | null): void {
    if (response === null) this.responses.delete(workload);
    else this.responses.set(workload, response);
  }

  get events(): IngestEvent[] {
    return this.batches.flatMap((b) => b.events);
  }

  sendBatch(body: Uint8Array): Promise<SendResult> {
    const scripted = this.sendResults.shift();
    if (scripted && scripted.status !== "ok") return Promise.resolve(scripted);
    this.batches.push(JSON.parse(Buffer.from(body).toString("utf8")) as IngestBatch);
    return Promise.resolve({ status: "ok", httpStatus: 202 });
  }

  fetchBundle(workload: string, etag: string | null): Promise<FetchResult> {
    this.fetches += 1;
    const response = this.responses.get(workload);
    if (!response) return Promise.resolve({ status: 404 });
    const body = JSON.stringify(response);
    const tag = `"${createHash("sha256").update(body).digest("hex").slice(0, 8)}"`;
    if (etag === tag) return Promise.resolve({ status: 304, etag: tag });
    return Promise.resolve({ status: 200, body, etag: tag });
  }

  close(): void {
    // nothing to release
  }
}

export function eventsOf(transport: MemoryTransport, runId: string, types?: readonly string[]): IngestEvent[] {
  return transport.events.filter((e) => e.run_id === runId && (types === undefined || types.includes(e.type)));
}

/**
 * An offline Sunkcost trusting only the test key, with a freshly signed bundle installed. For
 * examples and tests without an API: decisions are real, events are discarded.
 */
export function demoClient(
  workload: string,
  bundle: Partial<BundleOptions> = {},
  options: SunkcostOptions = {},
): Sunkcost {
  const client = new Sunkcost({ trustedKeys: TEST_TRUSTED_KEYS, argsHashSalt: "demo-salt", logger: null, ...options });
  const issuedAtUs = bundle.issuedAtUs ?? Date.now() * 1000;
  client.bundles.install(workload, sign(makeBundle(workload, { ...bundle, issuedAtUs })));
  return client;
}

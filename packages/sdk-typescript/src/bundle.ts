// Signed policy bundles: verification and parsing (§4 C2 bundle manager, §6.2, §9), mirroring
// sunkcost.bundle. A response is accepted only if its Ed25519 signature over canonicalJson(bundle)
// verifies under a pinned key, it names the expected workload, it has not expired, and its
// version is not below the highest already accepted. The unsigned override can only be null or
// "shadow", so it can only reduce enforcement.

import { CanonicalJSONError, canonicalJson } from "./core/canonical.js";
import { FEATURE_SPEC } from "./core/features.js";
import { holdoutThreshold } from "./core/holdout.js";
import { LogisticModel, ModelError, parseF64 } from "./core/scoring.js";
import { parseTimestampUs } from "./core/time.js";
import { ed25519Verify } from "./ed25519.js";
import { UUID } from "./ids.js";
import type { Mode } from "./wire.js";

export type Override = "shadow" | null;

export class BundleRejected extends Error {
  override name = "BundleRejected";
  constructor(
    readonly reason: string,
    detail = "",
  ) {
    super(detail ? `${reason}: ${detail}` : reason);
  }
}

/** A verified bundle, parsed for the decision engine. */
export interface Bundle {
  bundleId: string;
  version: number;
  workload: string;
  mode: Mode;
  holdoutRate: string;
  model: LogisticModel;
  threshold: number;
  minSteps: number;
  issuedAtUs: number;
  expiresAtUs: number;
  keyId: string;
}

export type TrustedKeys = ReadonlyMap<string, Uint8Array>;

const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

function base64(text: string): Uint8Array {
  if (!BASE64.test(text)) throw new RangeError("not standard base64");
  return Uint8Array.from(Buffer.from(text, "base64"));
}

/** Parse `{keyId: base64 raw 32-byte Ed25519 public key}`. */
export function loadTrustedKeys(keys: Readonly<Record<string, string>>): TrustedKeys {
  const out = new Map<string, Uint8Array>();
  for (const [keyId, b64] of Object.entries(keys)) {
    const raw = base64(b64);
    if (raw.length !== 32) throw new RangeError(`trusted key ${keyId} is not a 32-byte Ed25519 public key`);
    out.set(keyId, raw);
  }
  return out;
}

function f64(value: unknown, what: string): number {
  try {
    return parseF64(value, what);
  } catch (error) {
    if (error instanceof ModelError) throw new BundleRejected("malformed", error.message);
    throw error;
  }
}

function int(value: unknown, what: string, low: number, high: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < low || value > high) {
    throw new BundleRejected("malformed", `${what} must be an integer in [${String(low)}, ${String(high)}]`);
  }
  return value;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Verify a bundle-response.v1 document; returns the bundle and the override. */
export function verifyResponse(
  doc: unknown,
  trustedKeys: TrustedKeys,
  { workload, nowUs, minVersion = 0 }: { workload: string; nowUs: number; minVersion?: number },
): [Bundle, Override] {
  if (!isObject(doc) || doc.schema_version !== "1") {
    throw new BundleRejected("malformed", "not a bundle-response.v1 document");
  }
  const override = doc.override ?? null;
  if (override !== null && override !== "shadow") {
    throw new BundleRejected("malformed", "override must be null or 'shadow'");
  }
  const { key_id: keyId, signature, bundle } = doc;
  if (typeof keyId !== "string" || typeof signature !== "string" || !isObject(bundle)) {
    throw new BundleRejected("malformed", "key_id, signature and bundle are required");
  }
  const key = trustedKeys.get(keyId);
  if (!key) throw new BundleRejected("untrusted_key", keyId);
  let sig: Uint8Array;
  let message: Uint8Array;
  try {
    sig = base64(signature);
    message = canonicalJson(bundle);
  } catch (error) {
    if (error instanceof CanonicalJSONError || error instanceof RangeError) {
      throw new BundleRejected("bad_signature", error.message);
    }
    throw error;
  }
  if (!ed25519Verify(sig, message, key)) throw new BundleRejected("bad_signature");

  // Signed content from here on; still validated, since a trusted key could sign a bad bundle.
  if (bundle.schema_version !== "1" || bundle.feature_spec !== FEATURE_SPEC) {
    throw new BundleRejected("unsupported", "schema_version or feature_spec");
  }
  if (bundle.workload !== workload) throw new BundleRejected("wrong_workload", String(bundle.workload));
  const bundleId = bundle.bundle_id;
  if (typeof bundleId !== "string" || !UUID.test(bundleId)) throw new BundleRejected("malformed", "bundle_id");
  const version = int(bundle.version, "version", 1, 2_147_483_647);
  if (version < minVersion) {
    throw new BundleRejected("rollback", `version ${String(version)} < ${String(minVersion)}`);
  }
  const mode = bundle.mode;
  if (mode !== "shadow" && mode !== "enforce") throw new BundleRejected("malformed", "mode");
  const holdoutRate = bundle.holdout_rate;
  try {
    if (typeof holdoutRate !== "string") throw new RangeError("holdout_rate");
    holdoutThreshold(holdoutRate);
  } catch {
    throw new BundleRejected("malformed", "holdout_rate");
  }
  const decision = bundle.decision;
  if (!isObject(decision)) throw new BundleRejected("malformed", "decision");
  let model: LogisticModel;
  let issuedAtUs: number;
  let expiresAtUs: number;
  try {
    model = LogisticModel.fromBundle(bundle.model ?? {});
    issuedAtUs = parseTimestampUs(bundle.issued_at as string);
    expiresAtUs = parseTimestampUs(bundle.expires_at as string);
  } catch (error) {
    if (error instanceof ModelError || error instanceof RangeError || error instanceof TypeError) {
      throw new BundleRejected("malformed", error.message);
    }
    throw error;
  }
  if (expiresAtUs <= nowUs) throw new BundleRejected("expired", String(bundle.expires_at));
  return [
    {
      bundleId,
      version,
      workload,
      mode,
      holdoutRate,
      model,
      threshold: f64(decision.threshold, "threshold"),
      minSteps: int(decision.min_steps, "min_steps", 0, 1_000_000),
      issuedAtUs,
      expiresAtUs,
      keyId,
    },
    override,
  ];
}

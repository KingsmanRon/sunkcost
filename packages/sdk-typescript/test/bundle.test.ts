import { describe, expect, it } from "vitest";

import { BundleRejected, loadTrustedKeys, verifyResponse } from "../src/bundle.js";
import { canonicalJson } from "../src/core/canonical.js";
import { ed25519PublicKey, ed25519Sign } from "../src/ed25519.js";
import { DEFAULT_TRUSTED_KEYS } from "../src/keys.js";
import { TEST_KEY_ID, TEST_PUBLIC_KEY_B64, TEST_TRUSTED_KEYS, makeBundle, sign } from "../src/testing.js";
import type { BundleResponse } from "../src/wire.js";
import { specJson } from "./helpers.js";

const NOW = 1_790_000_000_000_000;
const KEYS = loadTrustedKeys(TEST_TRUSTED_KEYS);
const W = "support.billing_dispute";

function reason(fn: () => unknown): string {
  try {
    fn();
  } catch (error) {
    if (error instanceof BundleRejected) return error.reason;
    throw error;
  }
  return "accepted";
}

const verify = (doc: unknown, minVersion = 0) => verifyResponse(doc, KEYS, { workload: W, nowUs: NOW, minVersion });

describe("bundle verification (§6.2, §9)", () => {
  it("accepts a valid response and reads the override", () => {
    const [bundle, override] = verify(sign(makeBundle(W, { issuedAtUs: NOW, version: 4, minSteps: 2 })));
    expect([bundle.version, bundle.mode, bundle.minSteps, bundle.threshold, override]).toEqual([4, "enforce", 2, 0.5, null]);
    expect(verify(sign(makeBundle(W, { issuedAtUs: NOW }), { override: "shadow" }))[1]).toBe("shadow");
  });

  it("rejects tampered content and a tampered mode", () => {
    const doc = sign(makeBundle(W, { issuedAtUs: NOW, threshold: "0.5" }));
    doc.bundle.decision.threshold = "0.1";
    expect(reason(() => verify(doc))).toBe("bad_signature");
    const shadow = sign(makeBundle(W, { issuedAtUs: NOW, mode: "shadow" }));
    shadow.bundle.mode = "enforce";
    expect(reason(() => verify(shadow))).toBe("bad_signature");
  });

  it("rejects untrusted key ids and another key under a trusted id", () => {
    const doc = sign(makeBundle(W, { issuedAtUs: NOW }));
    expect(reason(() => verify({ ...doc, key_id: "someone-else" }))).toBe("untrusted_key");
    const otherSeed = new Uint8Array(32).fill(7);
    const forged = { ...doc, signature: Buffer.from(ed25519Sign(canonicalJson(doc.bundle), otherSeed)).toString("base64") };
    expect(reason(() => verify(forged))).toBe("bad_signature");
    expect(Buffer.from(ed25519PublicKey(otherSeed)).toString("base64")).not.toBe(TEST_PUBLIC_KEY_B64);
  });

  it("rejects the wrong workload, rollbacks and expired bundles", () => {
    expect(reason(() => verify(sign(makeBundle("other", { issuedAtUs: NOW }))))).toBe("wrong_workload");
    expect(reason(() => verify(sign(makeBundle(W, { issuedAtUs: NOW, version: 3 })), 4))).toBe("rollback");
    expect(reason(() => verify(sign(makeBundle(W, { issuedAtUs: NOW - 8 * 86_400_000_000 }))))).toBe("expired");
  });

  it("lets the unsigned override only reduce enforcement", () => {
    const doc = { ...sign(makeBundle(W, { issuedAtUs: NOW })), override: "enforce" };
    expect(reason(() => verify(doc))).toBe("malformed");
  });

  it("refuses floats in signed content", () => {
    const doc = sign(makeBundle(W, { issuedAtUs: NOW })) as unknown as { bundle: { decision: { min_steps: number } } };
    doc.bundle.decision.min_steps = 2.5;
    expect(reason(() => verify(doc))).toBe("bad_signature");
  });

  it("refuses signed but unusable bundles", () => {
    const cases: [string, (b: Record<string, unknown>) => void][] = [
      ["unsupported", (b) => (b.feature_spec = "features.v2")],
      ["malformed", (b) => (b.holdout_rate = "0.12345")],
      ["malformed", (b) => (b.mode = "aggressive")],
      ["malformed", (b) => ((b.model as Record<string, unknown>).type = "tree")],
      ["malformed", (b) => ((b.decision as Record<string, unknown>).threshold = "0x10")],
      ["malformed", (b) => (b.bundle_id = "not-a-uuid")],
    ];
    for (const [want, mutate] of cases) {
      const bundle = makeBundle(W, { issuedAtUs: NOW }) as unknown as Record<string, unknown>;
      mutate(bundle);
      expect(reason(() => verify(sign(bundle as never)))).toBe(want);
    }
  });

  it("verifies the spec fixture before it expires", () => {
    const doc = specJson("fixtures/valid/bundle-response.v1--signed.json") as BundleResponse;
    const nowUs = Date.parse(doc.bundle.issued_at) * 1000 + 1;
    const [bundle] = verifyResponse(doc, KEYS, { workload: doc.bundle.workload, nowUs });
    expect(bundle.version).toBe(doc.bundle.version);
  });

  it("never trusts the test key by default", () => {
    expect(Object.keys(DEFAULT_TRUSTED_KEYS)).not.toContain(TEST_KEY_ID);
    expect(Object.values(DEFAULT_TRUSTED_KEYS)).not.toContain(TEST_PUBLIC_KEY_B64);
  });
});

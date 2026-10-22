import { describe, expect, it } from "vitest";

import { verifyResponse, loadTrustedKeys, type Bundle } from "../src/bundle.js";
import { FeatureState } from "../src/core/features.js";
import { decide, Decision } from "../src/decision.js";
import { makeBundle, sign, TEST_TRUSTED_KEYS, type BundleOptions } from "../src/testing.js";
import type { DecisionDoc } from "../src/wire.js";
import { specFiles, specJson } from "./helpers.js";
import { schemaErrors } from "./schema.js";

const NOW = 1_790_000_000_000_000;
const KEYS = loadTrustedKeys(TEST_TRUSTED_KEYS);

function bundleOf(options: Partial<BundleOptions> = {}): Bundle {
  return verifyResponse(sign(makeBundle("w", { issuedAtUs: NOW, ...options })), KEYS, { workload: "w", nowUs: NOW })[0];
}

/** A state with `n` erroring tool steps (error_streak = n). */
function erroring(n: number): FeatureState {
  const state = new FeatureState(NOW);
  for (let i = 0; i < n; i++) {
    state.update({ kind: "tool", model: null, toolName: "t", argsHash: null, inputTokens: 0, outputTokens: 0, cost: 0, error: true, tsUs: NOW + i });
  }
  return state;
}

describe("decision engine (ADR-0004 order)", () => {
  it("gives every reason in its fixed order", () => {
    const b = bundleOf({ minSteps: 2 }); // score = sigmoid(2 × error_streak) ≥ 0.5 from the first error
    expect(decide(null, null, false, erroring(5), NOW).reason).toBe("no_bundle");
    expect(decide(b, null, false, erroring(5), b.expiresAtUs).reason).toBe("bundle_expired");
    expect(decide(b, null, false, erroring(1), NOW).reason).toBe("below_min_steps");
    expect(decide(bundleOf({ threshold: "0.9999" }), null, false, erroring(3), NOW).reason).toBe("below_threshold");
    expect(decide(b, "shadow", false, erroring(3), NOW).reason).toBe("override_shadow");
    expect(decide(bundleOf({ mode: "shadow" }), null, false, erroring(3), NOW).reason).toBe("shadow_mode");
    expect(decide(b, null, true, erroring(3), NOW).reason).toBe("holdout");
    const abort = decide(b, null, false, erroring(3), NOW);
    expect([abort.action, abort.reason, abort.wouldAbort, abort.mode]).toEqual(["ABORT", "above_threshold", true, "enforce"]);
  });

  it("never aborts on an expired bundle and treats the threshold as inclusive", () => {
    const b = bundleOf({ minSteps: 0, intercept: "0.0", weights: { error_streak: "0.0" } }); // score 0.5
    expect(decide(b, null, false, erroring(3), b.expiresAtUs + 1).action).toBe("CONTINUE");
    const d = decide(b, null, false, erroring(3), NOW);
    expect([d.score, d.threshold, d.action]).toEqual([0.5, 0.5, "ABORT"]);
  });
});

describe("decision.v1 fixtures", () => {
  for (const file of specFiles("fixtures/valid/").filter((f) => f.includes("decision.v1--"))) {
    it(file, () => {
      const doc = specJson(file) as DecisionDoc;
      expect(schemaErrors("decision.v1", doc)).toEqual([]);
      const d = new Decision(
        doc.action,
        doc.mode,
        doc.score === null ? null : Number(doc.score),
        doc.threshold === null ? null : Number(doc.threshold),
        doc.bundle_id,
        doc.bundle_version,
        doc.would_abort,
        doc.reason,
      );
      expect(d.toJSON()).toEqual(doc); // floats come back as the same decimal strings
    });
  }
});

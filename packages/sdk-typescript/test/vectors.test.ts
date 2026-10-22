// The shared golden vectors (packages/spec/vectors) against the TypeScript core: the same files
// the Python core is tested against, so both SDKs compute identical features, scores and hashes.

import { describe, expect, it } from "vitest";

import {
  argsHash,
  canonicalJson,
  CanonicalJSONError,
  FEATURE_NAMES,
  FeatureState,
  holdoutBucket,
  isHoldout,
  LogisticModel,
  parseTimestampUs,
  stepFromWire,
} from "../src/core/index.js";
import { ed25519Verify } from "../src/ed25519.js";
import { closeTo, hex, specFiles, specJson } from "./helpers.js";

interface FeatureVector {
  name: string;
  feature_names: string[];
  started_at: string;
  steps: Parameters<typeof stepFromWire>[0][];
  model: unknown;
  expected: { k: number; features: string[]; score: string }[];
}

describe("features.v1 vectors (relative tolerance 1e-12)", () => {
  for (const file of specFiles("vectors/features.v1/")) {
    const v = specJson(file) as FeatureVector;
    it(v.name, () => {
      expect(v.feature_names).toEqual([...FEATURE_NAMES]);
      const model = LogisticModel.fromBundle(v.model);
      const state = new FeatureState(parseTimestampUs(v.started_at));
      for (const expected of v.expected) {
        while (state.k < expected.k) {
          const step = v.steps[state.k];
          if (!step) throw new Error(`${v.name}: no step ${String(state.k + 1)}`);
          state.update(stepFromWire(step));
        }
        const x = state.vector();
        expected.features.forEach((want, j) => {
          if (!closeTo(x[j] ?? Number.NaN, Number(want))) {
            throw new Error(`${v.name} k=${String(expected.k)} ${FEATURE_NAMES[j] ?? ""}: ${String(x[j])} != ${want}`);
          }
        });
        expect(closeTo(model.score(x), Number(expected.score))).toBe(true);
      }
    });
  }
});

interface CanonicalVector {
  name: string;
  input: unknown;
  canonical_hex: string;
  signature?: { public_key_b64: string; signature_b64: string };
}

describe("canonical-json vectors (exact)", () => {
  for (const file of specFiles("vectors/canonical-json/").filter((f) => !f.endsWith("must-reject.json"))) {
    const v = specJson(file) as CanonicalVector;
    it(v.name, () => {
      const bytes = canonicalJson(v.input);
      expect(hex(bytes)).toBe(v.canonical_hex);
      if (v.signature) {
        const sig = Buffer.from(v.signature.signature_b64, "base64");
        const key = Buffer.from(v.signature.public_key_b64, "base64");
        expect(ed25519Verify(sig, bytes, key)).toBe(true);
        const tampered = Uint8Array.from(bytes);
        const i = tampered.length - 2;
        tampered[i] = (tampered[i] ?? 0) ^ 1;
        expect(ed25519Verify(sig, tampered, key)).toBe(false);
      }
    });
  }

  const rejects = specJson("vectors/canonical-json/must-reject.json") as {
    cases: { name: string; json_text: string }[];
  };
  for (const c of rejects.cases) {
    it(`rejects ${c.name}`, () => {
      expect(() => canonicalJson(JSON.parse(c.json_text))).toThrow(CanonicalJSONError);
    });
  }
});

describe("holdout.v1 vectors (exact)", () => {
  const v = specJson("vectors/holdout.v1/buckets.json") as {
    rates: string[];
    cases: { run_id: string; bucket: number; holdout: boolean[] }[];
  };
  for (const c of v.cases) {
    it(c.run_id, () => {
      expect(holdoutBucket(c.run_id)).toBe(c.bucket);
      expect(holdoutBucket(c.run_id.toUpperCase())).toBe(c.bucket);
      expect(v.rates.map((r) => isHoldout(c.run_id, r))).toEqual(c.holdout);
    });
  }
});

describe("args-hash.v1 vectors (exact)", () => {
  const v = specJson("vectors/args-hash.v1/cases.json") as {
    cases: { name: string; salt: string; args: unknown; canonical_hex: string; args_hash: string }[];
  };
  for (const c of v.cases) {
    it(c.name, () => {
      expect(hex(canonicalJson(c.args, { allowFloats: true }))).toBe(c.canonical_hex);
      expect(argsHash(c.salt, c.args)).toBe(c.args_hash);
    });
  }
});

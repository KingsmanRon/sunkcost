// The logistic_v1 score (features.v1.md, "Score"): z = intercept + Σ_j w_j · ((x_j − μ_j) / σ_j),
// the sum formed first, and a sigmoid that never overflows. Parameters arrive as decimal strings.

import { FEATURE_INDEX } from "./features.js";

export class ModelError extends Error {
  override name = "ModelError";
}

/** A float64 decimal string as Python's float() reads it; anything else is refused. */
const DECIMAL_FLOAT = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

export function parseF64(value: unknown, what: string): number {
  if (typeof value !== "string") throw new ModelError(`${what} must be a decimal string`);
  if (!DECIMAL_FLOAT.test(value.trim())) throw new ModelError(`${what} is not a number: ${value}`);
  const n = Number(value);
  if (!Number.isFinite(n)) throw new ModelError(`${what} must be finite`);
  return n;
}

interface Term {
  index: number;
  weight: number;
  mean: number;
  std: number;
}

export class LogisticModel {
  private constructor(
    readonly intercept: number,
    private readonly terms: readonly Term[],
  ) {}

  /** Validate and parse a bundle's `model` object. */
  static fromBundle(model: unknown): LogisticModel {
    const m = (model ?? {}) as Record<string, unknown>;
    if (m.type !== "logistic_v1") throw new ModelError(`unsupported model type ${JSON.stringify(m.type)}`);
    const { features: names, weights, means, stds } = m;
    if (!Array.isArray(names) || !Array.isArray(weights) || !Array.isArray(means) || !Array.isArray(stds)) {
      throw new ModelError("features, weights, means and stds must be arrays");
    }
    if (
      names.length === 0 ||
      names.length !== weights.length ||
      names.length !== means.length ||
      names.length !== stds.length
    ) {
      throw new ModelError("features, weights, means and stds must be non-empty and equal length");
    }
    if (new Set(names).size !== names.length) throw new ModelError("duplicate feature names");
    const terms = names.map((name: unknown, j): Term => {
      const index = typeof name === "string" ? FEATURE_INDEX.get(name) : undefined;
      if (index === undefined) throw new ModelError(`unknown feature ${JSON.stringify(name)}`);
      const std = parseF64(stds[j], "std");
      if (std <= 0) throw new ModelError("every std must be > 0");
      return { index, weight: parseF64(weights[j], "weight"), mean: parseF64(means[j], "mean"), std };
    });
    return new LogisticModel(parseF64(m.intercept, "intercept"), terms);
  }

  /** Feature names in model order, for diagnostics. */
  get featureIndices(): number[] {
    return this.terms.map((t) => t.index);
  }

  score(x: readonly number[]): number {
    let s = 0;
    for (const t of this.terms) s = s + t.weight * (((x[t.index] ?? Number.NaN) - t.mean) / t.std);
    return sigmoid(this.intercept + s);
  }
}

export function sigmoid(z: number): number {
  if (z >= 0) return 1 / (1 + Math.exp(-z));
  const e = Math.exp(z);
  return e / (1 + e);
}

export function scoreLogisticV1(model: unknown, x: readonly number[]): number {
  const parsed = model instanceof LogisticModel ? model : LogisticModel.fromBundle(model);
  return parsed.score(x);
}

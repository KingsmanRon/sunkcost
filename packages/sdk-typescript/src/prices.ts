// Model prices (prices.v1) for recordStep() calls without costUsd (ADR-0002 §17), mirroring
// sunkcost.prices. Exact model or alias match; the latest entry effective on or before the step's
// UTC date (else the earliest); a long-context tier replaces both prices when the input exceeds
// it. Arithmetic is exact, rounded half-even to 9 places.

import { BUNDLED_PRICES } from "./data/prices.js";
import { divHalfEven, parseMoney, type Nanos } from "./money.js";

interface Price {
  effectiveFrom: string; // YYYY-MM-DD sorts as text
  input: Nanos;
  output: Nanos;
  tiers: { above: number; input: Nanos; output: Nanos }[]; // ascending by `above`
}

interface PriceDoc {
  schema_version: string;
  unit: string;
  models: readonly {
    model: string;
    aliases: readonly string[];
    prices: readonly {
      effective_from: string;
      input: string;
      output: string;
      tiers: readonly { above_input_tokens: number; input: string; output: string }[];
    }[];
  }[];
}

function nanos(text: string): Nanos {
  const n = parseMoney(text);
  if (n === null) throw new Error(`bad price ${text}`);
  return n;
}

export class PriceTable {
  private readonly byModel = new Map<string, Price[]>();

  constructor(doc: PriceDoc) {
    if (doc.schema_version !== "1" || doc.unit !== "per_1m_tokens") {
      throw new Error("not a prices.v1 table");
    }
    for (const entry of doc.models) {
      const prices = entry.prices
        .map((p) => ({
          effectiveFrom: p.effective_from,
          input: nanos(p.input),
          output: nanos(p.output),
          tiers: p.tiers
            .map((t) => ({ above: t.above_input_tokens, input: nanos(t.input), output: nanos(t.output) }))
            .sort((a, b) => a.above - b.above),
        }))
        .sort((a, b) => (a.effectiveFrom < b.effectiveFrom ? -1 : a.effectiveFrom > b.effectiveFrom ? 1 : 0));
      for (const name of [entry.model, ...entry.aliases]) this.byModel.set(name, prices);
    }
  }

  /** The table shipped with this SDK version (a copy of packages/spec/prices). */
  static bundled(): PriceTable {
    return new PriceTable(BUNDLED_PRICES);
  }

  has(model: string): boolean {
    return this.byModel.has(model);
  }

  /** USD cost of one call in nanos, or null when the model is not in the table. `on` is YYYY-MM-DD. */
  cost(model: string, inputTokens: number, outputTokens: number, on: string): Nanos | null {
    const prices = this.byModel.get(model);
    if (!prices) return null;
    let price: Price | undefined = prices[0];
    for (const p of prices) if (p.effectiveFrom <= on) price = p;
    if (price === undefined) return null;
    let unitIn = price.input;
    let unitOut = price.output;
    for (const tier of price.tiers) {
      if (inputTokens > tier.above) {
        unitIn = tier.input;
        unitOut = tier.output;
      }
    }
    // Units are nanos per 1M tokens, so the exact cost in nanos is (tokens × unit) / 10^6.
    const total = BigInt(inputTokens) * unitIn + BigInt(outputTokens) * unitOut;
    return divHalfEven(total, 1_000_000n);
  }
}

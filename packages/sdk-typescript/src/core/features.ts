// Features v1, maintained incrementally (packages/spec/features/features.v1.md is normative).
// A line-for-line port of sunkcost.core.features: update() is O(1) amortised and vector() is O(1),
// so check() costs the same at step 200 as at step 2. The shared golden vectors pin both SDKs.

import { FEATURE_NAMES } from "../wire.js";
import { parseTimestampUs } from "./time.js";

export { FEATURE_NAMES };
export const FEATURE_SPEC = "features.v1";
export const FEATURE_INDEX: ReadonlyMap<string, number> = new Map(
  FEATURE_NAMES.map((name, i) => [name, i]),
);

const RECENT_WINDOW = 3;
const REPEAT_WINDOW = 5;
const MIN_COST_DENOMINATOR = 1e-12;

/** The parts of a completed step that the features use. `cost` is cost_usd as a float64. */
export interface StepRecord {
  kind: string;
  model: string | null;
  toolName: string | null;
  argsHash: string | null;
  inputTokens: number;
  outputTokens: number;
  cost: number;
  error: boolean;
  tsUs: number;
}

/** Build from an ingest-batch `step` event (a decoded JSON object). */
export function stepFromWire(event: {
  kind: string;
  model: string | null;
  tool_name: string | null;
  args_hash: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
  error: boolean;
  ts: string;
}): StepRecord {
  return {
    kind: event.kind,
    model: event.model,
    toolName: event.tool_name,
    argsHash: event.args_hash,
    inputTokens: event.input_tokens,
    outputTokens: event.output_tokens,
    cost: event.cost_usd === null ? 0 : Number(event.cost_usd),
    error: event.error,
    tsUs: parseTimestampUs(event.ts),
  };
}

/** A fixed-size window that drops its oldest value (Python's deque(maxlen=n)). */
class Window<T> {
  private readonly items: T[] = [];
  constructor(private readonly size: number) {}
  push(value: T): void {
    this.items.push(value);
    if (this.items.length > this.size) this.items.shift();
  }
  values(): readonly T[] {
    return this.items;
  }
}

/** Running features.v1 state for one run. */
export class FeatureState {
  private kCount = 0;
  private cost = 0;
  private tokens = 0;
  private readonly recentCosts = new Window<number>(RECENT_WINDOW);
  private readonly actionsSeen = new Set<string>();
  private readonly repeatFlags = new Window<boolean>(REPEAT_WINDOW);
  private lastAction: string | null = null;
  private streak = 0;
  private bestStreak = 0;
  private errors = 0;
  private errorStreak = 0;
  private lastState: string | null = null;
  private readonly transitionsSeen = new Set<string>();
  private repeatedTransitions = 0;
  private firstModelInput: number | null = null;
  private lastModelInput = 0;
  private lastTsUs: number;

  constructor(private readonly startedAtUs: number) {
    this.lastTsUs = startedAtUs;
  }

  /** Number of completed steps folded in so far. */
  get k(): number {
    return this.kCount;
  }

  get totalCost(): number {
    return this.cost;
  }

  /** Fold in one completed step. */
  update(step: StepRecord): void {
    this.kCount += 1;
    this.cost += step.cost;
    this.tokens += step.inputTokens + step.outputTokens;
    this.recentCosts.push(step.cost);

    const name = step.toolName ?? step.model ?? "";
    const action = JSON.stringify([step.kind, name, step.argsHash ?? ""]);
    this.repeatFlags.push(this.actionsSeen.has(action));
    this.actionsSeen.add(action);
    this.streak = action === this.lastAction ? this.streak + 1 : 1;
    this.bestStreak = Math.max(this.bestStreak, this.streak);
    this.lastAction = action;

    if (step.error) {
      this.errors += 1;
      this.errorStreak += 1;
    } else {
      this.errorStreak = 0;
    }

    const state = JSON.stringify([step.kind, name]);
    if (this.lastState !== null) {
      const transition = `${this.lastState}${state}`;
      if (this.transitionsSeen.has(transition)) this.repeatedTransitions += 1;
      else this.transitionsSeen.add(transition);
    }
    this.lastState = state;

    if (step.kind === "model") {
      this.firstModelInput ??= step.inputTokens;
      this.lastModelInput = step.inputTokens;
    }
    this.lastTsUs = step.tsUs;
  }

  /** The 12 features for the current prefix, in FEATURE_NAMES order. */
  vector(): number[] {
    const k = this.kCount;
    let recentShare = 0;
    let repeatRate = 0;
    let logElapsed = 0;
    if (k > 0) {
      let window = 0;
      for (const c of this.recentCosts.values()) window += c;
      recentShare = window / Math.max(this.cost, MIN_COST_DENOMINATOR);
      let repeats = 0;
      for (const flag of this.repeatFlags.values()) if (flag) repeats += 1;
      repeatRate = repeats / Math.min(REPEAT_WINDOW, k);
      const elapsed = (this.lastTsUs - this.startedAtUs) / 1_000_000;
      logElapsed = Math.log1p(Math.max(0, elapsed));
    }
    const cycleRate = k < 2 ? 0 : this.repeatedTransitions / (k - 1);
    const contextGrowth =
      this.firstModelInput === null
        ? 0
        : Math.log1p(this.lastModelInput) - Math.log1p(this.firstModelInput);
    const denominator = Math.max(k, 1);
    return [
      k,
      Math.log1p(this.cost),
      Math.log1p(this.tokens),
      recentShare,
      repeatRate,
      this.bestStreak,
      this.errors / denominator,
      this.errorStreak,
      this.actionsSeen.size / denominator,
      cycleRate,
      contextGrowth,
      logElapsed,
    ];
  }
}

/** Features for a whole prefix at once (convenience wrapper over FeatureState). */
export function featuresV1(startedAtUs: number, prefix: Iterable<StepRecord>): number[] {
  const state = new FeatureState(startedAtUs);
  for (const step of prefix) state.update(step);
  return state.vector();
}

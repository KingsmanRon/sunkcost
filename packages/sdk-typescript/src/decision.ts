// The decision engine: a pure function of the run's bundle, its feature state and the clock,
// mirroring sunkcost.decision. Order of evaluation (ADR-0004):
//
// 1. no bundle for this run               → CONTINUE, no_bundle (no score)
// 2. the run's bundle has expired         → CONTINUE, bundle_expired (no score)
// 3. fewer than minSteps completed        → CONTINUE, below_min_steps
// 4. score < threshold                    → CONTINUE, below_threshold
// 5. score ≥ threshold (wouldAbort): an override → override_shadow; a shadow bundle →
//    shadow_mode; a holdout run → holdout; otherwise ABORT, above_threshold.

import type { Bundle, Override } from "./bundle.js";
import type { FeatureState } from "./core/features.js";
import { pyFloatRepr } from "./repr.js";
import type { Action, DecisionDoc, Mode, Reason, StepDecision } from "./wire.js";

export class Decision {
  constructor(
    readonly action: Action,
    /** The effective mode: shadow or enforce. */
    readonly mode: Mode,
    readonly score: number | null,
    readonly threshold: number | null,
    readonly bundleId: string | null,
    readonly bundleVersion: number | null,
    readonly wouldAbort: boolean,
    readonly reason: Reason,
  ) {}

  get abort(): boolean {
    return this.action === "ABORT";
  }

  /** decision.v1 form: floats as shortest round-trip decimal strings (Python repr). */
  toJSON(): DecisionDoc {
    return {
      schema_version: "1",
      action: this.action,
      mode: this.mode,
      score: this.score === null ? null : pyFloatRepr(this.score),
      threshold: this.threshold === null ? null : pyFloatRepr(this.threshold),
      bundle_id: this.bundleId,
      bundle_version: this.bundleVersion,
      would_abort: this.wouldAbort,
      reason: this.reason,
    };
  }

  /** The subset carried on a step event (decision.v1#/$defs/step_decision). */
  stepJson(): StepDecision {
    return {
      action: this.action,
      score: this.score === null ? null : pyFloatRepr(this.score),
      threshold: this.threshold === null ? null : pyFloatRepr(this.threshold),
      would_abort: this.wouldAbort,
      reason: this.reason,
    };
  }
}

export const NO_BUNDLE = new Decision("CONTINUE", "shadow", null, null, null, null, false, "no_bundle");
export const SDK_ERROR = new Decision("CONTINUE", "shadow", null, null, null, null, false, "sdk_error");

export function effectiveMode(bundle: Bundle | null, override: Override, nowUs: number): Mode {
  if (bundle === null || override !== null || bundle.expiresAtUs <= nowUs) return "shadow";
  return bundle.mode;
}

export function decide(
  bundle: Bundle | null,
  override: Override,
  holdout: boolean,
  state: FeatureState,
  nowUs: number,
): Decision {
  if (bundle === null) return NO_BUNDLE;
  const { bundleId: bid, version } = bundle;
  if (bundle.expiresAtUs <= nowUs) {
    return new Decision("CONTINUE", "shadow", null, null, bid, version, false, "bundle_expired");
  }
  const mode: Mode = override !== null ? "shadow" : bundle.mode;
  const score = bundle.model.score(state.vector());
  const threshold = bundle.threshold;
  if (state.k < bundle.minSteps) {
    return new Decision("CONTINUE", mode, score, threshold, bid, version, false, "below_min_steps");
  }
  if (score < threshold) {
    return new Decision("CONTINUE", mode, score, threshold, bid, version, false, "below_threshold");
  }
  let reason: Reason;
  if (override !== null) reason = "override_shadow";
  else if (bundle.mode === "shadow") reason = "shadow_mode";
  else if (holdout) reason = "holdout";
  else return new Decision("ABORT", mode, score, threshold, bid, version, true, "above_threshold");
  return new Decision("CONTINUE", mode, score, threshold, bid, version, true, reason);
}

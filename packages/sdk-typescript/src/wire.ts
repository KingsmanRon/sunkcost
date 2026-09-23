/**
 * Sunkcost v1 wire contracts, mirroring packages/spec/schemas/*.v1.json.
 *
 * Field names are the JSON (snake_case) names. Decimal quantities are strings, never JSON numbers:
 * money has at most 9 fractional digits; scores, thresholds and model parameters are float64 values
 * written as decimal strings. Timestamps are UTC RFC 3339 strings ending in "Z".
 */

export type Uuid = string;
export type Timestamp = string;
/** Non-negative USD amount, at most 9 fractional digits, e.g. "0.002000". */
export type Money = string;
/** A float64 written as a decimal string, e.g. "0.842106" or "1e-05". */
export type F64String = string;

export type Mode = "shadow" | "enforce";
export type Action = "CONTINUE" | "ABORT";
export type Outcome = "success" | "failure" | "abandoned";
export type Reason =
  | "no_bundle"
  | "bundle_expired"
  | "below_min_steps"
  | "below_threshold"
  | "above_threshold"
  | "holdout"
  | "shadow_mode"
  | "override_shadow"
  | "sdk_error";

export const FEATURE_NAMES = [
  "step_index",
  "log_cost",
  "log_tokens",
  "recent_cost_share",
  "repeat_action_rate",
  "max_identical_streak",
  "error_rate",
  "error_streak",
  "distinct_action_ratio",
  "cycle_rate",
  "context_growth",
  "log_elapsed_s",
] as const;
export type FeatureName = (typeof FEATURE_NAMES)[number];

/** decision.v1 $defs/step_decision: the check() decision carried on a step event. */
export interface StepDecision {
  action: Action;
  score: F64String | null;
  threshold: F64String | null;
  would_abort: boolean;
  reason: Reason;
}

/** decision.v1 */
export interface Decision extends StepDecision {
  schema_version: "1";
  mode: Mode;
  bundle_id: Uuid | null;
  bundle_version: number | null;
}

export interface RunStartEvent {
  type: "run_start";
  run_id: Uuid;
  workload: string;
  external_ref: string | null;
  started_at: Timestamp;
  bundle_id: Uuid | null;
  bundle_version: number | null;
  mode: Mode;
  holdout: boolean;
}

export interface StepEvent {
  type: "step";
  run_id: Uuid;
  seq: number;
  ts: Timestamp;
  kind: "model" | "tool";
  model: string | null;
  tool_name: string | null;
  /** "hmac-sha256:" followed by 64 lower-case hex digits. */
  args_hash: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: Money | null;
  error: boolean;
  latency_ms: number | null;
  decision: StepDecision | null;
}

export interface RunEndEvent {
  type: "run_end";
  run_id: Uuid;
  ended_at: Timestamp;
  status: "completed" | "aborted" | "errored";
  total_cost_usd: Money;
  steps: number;
  would_abort_step: number | null;
  cost_through_would_abort_usd: Money | null;
  aborted_step: number | null;
}

export interface OutcomeEvent {
  type: "outcome";
  run_id: Uuid;
  outcome: Outcome;
  source: string;
  reported_at: Timestamp;
}

export type IngestEvent = RunStartEvent | StepEvent | RunEndEvent | OutcomeEvent;

/** ingest-batch.v1: body of POST /v1/ingest. */
export interface IngestBatch {
  schema_version: "1";
  sent_at: Timestamp;
  sdk: { name: string; version: string };
  dropped_events: number;
  events: IngestEvent[];
}

interface OutcomeBodyBase {
  schema_version: "1";
  outcome: Outcome;
  source: string;
  reported_at?: Timestamp;
}

/** outcome.v1: body of POST /v1/outcomes, addressed by run_id or by (workload, external_ref). */
export type OutcomeBody =
  | (OutcomeBodyBase & { run_id: Uuid })
  | (OutcomeBodyBase & { workload: string; external_ref: string });

export interface LogisticV1 {
  type: "logistic_v1";
  intercept: F64String;
  features: FeatureName[];
  weights: F64String[];
  means: F64String[];
  stds: F64String[];
}

/** bundle.v1: the signed payload. */
export interface Bundle {
  schema_version: "1";
  bundle_id: Uuid;
  org_id: Uuid;
  project_id: Uuid;
  workload_id: Uuid;
  workload: string;
  version: number;
  feature_spec: "features.v1";
  mode: Mode;
  holdout_rate: string;
  model: LogisticV1;
  decision: {
    threshold: F64String;
    min_steps: number;
    target_recall: string;
    delta: string;
  };
  metrics: {
    n_train: number;
    n_calib_success: number;
    test_false_abort_rate: F64String;
    test_savings_share: F64String;
    auroc: F64String;
    [name: string]: number | F64String;
  };
  issued_at: Timestamp;
  expires_at: Timestamp;
}

/** bundle-response.v1: body of GET /v1/bundles/active. */
export interface BundleResponse {
  schema_version: "1";
  bundle: Bundle;
  key_id: string;
  /** Standard base64 of the Ed25519 signature over canonical_json(bundle). */
  signature: string;
  /** Unsigned; can only reduce enforcement. */
  override: "shadow" | null;
}

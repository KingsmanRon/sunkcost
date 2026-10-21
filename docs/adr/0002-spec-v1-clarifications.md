# ADR-0002: Spec v1 clarifications from writing the contracts

- Status: accepted
- Date: 2026-09-23
- Deciders: implementing engineer; recorded for owner review in the P0 PR

## Context

Writing `packages/spec` (schemas, normative feature definition, golden vectors) exposed places where
`docs/ARCHITECTURE.md` is ambiguous, leaves a case undefined, or could not work as literally
written. Golden vectors are normative (§7.1), so every such point has to be decided before P1.

## Decisions

### Features v1 (§7.1)

1. **`context_growth`** uses the most recent *model* step in the prefix:
   `log1p(input_tokens_last_model_step) − log1p(input_tokens_first_model_step)`. Read literally,
   `input_tokens_k` is the input of step `k`, which is 0 for most tool steps. The feature would
   then swing to `−log1p(first)` after every tool call instead of measuring context growth.
2. **`log_elapsed_s`** is measured at the timestamp of the last completed step, `ts_k`, not at the
   wall-clock time of the `check()` call, and is `0` when `k = 0`. The feature must be a pure
   function of recorded data, or the worker could not reproduce at training time what the SDK saw
   at decision time. Negative elapsed time (client clock stepped back) is clamped to 0. Timestamps
   are converted to integer microseconds (extra digits truncated) before subtracting.
3. **Action names**: `tool_name or model` means `tool_name` if non-null, else `model` if non-null,
   else `""`; a null `args_hash` is `""`.
4. **Arithmetic order** is fixed where the text allowed several readings: sums run in step order
   from `0.0`; `recent_cost_share` sums its window separately; the score is
   `z = intercept + Σ_j w_j · ((x_j − μ_j) / σ_j)`, with the sum formed first; the sigmoid uses
   `1/(1+e^−z)` for `z ≥ 0` and `e^z/(1+e^z)` otherwise. "Relative tolerance 1e-12" means
   `|a − b| ≤ 1e-12 · max(|a|, |b|)`.

The full definition is `packages/spec/features/features.v1.md`.

### Wire contracts (§6)

5. **`run_end` carries the run's decision summary**: `would_abort_step`,
   `cost_through_would_abort_usd` and `aborted_step`. In enforce mode an `ABORT` means the next
   step never runs, so no step event can carry that decision. And the event buffer may drop step
   events, which must not corrupt savings accounting.
6. **`dropped_events`** is a required batch-header field (§10 says dropped counts are reported
   there). Every ingest event field is required, with `null` for absent values, so the wire form is
   unambiguous.
7. **`cost_usd` may be `null`** when the caller gave no cost and the model is not in `prices.v1`.
   Unknown costs count as 0 in features and savings, which under-reports savings rather than
   inventing them. `latency_ms` and a step's `decision` (no `check()` before the step) may also be
   null.
8. **`run_start.mode` is the effective mode**: `shadow` when there is no usable bundle or an
   override is set. Runs without a bundle are therefore uncensored shadow runs that the first
   calibration can use. A run is evaluated against the bundle it started with (`bundle_id` on
   `run_start`); an override or expiry still takes effect immediately.
9. **Bundles include `org_id` and `project_id`.** Workload names are unique only per project, so a
   bundle replayed from another tenant could otherwise pass the SDK's workload-name check. The SDK
   can pin these IDs later without a schema change.
10. **`schema_version`** is carried by every document type: batch, outcome, bundle, bundle
    response and decision. `common.v1.json` holds shared definitions only.
11. **Outcomes** (`outcome.v1`) name the run either by `run_id` or by `(workload, external_ref)`,
    never both. `reported_at` is optional and informational; the server's receive time is
    `outcome_at`. All three outcome values are final, and the first one recorded wins.
12. **Timestamps** are UTC RFC 3339 with a literal `Z`; IDs are lower-case; `run_id` must be a
    UUIDv7. Rates in bundles (`holdout_rate`) have at most 4 decimals, and `target_recall` and
    `delta` lie strictly between 0 and 1.

### Canonical JSON, hashing and holdout (§4 C5, §9)

13. **Canonical JSON** follows RFC 8785: keys sorted by UTF-16 code units, integers limited to
    ±(2^53 − 1), and unpaired surrogates rejected. Signed content still admits no floats.
14. **Argument hashing uses full RFC 8785, floats included.** Tool arguments routinely contain
    floats, and the float-free subset would reject them. So `args_hash` is
    `hmac-sha256:` + HMAC-SHA256(UTF-8 salt, JCS(args)), serialising numbers the way ECMAScript
    does.
15. **Holdout bucket**: `sha256(run_id)[:8]` means the first 8 hexadecimal digits (32 bits) of the
    SHA-256 of the lower-case `run_id` string. `holdout_rate × 10000` is exact because rates have
    at most 4 decimals.

### Test signing key and prices

16. Vectors are signed with the RFC 8032 section 7.1 TEST 1 key (`key_id` `rfc8032-test-1`). The key is
    published in the RFC, so committing it discloses nothing. It must never appear in an SDK's
    default trusted keys; P2 and P6 add tests for that.
17. **Price lookup** (for `record_step` without `cost_usd`): exact match on model ID or alias; the
    latest `effective_from` on or before the step's date, falling back to the earliest entry; an
    optional long-context tier replaces both prices for that request; exact decimal arithmetic,
    rounded half-even to 9 digits. `effective_from` is the first date an entry is known to apply,
    which is the retrieval date unless the source states when the price took effect.

## Consequences

- `docs/ARCHITECTURE.md` §4 C2, §4 C5, §6.1, §6.2, §7.1 and §9 now point at these decisions.
- The P1 core must reproduce `packages/spec/vectors/` exactly as generated by the independent
  reference implementation in `packages/spec/tools/gen_vectors.py`.

# Features v1 and the `logistic_v1` score (normative)

This document is the definition referred to by `feature_spec: "features.v1"` in a policy bundle and by
§7.1 of `docs/ARCHITECTURE.md`. The golden vectors in `../vectors/features.v1/` are normative too: an
implementation conforms when it reproduces every vector within a relative tolerance of 1e-12
(`|a − b| ≤ 1e-12 · max(|a|, |b|)`, so zero must be exactly zero). Clarifications of the architecture
text are recorded in `docs/adr/0002-spec-v1-clarifications.md`.

## Inputs

The features are a pure function of the run's `started_at` and the prefix of completed steps
`1..k` (`k ≥ 0`) at the moment `check()` is called. `check()` before step `k + 1` therefore sees `k`
completed steps. Each step contributes the wire fields of its `step` event (`ingest-batch.v1`):
`kind`, `model`, `tool_name`, `args_hash`, `input_tokens`, `output_tokens`, `cost_usd`, `error`, `ts`.

No clock is read: time enters only through recorded timestamps, so the SDK at decision time and the
worker at training time compute identical values from identical data.

## Arithmetic

- All non-integer arithmetic is IEEE-754 binary64 with round-to-nearest-even, performed in exactly
  the order written below. Sums run in step order `i = 1..k` and start from `0.0`.
- `c_i` is step `i`'s `cost_usd` parsed from its decimal string to the nearest float64 (Python
  `float(s)`, JavaScript `Number(s)`), or `0.0` when `cost_usd` is null.
- `n_i = input_tokens_i + output_tokens_i` is an exact integer. Token sums are exact integers,
  converted to float64 once (every sum allowed by the schema is below 2^53).
- `log1p` and `exp` are the platform's IEEE-754 functions (Python `math.log1p`/`math.exp`,
  JavaScript `Math.log1p`/`Math.exp`). They may differ by an ulp between platforms, which the
  tolerance absorbs.
- `us(t)` is a timestamp as integer microseconds since the Unix epoch. Fractional digits beyond the
  sixth are truncated, not rounded.

## Actions and states

For step `i`:

- `name_i` = `tool_name_i` if it is non-null, else `model_i` if it is non-null, else `""`.
- `h_i` = `args_hash_i` if it is non-null, else `""`.
- Action `a_i = (kind_i, name_i, h_i)`. Abstract state `s_i = (kind_i, name_i)`.
- Two actions (or states, or transitions) are equal when every component is the same string.

## The 12 features, in vector order

| # | Name | Value at prefix `k` |
|---|---|---|
| 0 | `step_index` | `float(k)` |
| 1 | `log_cost` | `log1p(C)` where `C = Σ_{i=1..k} c_i`; `0.0` when `k = 0` |
| 2 | `log_tokens` | `log1p(float(Σ_{i=1..k} n_i))` |
| 3 | `recent_cost_share` | `0.0` if `k = 0`; else `R / max(C, 1e-12)` where `R = Σ_{i=max(1,k−2)..k} c_i` (a separate sum over the window, in step order, starting from `0.0`) |
| 4 | `repeat_action_rate` | `0.0` if `k = 0`; else the number of `i` in `max(1,k−4)..k` for which some `j < i` has `a_j = a_i`, divided by `min(5, k)`. Earlier steps `j` may lie outside the window |
| 5 | `max_identical_streak` | length of the longest block of consecutive steps with equal actions; `0.0` when `k = 0` |
| 6 | `error_rate` | `float(E) / float(max(k, 1))` where `E` is the number of steps with `error = true` |
| 7 | `error_streak` | number of consecutive steps with `error = true` ending at step `k`; `0.0` when `k = 0` |
| 8 | `distinct_action_ratio` | number of distinct actions among `a_1..a_k`, divided by `max(k, 1)` |
| 9 | `cycle_rate` | `0.0` if `k < 2`; else, with transitions `t_i = (s_{i−1}, s_i)` for `i = 2..k`, the number of `i` for which some `j` with `2 ≤ j < i` has `t_j = t_i`, divided by `k − 1` |
| 10 | `context_growth` | `0.0` if no step in `1..k` has `kind = "model"`; else `log1p(float(input_tokens_last)) − log1p(float(input_tokens_first))`, where `first` and `last` are the earliest and the most recent model steps in the prefix |
| 11 | `log_elapsed_s` | `0.0` if `k = 0`; else `log1p(max(0.0, e))` where `e = float(us(ts_k) − us(started_at)) / 1000000.0` |

Counts (features 0, 4, 5, 7, 8, 9 and the numerators of 6) are exact integers converted to float64
before the single division shown. Negative elapsed time (a client clock stepped backwards) is clamped
to zero.

## Score: `logistic_v1`

A bundle's `model` lists `features` (a subset of the names above, in any order) with parallel
`weights`, `means` and `stds`; each value is a float64 decimal string, parsed to the nearest float64.
Every `std` is greater than 0 (the trainer replaces a standard deviation of 0 with 1).

```
s = 0.0
for j in 0 .. len(features) − 1:
    z_j = (x[features[j]] − means[j]) / stds[j]
    s   = s + weights[j] * z_j
z = intercept + s
score = 1 / (1 + exp(−z))        if z ≥ 0
score = e / (1 + e), e = exp(z)  if z < 0
```

## Decision rule

With bundle threshold `τ` and `min_steps`: a check at prefix `k` would abort exactly when
`k ≥ min_steps` and `score ≥ τ`. Everything else about the decision (mode, holdout, override,
expiry) is defined in `docs/ARCHITECTURE.md` §4 C2.

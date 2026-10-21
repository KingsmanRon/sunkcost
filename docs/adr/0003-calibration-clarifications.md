# ADR-0003: Calibration and benchmark clarifications

- Status: accepted
- Date: 2026-09-23
- Deciders: implementing engineer; recorded for owner review in the P1 PR

## Context

Implementing §7.2–§7.4 as the worker calibration library (`services/worker/sunkcost_worker/calibration`)
and running it on public data (`bench/`) required decisions the architecture leaves open.

## Decisions

### Calibration (§7.2–§7.3)

1. **Decision points are k = min_steps .. n − 1.** `check()` runs before each step, so a run of n
   steps has checks at k = 0 .. n − 1 and none after its final step. Training rows, each success's
   maximum score m_i and every test-split abort use exactly these prefixes. Including k = n would
   count false aborts, and train on prefixes, that can never occur in production. A run with
   n ≤ min_steps has no decision points: it still counts in the calibration denominator but can
   never be aborted.
2. **Each run's rows share a total weight of 1** (each row weighs 1 / #rows of its run). This is the
   stated intent of "weighted 1/len(run) so long runs don't dominate". Using 1/n with n − min_steps
   rows would still give long runs more total weight than short ones.
3. **Model selection.** Features are standardised with the unweighted mean and population standard
   deviation of the training rows; a standard deviation of 0 becomes 1. L2 logistic regression
   (scikit-learn, lbfgs) picks C from 10^−4 .. 10^4 (9 values) by weighted log-loss under 5-fold
   **grouped** CV: groups are runs, or tasks when the data names them, so no run's prefixes straddle
   folds. Ties go to the smaller C.
4. **Scores during calibration come from `sunkcost.core.LogisticModel`,** parsed back from the
   bundle's decimal strings, not from scikit-learn's `predict_proba`. The thresholds are then
   computed with arithmetic bit-identical to the SDK's.
5. **Split rounding**: runs sorted by `(started_at, run_id)`; train = first ⌊3n/5⌋, calibration up
   to ⌊4n/5⌋, test the rest. The minimums (100 successes, 30 failures) apply to the calibration and
   test splits.
6. **τ = 1.0 is not "never abort"** when a score saturates to exactly 1.0 in float64. Such a success
   counts as a false abort at τ = 1.0, and the job can end `cannot_meet_recall`. This is correct:
   that bundle would abort the run.
7. **Evaluation**:
   - The realised false-abort rate gets a two-sided 95% Clopper–Pearson interval.
   - AUROC ranks each test run by its maximum decision-point score; runs without decision points rank
     lowest.
   - The validation gate compares the point estimate of the test false-abort rate with α.
8. **Extra bundle metrics** (integers or float64 strings, allowed by `bundle.v1`): `n_train_rows`,
   `n_calib_failure`, `n_test_success`, `n_test_failure`, `calib_false_aborts`, `calib_ucb`,
   `l2_c`, `test_fa_ci_low`, `test_fa_ci_high`, `test_failed_spend_saved_share`,
   `test_failures_caught`, and `test_mean_saved_cost_usd`. The last is the §7.4 per-abort estimate:
   the mean remaining cost of aborted failures in the test split.

### Benchmark (§4 C10, S1)

9. **Arrival order.** Every public dataset available was produced one agent model at a time. Raw
   start times would put whole models into the test split, and the guarantee's exchangeability
   assumption would then fail. The primary protocol re-times each run to `(rank + 0.5) / batch
   size` within its own batch, keeping each batch's own chronology, and then applies the worker's
   time split unchanged. Results on raw start times are reported alongside as a model-switch
   robustness check.
10. **S1 is read on the primary protocol at target recall 0.90.** The policy calibrated there must
    keep ≥ 90% of test successes and save more than 0% of failed-run spend. Validation-gate status
    is reported separately.
11. **Pooling.** A dataset pools the runs of several agent models on one harness into one workload.
    The architecture's calibration minimums apply to the pooled runs, and no settings are tuned per
    dataset.
12. **Post-hoc sensitivity protocol** (added after the primary results were seen). SWE-bench
    records its instances in instance-ID order, which groups them by repository. Its time split
    therefore calibrates on one set of repositories and tests on another: 1% of its test runs
    repeat a task seen in training, against 100% for every τ dataset. That is a task-mix shift,
    and exchangeability fails. The sensitivity protocol puts each batch in a seeded random order
    (seed `sunkcost-bench-v1`) before the re-timing of decision 9, so every split is a random
    sample of every batch. It is reported alongside the primary protocol, together with each
    order's share of test runs that repeat a training task. S1 stays on the primary protocol, as
    decision 10 fixed before the results were seen.

## Consequences

- `docs/ARCHITECTURE.md` §7.2 and §7.3 now state decisions 1–3, 5 and 7: the decision points, the run
  weighting, model selection, split rounding and the evaluation details.
- The G2b simulation exercises the complete procedure, including training.

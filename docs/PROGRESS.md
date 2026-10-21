# Progress

One dated entry per phase (CLAUDE.md): what was built, gate results, open issues.

## 2026-09-23 · P0: scaffold, tooling, contracts

### Built

- **Monorepo scaffold.** uv workspace (Python 3.12 for development; ruff, strict mypy, pytest)
  and pnpm workspace (Node 22; ESLint 10 with typescript-eslint type-checked rules;
  TypeScript 6.0 `tsc --noEmit`). The `Makefile` has a target for every §13 gate; gates not
  built yet exit 2 with `NOT RUN`.
- **`packages/spec`.** Seven JSON Schemas (2020-12) and Pydantic v2 models that mirror them. There
  are 17 valid, 62 invalid and 12 semantic-only invalid fixtures. The conformance tests show that
  the schemas and models accept and reject the same documents, and that every valid fixture
  round-trips unchanged.
- **Normative `features.v1.md` and golden vectors**, produced by an independent reference
  generator:
  - 13 feature/score sequences, including a 200-step run and both sigmoid limits
  - 5 canonical-JSON + Ed25519 cases and 5 must-reject inputs
  - 56 holdout-bucket cases, including every rate boundary
  - 9 argument-hash cases
- **`prices.v1.json`**: 18 Anthropic models from the official pricing page, sources in
  `packages/spec/prices/SOURCES.md`.
- **CI skeleton**: G0, G2 and a gitleaks history scan, with actions pinned by commit SHA and
  read-only permissions; Dependabot for uv, npm and GitHub Actions.
- **ADRs**: ADR-0001 adopts the architecture. ADR-0002 records 17 clarifications from writing the
  contracts, and `ARCHITECTURE.md` was updated to match.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff 0 errors and format clean, ESLint 0 warnings, mypy strict 0 errors in 8 files, tsc 0 errors. Run locally and from a fresh clone. |
| G2 Unit (spec only) | `make test-unit` | **PASS**: 166 passed, 1 skipped (the empty-run vector has no steps to validate). |
| Secret scan | gitleaks 8.30.1 `git` | **PASS**: no leaks. The public RFC 8032 test key is allowlisted by exact value. |

The other gates start in later phases and report `NOT RUN` until then.

### Environment

- The Docker daemon was not running and had to be started by hand. Docker Hub anonymous pulls
  return 429 and `public.ecr.aws` blob downloads are blocked, so the daemon uses `mirror.gcr.io` as
  a registry mirror.
- Supabase CLI 2.117.0 was installed from its GitHub release (checksum verified). A trial
  `supabase start` brought up Postgres, Auth, PostgREST, Kong and Mailpit, so G5 and G6 can run
  locally against a real database from P3.
- Neither setup survives a new session container; both are repeated per session.

### Open issues

1. **Price table covers Anthropic only.** The official OpenAI pages (`openai.com`,
   `platform.openai.com`) and the pages giving Google's Gemini model IDs (`ai.google.dev`,
   `docs.cloud.google.com`) are blocked by this environment's egress policy. P1 needs them if a
   benchmark dataset records tokens but not cost for those models.
2. **ADR-0002 is waiting for owner review**, in particular the `context_growth` and
   `log_elapsed_s` definitions and the decision summary on `run_end`.
3. **The TypeScript SDK package stays `private`** until P6/P7; npm trusted publishing is an owner
   action.

## 2026-09-23 · P1: core, calibration library, benchmark

### Built

- **Core** (`packages/sdk-python`, `sunkcost.core`; pure Python ≥ 3.10, no dependencies):
  incremental features v1, `logistic_v1` scoring, canonical JSON (no floats in signed content;
  full RFC 8785 for argument hashing), holdout buckets, argument HMAC and UTC time helpers. It
  matches every golden vector bit for bit.
- **Worker calibration library** (`services/worker`, `sunkcost_worker.calibration`): §7.2–§7.4 as
  a library. It covers the time split and minimums, weighted L2 logistic regression with grouped
  5-fold CV, Clopper–Pearson fixed-sequence threshold selection, test-split evaluation and the
  validation gate, and §7.4 accounting.
- **G2b simulation** (`sunkcost_worker.simulation`): 500 synthetic workloads with known failure
  processes across four (target recall, δ) cells. CI now runs it as its own job.
- **Benchmark** (`bench/`):
  - Loaders for τ²-bench (retail, telecom, airline), τ-bench retail, and SWE-bench Verified
    (eleven mini-SWE-agent 2.0.0 entries).
  - `make bench`, trade-off plots, and `docs/BENCHMARKS.md`.
  - Downloads are verified and cached, never committed. Only step metadata and argument HMACs are
    kept. Provenance and licences are in `bench/DATASETS.md`.
- **ADR-0003**: 12 calibration and benchmark decisions. `ARCHITECTURE.md` §7.2–§7.3 now state
  them.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff 0 errors and format clean, ESLint 0 warnings, mypy strict 0 errors in 45 files, tsc 0 errors. |
| G1 Proof | `make bench` | **PASS**: S1 met on 2 of 5 datasets (τ²-bench telecom, τ-bench retail), both MIT-licensed. `docs/BENCHMARKS.md` regenerated from `1217d74`. |
| G2 Unit | `make test-unit` | **PASS**: 383 passed, 1 skipped. The TypeScript SDK half of the feature-vector check arrives in P6. |
| G2b Statistical | `make test-stats` | **PASS**: 500 workloads (463 validated, 37 rejected). The share of workloads above α was 0.008, 0.040, 0.032 and 0.040, against limits of 0.08, 0.13, 0.08 and 0.13 (cells 0.95/0.05, 0.95/0.10, 0.90/0.05, 0.90/0.10). |
| Secret scan | gitleaks 8.30.1 `git` | **PASS**: no leaks. |

P0's PR (#1) is green in CI: G0, G2 and gitleaks.

### Benchmark at target recall 0.90 (test split)

| Dataset | Successes kept | Failed-run spend saved | S1 | Validation gate |
|---|---|---|---|---|
| τ²-bench retail | 89.4% | 3.4% | fail | rejected |
| τ²-bench telecom | 93.4% | 10.8% | pass | validated |
| τ²-bench airline | – | – | fail (insufficient data) | – |
| τ-bench retail | 94.4% | 4.6% (steps) | pass | rejected (savings share 1.8% < 2%) |
| SWE-bench Verified | 87.2% | 28.1% | fail | rejected (false aborts above α) |

The results at 0.95, the confidence intervals and the curves are in `docs/BENCHMARKS.md`.

### Open issues

1. **Owner decision: continue or kill.** P2 does not start until the owner says continue.
2. **SWE-bench fails because of its run order, not a lack of signal.** Its harness ran instances in
   ID order, which groups them by repository. The time split therefore tests on repositories that
   the calibration split never saw: 1% of test runs repeat a training task, against 100% for the
   τ datasets. Under the post-hoc shuffled order (ADR-0003 decision 12), SWE-bench keeps 94.1% of
   successes and saves 14.5% of failed-run spend. A production workload whose task mix drifts
   over time breaks the guarantee in the same way. The drift monitor (§7.5, P4) has to catch that.
3. **The SWE-bench trajectories have no licence file.** The owner should confirm this use or drop
   the dataset. S1 holds without it.
4. **τ²-bench retail has little signal** (AUROC 0.54) and misses S1 narrowly. τ²-bench airline is
   below the calibration minimums.
5. **Only τ²-bench telecom passes the validation gate.** τ-bench retail meets S1, but its savings
   share (1.8% of all spend) is below `min_savings_share`.
6. **arXiv 2608.23670 is unreadable here**, so the datasets it lists were not checked. The price
   table is still Anthropic-only; P1 did not need it, because every dataset records cost or has no
   tokens.

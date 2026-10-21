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

## 2026-10-21 · P2: Python SDK, adapters, examples

The owner said continue after P1; PRs #1 and #2 were merged into `main`.

### Built

- **Python SDK** (`packages/sdk-python`, `sunkcost`; Python ≥ 3.10; deps `httpx`, `cryptography`):
  - `Sunkcost` / `Run` with `check()`, `record_step()`, `end()` and `outcome()`.
  - Decision engine: a pure function, with no I/O in `check()`.
  - Bundle manager:
    - Ed25519 verification against pinned keys.
    - Workload, expiry and rollback checks, ETag refresh and an optional disk cache.
    - The unsigned override can only downgrade to shadow.
  - Bounded event buffer and flusher: drops the oldest events on overflow, reports
    `dropped_events`, backs off with jitter up to 60 s, and flushes on exit within 3 s.
  - Price-table costing and argument HMAC.
  - Fail-open everywhere: internal errors return `CONTINUE` / `sdk_error`.
- **Adapters**: `sunkcost-langgraph` (callback handler), `sunkcost-openai-agents` (run hooks and
  `run_agent`) and `sunkcost-cycles` (`guard` above `@cycles`, plus `reserve`/`commit`). Each hook
  point was verified against the framework's released source.
- **Examples** (`examples/`): a plain loop, LangGraph, OpenAI Agents and Cycles. All run offline
  with fake LLMs and a bundle signed with the public test key, and each shows a real ABORT.
- **ADR-0004**: 12 SDK and adapter decisions. `ARCHITECTURE.md` §4 C2, §4 C4 and §5.1 are updated
  to match. `sunkcost_spec.validation` now holds the schema validator shared by the spec and SDK
  tests.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff 0 errors and format clean, ESLint 0 warnings, mypy strict 0 errors in 75 files, tsc 0 errors. |
| G2 Unit | `make test-unit` | **PASS**: 478 passed, 1 skipped. This includes every SDK event validated against `ingest-batch.v1` (schema and model), decision reasons against `decision.v1`, and the bundle-verification and buffer cases. The TypeScript half arrives in P6. |
| G3 SDK perf (Python) | `make bench-sdk` | **PASS**: `check()` at step 200, p50 0.005 ms, p99 0.021 ms (limit 0.5 ms), 0 network connects. Recorded in `docs/PERF.md`. |
| G4 Failure (Python) | `make test-failure` | **PASS**: 12 passed against a real local HTTP server. Cases: API down, API slow (10 s), 500s then recovery, bad signature, expired bundle, rollback, override, buffer overflow, process exit with and without a hung API, and `sdk_error` injection. The agent completed every run. |
| SDK on Python 3.10 | `make test-sdk-py310` | **PASS**: 195 passed (unit tests and G4), on a fresh resolve. |

Node halves of G3 and G4 are **NOT RUN** until the TypeScript SDK is built in P6.

### Open issues

1. **No production trusted key or endpoint yet.** The owner generates the signing key offline;
   its public half goes into `sunkcost/keys.py` before release (P7).
2. **ADR-0004 needs owner review**, in particular decision 1 (404 clears the cached bundle) and
   decision 12 (the Cycles `guard` decorator).
3. **Ingest and bundle endpoints are defined but not yet served.** The API arrives in P3;
   ADR-0004 decisions 1 and 4 are the contract it must meet.

## 2026-10-21 · P3: Supabase schema, row-level security, API

### Built

- **Schema** (`supabase/migrations/`, Postgres 17): every §8 table, plus `signing_keys`.
  - UUIDv7 defaults.
  - Money stored as `numeric(20,9)`.
  - `step_events` is range-partitioned by the month the server first received the run, with
    monthly partitions and a default partition.
  - At most one active bundle per workload.
  - `audit_log` is append-only, enforced by revoked grants and a trigger that refuses even the
    table owner.
- **Access control** (§8.1):
  - RLS on every org table.
  - The console role reads its own orgs, `SELECT` only, and the secret hashes are excluded by
    column grants.
  - `sunkcost_api` and `sunkcost_worker` are login roles without `BYPASSRLS`; they act for
    `app.org_id`.
  - Four `SECURITY DEFINER` functions, each with a pinned `search_path`.
  - `anon` has nothing, and the `service_role` key is used nowhere.
- **API** (`services/api`, FastAPI + psycopg 3, prepared statements off): every §4 C6 endpoint.
  - Idempotent ingest per §8.2, with per-event savepoints and placeholder runs.
  - Outcomes with first-wins conflict auditing.
  - Bundle serving with ETag/304.
  - API keys (SHA-256 stored, constant-time compare, scopes, revocation, `last_used_at`).
  - Supabase JWT auth via JWKS, with role checks and an audit entry for every mutation.
  - RFC 9457 errors with `x-request-id`, a per-key token-bucket rate limit, 1 MB body limit
    after decompression, and browser origins refused on API-key routes.
  - JSON logs and Prometheus `/metrics`.
- **Local stack**: `services/api/Dockerfile` (digest-pinned base, non-root) and
  `docker-compose.yml`.
- **ADR-0005**: 13 decisions, including the partitioning fix for a §8/§10 contradiction.
  `ARCHITECTURE.md` §4 C6, §8, §8.1 and §8.2 are updated to match.
- **CI**: a job runs G5 and G6 against a fresh local Supabase, using a checksum-verified CLI.
  Another job builds the API image.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff 0 errors and format clean, ESLint 0 warnings, mypy strict 0 errors in 90 files, tsc 0 errors. |
| G2 Unit | `make test-unit` | **PASS**: 489 passed, 1 skipped. |
| G5 Isolation | `make test-db` | **PASS**: `supabase db reset`, then 57 pgTAP tests (tenant A cannot read or write B as console user, API role or worker role; definer functions are callable only by their role; `audit_log` is immutable even for its owner; the console role cannot write), then 4 PostgREST HTTP tests with real Supabase Auth tokens. |
| G6 Integration (API parts) | `make test-integration` | **PASS**: 17 tests against local Supabase, covering duplicate batches, placeholder runs, late and conflicting outcomes, per-event rejection, key scopes and revocation, roles, tenant isolation through the API, bundle activation, ETag/304 and override, and an SDK → API → Postgres end-to-end run that aborts. |

The worker parts of G6 (calibration → validated bundle → SDK) are **NOT RUN** until P4 builds
the worker.

### Environment

- Supabase CLI 2.117.0 and Docker were available. `supabase start` ran with Realtime, Studio,
  Storage, Edge Runtime and Analytics disabled (`supabase/config.toml`), since Sunkcost doesn't
  use them.
- The sandbox's HTTPS proxy is not trusted inside `docker build`. The API image was built locally
  with the proxy CA passed as a BuildKit secret, which is never stored in a layer. CI builds need
  no secret.

### Open issues

1. **Role passwords for staging and production** are owner actions (`alter role … password`), as
   are the Supabase, Railway and Vercel projects (P7).
2. **ADR-0005 needs owner review**, in particular decisions 1 (partitioning), 7 (workloads
   created on first use) and 12 (who may clear an override).
3. **The ingest path makes several round trips per event.** Throughput is measured in P7
   (`make load-test`); batching the SQL is the first optimisation if it misses 1,000 events/s.

## 2026-10-21 · P4: worker service

### Built

- **Worker service** (`services/worker/sunkcost_worker/service/`):
  - A job runner that claims jobs with `SKIP LOCKED` and retries failures with backoff, then
    marks them `dead`.
  - A scheduler that runs only while it holds a Postgres advisory lock. It deduplicates per
    period with `jobs.dedupe_key`.
  - An internal `/healthz` endpoint and JSON logs.
- **Job handlers**:
  - `calibrate`: produces a validated bundle (signed) or a rejected bundle (stored unsigned).
  - `resign_bundle`: the daily re-sign, plus a new version when the mode changes.
  - `build_report`, from observed spend only.
  - `drift_check`: downgrades the workload to shadow, writes an audit entry and sends a
    webhook.
  - `retention` (90 days) and `webhook`, which is HMAC-signed, retried 5 times, and blocks
    private destinations.
- **Signing**:
  - The worker publishes its Ed25519 public key to `signing_keys` on start.
  - `scripts/gen-signing-key.py` lets the owner generate the production key offline. It prints
    the key and writes nothing.
- **Migration `20261021000300_worker.sql`**:
  - Job dedupe keys and results.
  - `list_due_work()` now also lists due and stale jobs.
  - Webhook secrets in Supabase Vault, behind `set_org_webhook()` (API) and
    `org_webhook_target()` (worker).
- **API**: `PUT /v1/orgs/webhook` (admin) creates the org's webhook secret and returns it once.
- **Local stack**: a worker image (digest-pinned base, non-root) and a compose service. The
  compose service signs with the public RFC 8032 test key.
- **ADR-0006**: 10 decisions. It fixes a contradiction in §5.1 ⑧ and §8: an HMAC needs the
  secret, but only its hash was stored, so the secret now lives in Vault. `ARCHITECTURE.md` is
  updated to match.
- **CI**: the image job now builds both the API and worker images.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff, ESLint and gitleaks clean; mypy strict 0 errors in 100 files; tsc 0 errors. |
| G2 Unit | `make test-unit` | **PASS**: 508 passed, 1 skipped. These include the worker's signer (checked against the SDK verifier), bundle documents, webhook signatures and the SSRF guard. |
| G5 Isolation | `make test-db` | **PASS**: 65 pgTAP tests (now including the webhook functions and Vault access by role) and 4 PostgREST HTTP tests. |
| G6 Integration | `make test-integration` | **PASS**: 24 tests, against local Supabase, passing on two consecutive runs. 17 are the API tests from P3; 7 are new worker tests: <br>• SDK → API → calibrate → validated, signed bundle → activate → enforce re-sign → SDK aborts, with holdout runs never aborted <br>• a report equal to hand-computed figures <br>• drift → downgrade, audit entry and signed webhook → the SDK stops aborting <br>• the scheduler enqueues once per period and the runner drains the queue <br>• failing jobs retry, then die <br>• the signing key is published <br>• a calibration with no signal is stored as rejected and can't be activated |

### Fixed during the phase

- **G6 was nondeterministic**: one test failed in one full run. The local database keeps other
  tests' due jobs, and `run_due_jobs()` handles at most 20 per pass, so a test's own job was
  sometimes not reached. Each test now runs its own job, and the scheduler test checks the
  runner loop separately. No assertion changed.

### Open issues

1. **Production signing key**: the owner generates it (`scripts/gen-signing-key.py`) and sets
   `BUNDLE_SIGNING_KEY` and `BUNDLE_SIGNING_KEY_ID` on Railway. Its public half must be added
   to `DEFAULT_TRUSTED_KEYS` in both SDKs before release (P7).
2. **ADR-0006 needs owner review**, in particular decision 1 (webhook secrets in Vault) and
   decision 9 (retention counted from when the server received a run).
3. **Calibration runs in the worker process.** With 50,000 runs it takes minutes and blocks
   that worker's other jobs. Scale out with more workers if this becomes a problem.

## 2026-10-21 · P5: console

### Built

- **Console** (`apps/console`): Next.js 16 (App Router), React 19, `@supabase/ssr`.
  - Pages: sign-in, organisations and projects (with the webhook setting), API keys,
    workloads, workload detail, runs and the run explorer, and the audit log.
  - Workload detail:
    - 7-day verified and estimated savings, successes lost, and the false-abort rate with its
      interval.
    - An "outcome pending" count.
    - A per-day savings chart, with verified and estimated as separate bars, never stacked.
    - Bundle history with metrics, and activation.
    - Calibrate, mode switch and emergency override.
  - The enforce toggle stays disabled until §4 C9's conditions hold, and the page says which
    are missing.
  - Run explorer: an SVG timeline of each step's score against the bundle's threshold, the
    would-abort point, and the step table.
  - Reads go through PostgREST as the signed-in user, so RLS scopes them. Writes are Server
    Actions that call the API with the user's token; the console never writes to Postgres.
  - Money is never a float: `numeric` columns are read as text and summed as `BigInt` nano-units.
- **Worker and schema**:
  - The worker now also builds a daily trailing-week report.
  - Reports record `enforced_runs` and `first_run_at`, added by the expand-only migration
    `20261021000400_console.sql`.
- **ADR-0007**: 8 decisions, the main one being the precise enforce-gate rule.
  `ARCHITECTURE.md` §4 C7, §4 C9 and §8 are updated to match.
- **CI**: a new job runs G6b against a fresh local Supabase. `make test-unit` now also runs the
  console's Vitest suite.

### Gates

| Gate | Command | Result |
|---|---|---|
| G0 Static | `make lint typecheck` | **PASS**: ruff, ESLint (console included) and gitleaks clean; mypy strict 0 errors in 101 files; tsc 0 errors. |
| G2 Unit | `make test-unit` | **PASS**: 508 Python tests passed, 1 skipped. 13 console Vitest tests passed: decimal money, the enforce gate, reports and problem documents. |
| G5 Isolation | `make test-db` | **PASS**: 65 pgTAP tests and 4 PostgREST HTTP tests. |
| G6 Integration | `make test-integration` | **PASS**: 24 tests. These now also check the new report columns against hand-computed values, and that the scheduler enqueues the trailing-week report. |
| G6b Console e2e | `make test-e2e` | **PASS**, on two consecutive runs (1.9 min each). A single Chromium journey against the real API, worker and local Supabase: <br>• magic-link sign-in through Mailpit <br>• create an organisation, project, API key (shown once) and workload <br>• enforce disabled with both reasons <br>• calibrate → validated bundle; enforce still disabled until a shadow week <br>• activate <br>• shadow reports → enforce enabled <br>• run explorer shows score against threshold <br>• switch to enforce → the worker signs v2 <br>• emergency override → audit log <br>• revoke the key |

### Fixed during the phase

- **P4's G4 failed once on CI**: one SDK call took 52 ms against a 50 ms bound, on a shared
  runner; locally the slowest call is about 1 ms.
  - G4 now asserts the property directly: no socket connect or send happens on the agent
    thread. A mutation that adds a fetch to `check()` makes it fail.
  - The wall-clock bound is now 0.5 s, which still catches any wait on network I/O.
  - The fix was pushed to the P4 PR, where CI is green.
- **Auth redirects lost the session**: Next.js reported `localhost` while the browser used
  `127.0.0.1`. Redirects now use the browser's own host.

### Open issues

1. **Production URLs**: the console's Supabase Auth site URL and redirect URLs must be set in
   the staging and production projects (P7, owner action).
2. **ADR-0007 needs owner review**, in particular decision 3: the enforce gate is a console
   safeguard that the API doesn't apply.
3. **PRs are stacked**: the P4 PR is green but unmerged, and the P5 PR is based on it.

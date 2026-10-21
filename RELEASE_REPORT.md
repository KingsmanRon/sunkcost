# Release report: Sunkcost v1 (pre-release)

Prepared 2026-10-21 at the end of phase P7 (`docs/ARCHITECTURE.md` §13, §14). **Not released:**
nothing has been deployed to production, no tag exists, and no package is published. Production
promotion is the owner's decision.

Every gate below was run on the P7 tree (`phase/p7-staging-release`) on this date, unless it says
otherwise. NOT RUN means the gate did not run, with the reason; it is never counted as a pass.

## Gates

| Gate | Command | Result | Evidence |
|---|---|---|---|
| G0 Static | `make lint typecheck` | **PASS** | ruff (lint and format), ESLint strict type-checked, mypy strict on 101 files, tsc: 0 errors. |
| G1 Proof | `make bench` | **PASS** | S1 met on 2 of 5 public datasets: τ²-bench telecom (kept 93.4% of successes, saved 10.8% of failed-run spend) and τ-bench retail (94.4%, 4.6%). τ²-bench retail, τ²-bench airline and SWE-bench Verified mini do not pass. The figures are identical to P1's, so the benchmark is reproducible. See `docs/BENCHMARKS.md`. |
| G2 Unit | `make test-unit` | **PASS** | 508 Python tests passed (1 skipped: an empty vector case), 13 console tests, 162 TypeScript SDK tests. |
| G2b Statistical | `make test-stats` | **PASS** | 500 simulated workloads, fixed seeds. In every configuration, the share of simulations whose realised false-abort rate exceeds α is ≤ δ + 0.03. At target recall 0.95 / δ 0.05 it is 0.008, against 0.08 allowed. |
| G3 SDK perf | `make bench-sdk` | **PASS** | `check()` p99 at step 200 is 0.0285 ms in Python and 0.0010 ms in Node (limit 0.5 ms), with 0 network calls. See `docs/PERF.md`. |
| G4 Failure | `make test-failure` | **PASS** | 12 Python and 12 Node scenarios against a real local HTTP server: <br>• unreachable <br>• cached bundle until expiry <br>• slow 10 s <br>• 500s then recovery <br>• bad signature <br>• expired bundle <br>• rollback <br>• override <br>• buffer overflow <br>• `sdk_error` injection <br>• exit flush <br>• exit mid-flush, bounded |
| G5 Isolation | `make test-db` | **PASS** | `supabase db reset`, then 65 pgTAP tests and 4 PostgREST tests with real Auth tokens. |
| G6 Integration | `make test-integration` | **PASS** | 25 tests: the API, worker and SDK against local Supabase. They cover ingest idempotency, late and conflicting outcomes, calibration → validated bundle → enforce with holdout, hand-computed report figures, drift downgrade with webhook, the scheduler, and the drift period. |
| G6b Console e2e | `make test-e2e` | **PASS** | One Playwright journey (1.6 min) against the real API, worker and local Supabase: <br>• sign-in <br>• create workload and key <br>• calibrate and activate <br>• enforce disabled until §4 C9's conditions hold <br>• enforce <br>• override <br>• audit log |
| G7 Staging | `make smoke-staging` | **NOT RUN** | The staging Supabase, Railway and Vercel projects don't exist yet; creating them is an owner action (`docs/DEPLOYMENT.md`). The smoke script itself ran against the local stack (9 of 9 steps, including a drift downgrade the SDK observed); that is not G7. |
| G8 Security | `make security` | **PASS in CI**, with proposed waivers | On the P7 PR head `b9be5e9` all 7 scanners pass in CI: gitleaks, Semgrep `p/default`, pip-audit, pnpm audit, Trivy on the tree, Trivy on the api image, Trivy on the worker image. CodeQL (Python, TypeScript, Actions) is clean. Locally Semgrep is NOT RUN, because semgrep.dev is blocked by the build environment's network policy. <br>Semgrep's first CI run found 14 findings: <br>• fixed: a `workflow_run` pwn-request path in `staging.yml`, no Dependabot or pnpm cooldown, missing pnpm supply-chain settings, a string `replace` <br>• suppressed inline with a reason: 4 false positives <br>The pass relies on two proposed waivers in `security/waivers.md`, neither yet accepted by the owner: the uv cooldown, and unfixed Debian base-image CVEs. |
| G8 Docs | `make docs-check` | **PASS** | 27 Markdown files with no broken links. The README quickstart ran end to end in a clean `node:22` container in 168 s, against S6's 30-minute budget. |
| Load | `make load-test` | **NOT RUN** | Needs staging, as for G7. A 60 s local dry run sustained 982 events/s with every batch accepted; that is not the gate. |

## Success criteria (§1.1)

| | Criterion | Status |
|---|---|---|
| S1 | ≥ 90% of successes kept and > 0% of failed spend saved, on ≥ 2 public datasets | Met (G1) |
| S2 | `check()` < 0.5 ms p99, never a network call | Met in both SDKs (G3, G4) |
| S3 | Losing the cloud never stops, slows or errors an agent | Met (G4, both SDKs) |
| S4 | No cross-tenant reads or writes | Met (G5, plus G6 isolation) |
| S5 | Shadow savings only from observed spend | Met (G6 report test, G6b) |
| S6 | Self-host from the README in < 30 minutes | Quickstart part met (docs-check, 168 s). The full self-hosted stack ran in this session but is not timed in a clean container. |

## Before production: owner actions

1. **Merge the phase PRs in order.** #7 (P4), #8 (P5), #9 (P6) and the P7 PR are stacked, and
   all are green. The build session was not permitted to merge them.
2. **Provision staging** per `docs/DEPLOYMENT.md` §1–§4, then let `staging.yml` deploy. Running
   G7 and the load test needs this.
3. **Generate the staging and production signing keys** offline (`scripts/gen-signing-key.py`).
   Add the production public half to `DEFAULT_TRUSTED_KEYS` in both SDKs.
4. **Accept or reject** the proposed unfixed-OS-package policy in `security/waivers.md`. Confirm
   that Semgrep and CodeQL are clean on the P7 PR.
5. **Set up release**: PyPI and npm trusted publishers, the `production` environment's required
   reviewer, and drop `"private": true` from the npm package in the release commit.
6. **Review ADR-0004 to ADR-0009.** The decisions most worth a look:
   - ADR-0005 §1: partitioning.
   - ADR-0006 §1: webhook secrets in Vault.
   - ADR-0007 §3: the enforce gate is enforced in the console only.
   - ADR-0008 §1: Node 22+ only.
   - ADR-0009 §8: the waiver policy.

## Known risks

- **Ingest throughput on staging.** Each event costs several SQL round trips. Locally a
  500-event batch takes about 1.8 s, and 1,000 events/s needs about 8 concurrent senders.
  Across a network to Supavisor, the load test may miss its target. Batching the ingest SQL
  is the first fix if it does.
- **Railway documentation could not be fetched directly.** The config files follow Railway's
  documentation as quoted by search, and the CLI's `--help`. Check them when creating the
  services.
- **Node `recordStep()` p99 is 0.55 ms**, over 200 calls including warm-up. It is not a gate.
- **The enforce gate is a console safeguard.** An admin calling the API directly can still
  switch a workload to enforce early, and that change is audited.

# ADR-0009: Staging, release and the G7/G8 gates

- Status: accepted (decision 8 proposed, pending owner acceptance)
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P7 PR

## Context

Phase P7 wires staging and production (§11) and builds the remaining gates (§13: G7, G8, Load).
The architecture leaves the mechanics open. `docs.railway.com` was not reachable from the build
environment, so the Railway settings below come from Railway's documentation as quoted by web
search, and from the Railway CLI's own `--help`. The owner should check them when creating the
services.

## Decisions

1. **Deploys come from GitHub Actions, after migrations.**
   - `staging.yml` runs after `ci` passes on `main`: `supabase db push` (dry run first,
     `--skip-vault`), then `railway up --ci` for `api` and `worker`, then the G7 smoke.
   - Railway's own GitHub autodeploy is turned off, so a deploy can never run ahead of its
     migrations.
   - The console's Vercel Preview still deploys from `main` through Vercel's Git integration.
     That ordering is safe because every migration is expand-only (CLAUDE.md).
2. **Production runs only from a `v*` tag on `main`** (`release.yml`).
   - Every job that touches production uses the GitHub environment `production`, which requires
     the owner's approval.
   - The order is: migrations (dry run, then apply) → Railway and Vercel `--prod` → production
     smoke → PyPI and npm trusted publishing.
   - Vercel's production branch is set to an unused branch, so `main` stays Preview.
3. **The services listen on `$PORT`**, which Railway injects and health-checks.
   - The API defaults to 8000. The worker's `/healthz` uses `HEALTH_PORT`, then `PORT`, then
     8081.
   - Each Dockerfile's `HEALTHCHECK` follows the same variables.
4. **Drift checks run once per `DRIFT_INTERVAL_S`**, default 3600 as in §4 C7.
   - The dedupe key is `drift:<workload>:<interval>:<period>`.
   - Staging sets 120 s so G7 observes a downgrade within minutes. Production leaves the default.
5. **Images are multi-stage.** uv builds the virtual environment, and the runtime stage holds
   only `/app/.venv` on the same pinned base. Trivy had flagged the uv binary left in the old
   single-stage images.
6. **G7 smoke (`scripts/smoke.py`)** runs as a customer would:
   - A password-authenticated smoke user (Supabase Auth) and a fresh API key per run, in a
     dedicated `sunkcost-smoke` organisation.
   - Reads go through PostgREST under RLS. It never connects to Postgres directly and never uses
     `service_role`.
   - Drift is triggered by ingesting successful runs that the active bundle would have aborted,
     which is the SDK's own report, and observed through the SDK's bundle refresh.
   - `--quick` is the production smoke: health, ingest with an outcome, and a bundle fetch.
7. **The load test (`scripts/load_test.py`)** sends gzip batches of 494 whole-run events from 8
   concurrent senders, paced to the target rate. The default is 1,000 events/s for 10 minutes.
   It writes its result to `docs/PERF.md` whether or not the target is met (§13).
8. **G8 security (`scripts/security.sh`)** runs gitleaks, Semgrep `p/default`, pip-audit, pnpm
   audit, and Trivy over the tree and both images.
   - A scanner that cannot run is reported NOT RUN, and the script exits 2.
   - **Proposed:** HIGH and CRITICAL findings in Debian packages of the pinned base that have
     *no fixed version* are listed in `security/waivers.md` rather than failing the gate
     (`--ignore-unfixed`). Any finding with a fix still fails it. This needs the owner's
     acceptance.
9. **G8 docs-check (`scripts/docs-check.sh`)**:
   - It checks every relative Markdown link.
   - It runs the README block between the `docs-check` markers in a clean, digest-pinned
     `node:22` container, which has Corepack and gets a checksum-verified uv. The README and the
     check therefore cannot drift apart.
   - S6's 30-minute budget is asserted.
10. **CodeQL** runs as its own workflow, for Python, TypeScript and Actions. The repository is
    public, so it needs no Advanced Security licence.

## Consequences

- `docs/DEPLOYMENT.md` is the owner's checklist of every service, setting, variable and secret.
- `docs/ARCHITECTURE.md` §11.2, §11.3 and §11.4 point here.
- G7 and the load test can only run after the owner has created the staging services and set
  their configuration.

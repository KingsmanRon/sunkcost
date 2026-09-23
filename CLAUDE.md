# CLAUDE.md — standing rules for this repository

`docs/ARCHITECTURE.md` is the source of truth. If the code needs to differ from it, write an ADR in `docs/adr/` first, then change both.

## Commands

Everything goes through `make`:

- Static checks: `make lint typecheck`
- Tests: `make test-unit`, `make test-stats`, `make test-failure`, `make test-db`, `make test-integration`, `make test-e2e`
- Benchmarks: `make bench`, `make bench-sdk`
- Staging and security: `make smoke-staging`, `make load-test`, `make security`, `make docs-check`
- Local stack: `supabase start && docker compose up`

## Never

- Deploy to production, promote a release, or run migrations against staging or production from a local machine. Migrations reach cloud databases only through the GitHub Actions workflows.
- Use the Supabase `service_role` key in any component.
- Commit secrets, `.env` files or signing keys.
- Capture, log or transmit prompt, completion or raw tool-argument content.
- Put JSON floats in signed content (decimal strings only), or use floats for money (decimal strings on the wire, `numeric(20,9)` in Postgres).
- Mark a gate as passed when it didn't run. Write **NOT RUN** and the reason.
- Weaken, skip or delete a test to make a gate pass, or mock Postgres in G5/G6. Those gates run against a real local Supabase.
- Make a network call inside the SDK's `check()`.
- Let the console write to Postgres directly, or let the API and worker call each other.

## Human-only actions (ask Ronald)

- Creating or linking Supabase, Railway and Vercel projects; setting environment variables and secrets.
- Generating the production signing key (`scripts/gen-signing-key.py` is run by the owner offline).
- Approving the GitHub `production` environment; configuring PyPI and npm trusted publishing.
- The continue/kill decision after phase P1, and production approval after P7.

## Conventions

- IDs are UUIDv7; times are UTC ISO-8601; API errors use RFC 9457 problem+json with `x-request-id`.
- Supavisor transaction pooler: prepared statements disabled. The worker uses the session pooler.
- Every migration is backward compatible with the currently deployed code (expand → deploy → contract).
- Conventional Commits. One branch and PR per phase: `phase/p<N>-<slug>`.
- After each phase, append a short dated entry to `docs/PROGRESS.md`: what was built, gate results, open issues.

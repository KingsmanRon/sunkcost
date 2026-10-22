# ADR-0007: Console clarifications

- Status: accepted
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P5 PR

## Context

Building the console (§4 C9) needed a precise meaning for its one safety rule: the enforce toggle
stays disabled until the workload has "at least one `validated` bundle *and* one shadow report
covering ≥ 7 days". The worker built only daily reports, and reports did not record whether
their runs were enforced, so no report could show that.

## Decisions

1. **The worker also builds a trailing-week report.**
   - Each day the scheduler enqueues, per workload, the daily report for yesterday and a report
     for the 7 days ending today at 00:00 UTC (dedupe key `report7:<workload>:<day>`).
   - The console takes its 7-day figures (verified savings, successes lost, false-abort rate
     with its Clopper–Pearson interval) from the latest trailing-week report. The interval is
     computed once, by the worker, over the whole week. Daily rates can't be combined into one.
2. **Reports record `enforced_runs` and `first_run_at`.**
   - `enforced_runs`: runs in the period with mode `enforce`, holdout included.
   - `first_run_at`: the earliest `started_at` in the period.
   - Both are added by migration `20261021000400_console.sql`, with defaults, so the change is
     expand-only.
3. **The enforce gate**:
   - *Validated bundle*: any bundle of the workload with status `validated`, `active` or
     `retired`. All three passed validation; `rejected` never did.
   - *Shadow report covering ≥ 7 days*: a report whose period is at least 7 days long, with
     `runs > 0`, `enforced_runs = 0`, and `first_run_at` within the period's first day. The last
     condition stops a workload whose data began mid-week from qualifying early.
   - The gate lives in the console (`apps/console/lib/enforce.ts`), as §4 C9 places it.
   - `PATCH /v1/workloads/{id}` doesn't apply it, so an admin calling the API directly can still
     enforce early. That change is audited, and G6 relies on it.
4. **Sign-in**:
   - Magic link with PKCE: `/auth/callback` exchanges the code for session cookies.
   - GitHub OAuth (optional in §4 C9) is not enabled in v1.
   - The session is refreshed in `proxy.ts`, which is what Next.js 16 calls middleware. The proxy
     verifies the token with `getClaims()`, and signed-out users are sent to `/login`.
   - Redirects use the origin the browser sent (`x-forwarded-host` or `Host`). Next.js's own
     request URL reports the server's bind address, and a redirect there drops the session
     cookie.
5. **Money stays decimal end to end.**
   - The console selects `numeric` columns as text.
   - It adds and rounds on integers scaled by 10⁹ (`BigInt`), never on floats.
   - Rates (false-abort rate, its interval) are not money and are shown from the stored floats.
6. **Console scope beyond the §4 C9 page list**:
   - Creating organisations, projects and workloads.
   - Setting the webhook URL (the secret is shown once).
   - Revoking API keys.
   - A run list per workload, which leads into the run explorer.
   - An "outcome pending" count (§10).
   - Workload settings (target recall, δ, min steps, holdout rate) are API-only in v1.
7. **The audit log shows the actor's user id, not an email.** Under RLS the console can't read
   `auth.users`, and adding a definer function just to expose emails isn't worth it in v1.
8. **G6b seeds its data like a customer would**:
   - Runs go through `POST /v1/ingest` and the Python SDK.
   - The one exception is the report jobs the scheduler would enqueue overnight, which the
     spec's seed script inserts as the local admin.

## Consequences

- `docs/ARCHITECTURE.md` §4 C7, §4 C9 and §8 now point here.
- The enforce gate is a console safeguard, not a server-side guarantee. Making the API refuse an
  early switch would be a future ADR, and G6's direct switch would need to change with it.

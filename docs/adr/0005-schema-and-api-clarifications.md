# ADR-0005: Database schema and API clarifications

- Status: accepted
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P3 PR

## Context

Writing the Supabase migrations (§8) and the API (§4 C6) exposed one contradiction in
`docs/ARCHITECTURE.md` and several open points.

## Decisions

### Schema (§8)

1. **`step_events` is partitioned by `runs.run_month`, not by the client's `ts`.**
   - §8 says "partitioned by month on `ts`", while §10 says the server partitions on received
     time so client clock skew cannot misplace data. A partitioned table's unique key must also
     include the partition key, so partitioning on either timestamp would break `(run_id, seq)`
     deduplication when a re-sent step arrives later.
   - `runs.run_month` is the month the server first received any event of the run. Every step of
     a run, including re-sends, lands in the same partition, and the key
     `(org_id, run_id, seq, run_month)` deduplicates exactly.
   - Retention follows the server clock.
   - Monthly partitions are created ahead by `ensure_step_partitions` (the worker calls it daily
     from P4). A default partition catches anything else.
2. **Runs are keyed by `(org_id, id)` and carry `project_id`.** A run id chosen by one tenant can
   never collide with, block or modify another tenant's run. An event for a run owned by another
   project of the same org is rejected.
3. **`runs.status` is derived:**
   - `partial`: no `run_start` seen yet.
   - `running`: started, not ended.
   - Otherwise the `run_end` status (`completed`, `aborted` or `errored`).

   A late `run_start` therefore fills a placeholder without losing an earlier `run_end`.
4. **Every table has `org_id`.** For `orgs` it is a generated copy of `id`. The global
   `signing_keys` table (public keys for `/v1/signing-keys`) has no `org_id`.
5. **Definer functions.** Besides `resolve_api_key` and `list_due_work`, there are two more:
   - `my_org_ids()`, for the console's RLS policies. It returns only the caller's own
     memberships and avoids a policy recursing into `org_members`.
   - `ensure_step_partitions()`, partition maintenance that runs as the table owner.

   All four pin `search_path`, and a pgTAP test checks this.
6. **Role passwords are not in migrations.** `sunkcost_api` and `sunkcost_worker` are created
   as login roles with no password. The owner sets the password per environment.
   `supabase/seed.sql` sets local-only development passwords.

### API (§4 C6, §8.2)

7. **Workloads are created on first use.** A `run_start` naming an unknown workload creates it
   with default settings (shadow mode). "No bundle yet: events still flow" (§10) then holds for
   new workloads without a console step.
8. **Outcomes for unknown runs.** An outcome by `run_id`, through ingest or `/v1/outcomes`,
   creates a placeholder run, because outcomes can arrive before the SDK's flush. An outcome by
   `(workload, external_ref)` applies to the most recently started matching run, and returns 404
   if there is none.
9. **Ingest response.** `{accepted, duplicates, rejected: [{index, reason}]}`.
   - Each event is validated and applied in its own savepoint.
   - An invalid batch header is a 400 problem.
   - A conflicting outcome counts as accepted, since the conflict is recorded.
   - The 1 MB limit applies after `gzip`/`deflate` decompression.
10. **ETag** = hash of bundle id, signature and override state. A daily re-sign changes the
    signature, so SDKs pick up the new `expires_at`.
11. **Console requests name their org** in an `x-sunkcost-org` header. The transaction can then
    act for that org under RLS before reading any row, with no further definer functions.
12. **Override.** Setting `shadow` needs role member or higher (it only reduces enforcement).
    Clearing it restores the signed mode and needs admin.
13. **User tokens** are verified against the Supabase JWKS (ES256 or RS256). HS256 with
    `SUPABASE_JWT_SECRET` is accepted only when that secret is configured.

## Consequences

- `docs/ARCHITECTURE.md` §4 C6, §8, §8.1 and §8.2 now point here.
- P4 (worker) calls `ensure_step_partitions` daily, publishes its public key to `signing_keys`,
  and may revise `list_due_work` through a new migration.

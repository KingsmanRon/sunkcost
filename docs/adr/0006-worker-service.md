# ADR-0006: Worker service clarifications

- Status: accepted
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P4 PR

## Context

Building the worker (§4 C7) required several decisions the architecture leaves open. In one case
the architecture can't work as written: webhooks must be signed with HMAC, but only the secret's
hash is stored.

## Decisions

1. **Webhook secrets live in Supabase Vault.**
   - Why: `X-Sunkcost-Signature` is an HMAC (§5.1 ⑧), so the worker needs the secret itself,
     while §8 stores only `orgs.webhook_secret_hash`.
   - `PUT /v1/orgs/webhook` (admin) generates a `whsec_…` secret and shows it once. It stores the
     secret encrypted in Vault through `set_org_webhook()`, and keeps the hash on `orgs`.
   - The worker reads the secret through `org_webhook_target()`.
   - Both are `SECURITY DEFINER` functions. Each is executable only by its own role, and only
     while acting for that org.
2. **Webhook delivery.**
   - Signature: `t=<unix>,v1=hex(HMAC_SHA256(secret, "<t>.<body>"))`. Receivers reject timestamps
     more than 5 minutes off.
   - Retries: 5 attempts over about an hour (delays of 1, 5, 15 and 35 minutes), then the job is
     `dead`.
   - URLs must be https, and hosts resolving to private, loopback, link-local or reserved
     addresses are refused. `WEBHOOK_ALLOW_PRIVATE=1` lifts both rules, for local development
     only.
3. **`list_due_work()` also lists due jobs.** Claiming needs a cross-org view of `jobs`, and §8.1
   allows only the two narrow functions for cross-org reads. A job still `running` 15 minutes
   after it was claimed counts as due again, which enforces the job timeout.
4. **The scheduler deduplicates** with a unique `jobs.dedupe_key` per period, for example
   `report:<workload>:<day>` or `drift:<workload>:<hour>`. Several workers can scale out, and
   restarts never double-enqueue.
   - Per pass: `ensure_step_partitions`.
   - Hourly: `drift_check` per workload with an active bundle.
   - Daily: `resign_bundle` and `build_report` (for the previous UTC day) per workload, and
     `retention` per org.
   - Nightly: `calibrate` when there are at least 200 new labelled runs since activation.
   - An active bundle not re-signed for 36 h is logged at error level.
5. **Calibration input.**
   - Runs used: the workload's uncensored runs (shadow, or holdout, and never aborted) that have
     a final success or failure outcome and a `run_end`, at most the 50,000 most recent.
   - Runs whose stored steps are not exactly `1..steps` are excluded and counted
     (`runs_incomplete`). A dropped event would make the features differ from what the SDK saw.
   - Training happens outside any transaction.
6. **Calibration output.**
   - `insufficient_data` and `cannot_train` end the job without a bundle; the job's `result`
     says why.
   - `validated` and `rejected` are stored as the next version. Only validated bundles are
     checked against `bundle.v1` and signed. A rejected bundle has no signature, and the API
     refuses to activate it.
   - The bundle's mode is the workload's mode at calibration time.
7. **Re-signing.**
   - Daily re-sign: same version, fresh `issued_at` and `expires_at`, and a new signature (so a
     new ETag).
   - Mode change: a new signed version, which replaces the active one immediately. It is
     requested by an admin through `PATCH /v1/workloads/{id}`, and §6.2 requires a new version
     for any increase in enforcement.
8. **Drift.**
   - Data: successful uncensored runs of the active bundle in the last 7 days.
   - Test: if the one-sided Clopper–Pearson lower bound (confidence 1 − δ) of the would-abort
     rate exceeds α, the workload gets `override = 'shadow'`.
   - The change is written to `audit_log` (actor `system`, reason `drift`), and a
     `workload.downgraded` webhook is sent.
9. **Retention** deletes an org's runs, and their steps by cascade, 90 days after the server
   first received them.
10. **The signing key**, a 32-byte Ed25519 seed in `BUNDLE_SIGNING_KEY`, is generated offline by
    the owner with `scripts/gen-signing-key.py`. On start the worker publishes the public half to
    `signing_keys`, which `GET /v1/signing-keys` serves. The local stack signs with the public
    RFC 8032 test key, which SDKs trust only when it is passed explicitly.

## Consequences

- `docs/ARCHITECTURE.md` §4 C6, §4 C7 and §5.1 ⑧ now point here.
- The owner generates the production key and adds its public half to the SDK defaults before
  release (P7).

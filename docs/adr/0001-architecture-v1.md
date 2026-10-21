# ADR-0001: Adopt the Sunkcost v1 architecture

- Status: accepted
- Date: 2026-09-23
- Deciders: Ronald Maduna (owner)

## Context

Sunkcost needs one agreed design before any code is written: which components exist, how they talk,
where the security boundaries are, what the statistical guarantee is, and which gates a release must
pass. The owner approved `docs/ARCHITECTURE.md` v1.0 for implementation.

## Decision

`docs/ARCHITECTURE.md` v1.0 is the baseline and the source of truth. In particular:

- Decisions run locally in the SDK against a signed policy bundle; `check()` makes no network call.
- Only signed bundles can raise enforcement; the unsigned override can only downgrade to shadow.
- The console never writes to Postgres, and the API and worker communicate only through the `jobs`
  table.
- Thresholds come from Clopper–Pearson fixed-sequence testing on uncensored calibration runs, and
  savings are reported as verified (observed) or estimated, never summed.
- Build order is P0–P7 (§14), with the release gates of §13.

The defaults in §15 (name, Apache-2.0 licence, logistic regression, Postgres job queue, bundles in
`jsonb`, signed enforcement, unsigned emergency downgrade) are adopted as written.

## Consequences

- Code that disagrees with the architecture is a bug unless an ADR changes the architecture first;
  the ADR and `ARCHITECTURE.md` are then updated together.
- Clarifications needed to make the text precise enough to implement are recorded as ADRs
  (ADR-0002 onward) rather than decided silently in code.

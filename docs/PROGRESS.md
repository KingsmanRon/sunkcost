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

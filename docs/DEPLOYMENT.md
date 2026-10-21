# Deployment: staging and production

What the owner sets up once, so that `.github/workflows/staging.yml` and `release.yml` can run
(ARCHITECTURE.md §11, ADR-0009). Nothing here is done from a local machine: migrations reach
cloud databases only through those workflows, and no component uses the `service_role` key.
Repeat everything in **Staging** for **Production** with production values; the differences
are listed at the end.

## 1. Supabase project (`sunkcost-staging`)

1. Create the project (Postgres 17). Note its **project ref** and **database password**.
2. Create a personal access token for the CLI (Account → Access tokens).
3. Authentication → URL configuration:
   - Site URL: the staging console URL (Vercel preview domain for `main`, see §3).
   - Redirect URLs: `<console URL>/auth/callback`.
4. Authentication → Providers → Email: enabled, with magic links. Password sign-in must stay
   enabled for the smoke user.
5. Authentication → Users → Add user: the **smoke user** (an address you control, a long random
   password, "auto confirm"). It owns the `sunkcost-smoke` organisation the smoke creates.
6. After the first migration run (§4), set the two service roles' passwords in the SQL editor:

   ```sql
   alter role sunkcost_api with password '<random 32+ characters>';
   alter role sunkcost_worker with password '<another random 32+ characters>';
   ```

7. Connection strings (Connect → Supavisor), using the roles above:
   - API: **transaction pooler**, port 6543, user `sunkcost_api.<project ref>`.
   - Worker: **session pooler**, port 5432, user `sunkcost_worker.<project ref>`.
8. JWT verification: the API reads `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`.
   If the project still signs user tokens with the legacy shared secret, set
   `SUPABASE_JWT_SECRET` on the API instead.

## 2. Railway project (`sunkcost`, environments `staging` and `production`)

Two services from this repository, both built from their Dockerfiles at the repository root:

| Service | Config-as-code path | Public domain | Health check |
|---|---|---|---|
| `api` | `/services/api/railway.toml` | yes (this is `SMOKE_API_URL`) | `/readyz` on `$PORT` |
| `worker` | `/services/worker/railway.toml` | no | `/healthz` on `$PORT` |

- Turn off automatic deploys from GitHub for both services: the workflows deploy with
  `railway up` after migrations have run.
- Create a **project token** for the `staging` environment (Project settings → Tokens). This is
  `RAILWAY_TOKEN` in the GitHub `staging` environment.
- Never set a pre-deploy command: migrations do not run on Railway.

Variables (Railway → service → Variables):

| Service | Variable | Staging value |
|---|---|---|
| api | `DATABASE_URL` | transaction pooler URL for `sunkcost_api` (§1.7) |
| api | `SUPABASE_URL` | `https://<ref>.supabase.co` |
| api | `SUPABASE_JWKS_URL` or `SUPABASE_JWT_SECRET` | see §1.8 (JWKS is the default when unset) |
| api | `CONSOLE_ORIGIN` | the staging console URL |
| api | `LOG_LEVEL`, `RATE_LIMIT_PER_MIN`, `DB_POOL_MIN`, `DB_POOL_MAX` | `INFO`, `600`, `1`, `10` (defaults) |
| worker | `DATABASE_URL` | session pooler URL for `sunkcost_worker` (§1.7) |
| worker | `BUNDLE_SIGNING_KEY`, `BUNDLE_SIGNING_KEY_ID` | from `python scripts/gen-signing-key.py`, run offline; a **staging** key, never the production one |
| worker | `DRIFT_INTERVAL_S`, `SCHEDULE_INTERVAL_S` | `120`, `30` in staging, so the G7 drift check runs within minutes; leave unset (3600, 60) in production |
| worker | `WEBHOOK_TIMEOUT_S`, `POLL_INTERVAL_S`, `LOG_LEVEL` | defaults (`10`, `2`, `INFO`) |

`PORT` is set by Railway. `WEBHOOK_ALLOW_PRIVATE` must never be set outside local development.

## 3. Vercel project (console)

- Import the repository with **Root Directory** `apps/console` (framework Next.js; install and
  build commands come from `apps/console/vercel.json`).
- Settings → Git → Production Branch: `release`, a branch nobody pushes. `main` then deploys as
  **Preview**, which is staging (§11.1). Production is deployed by `release.yml` from a `v*` tag.
- Environment variables:

| Variable | Preview (staging) | Production |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | staging `https://<ref>.supabase.co` | production URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | staging anon (publishable) key | production anon key |
| `NEXT_PUBLIC_API_URL` | staging API URL | production API URL |

- For `release.yml`: an access token (`VERCEL_TOKEN`), and the team and project IDs
  (`VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, from Project settings).

## 4. GitHub environments

Settings → Environments. Variables are not secret; secrets are.

| Name | Kind | `staging` | `production` |
|---|---|---|---|
| `SUPABASE_PROJECT_REF` | variable | staging ref | production ref |
| `SUPABASE_ACCESS_TOKEN` | secret | CLI token (§1.2) | same or a separate token |
| `SUPABASE_DB_PASSWORD` | secret | staging DB password | production DB password |
| `RAILWAY_TOKEN` | secret | staging project token | production project token |
| `SMOKE_API_URL` | variable | staging API URL | production API URL |
| `SMOKE_SUPABASE_URL` | variable | staging Supabase URL | production Supabase URL |
| `SMOKE_SUPABASE_ANON_KEY` | variable | staging anon key | production anon key |
| `SMOKE_SIGNING_KEY_ID` | variable | staging `BUNDLE_SIGNING_KEY_ID` | production key id |
| `SMOKE_SIGNING_PUBLIC_KEY` | variable | its public half (printed by the script) | production public half |
| `SMOKE_USER_EMAIL`, `SMOKE_USER_PASSWORD` | secret | staging smoke user (§1.5) | production smoke user |
| `VERCEL_TOKEN` | secret | — | Vercel token |
| `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | variable | — | Vercel IDs |

- `production`: **Required reviewers: the owner**, and "Deployment branches and tags" limited to
  tags `v*`. Every production job waits for this approval.
- `staging`: deployment branch `main` only.

## 5. Package registries (release only)

- PyPI trusted publishers for `sunkcost`, `sunkcost-langgraph`, `sunkcost-openai-agents` and
  `sunkcost-cycles`: owner `KingsmanRon`, repository `sunkcost`, workflow `release.yml`,
  environment `production`.
- npm trusted publisher for `sunkcost`: the same repository, workflow and environment.
  `packages/sdk-typescript/package.json` must drop `"private": true` in the release commit.
- Add the production public key to `DEFAULT_TRUSTED_KEYS` in both SDKs
  (`packages/sdk-python/sunkcost/keys.py`, `packages/sdk-typescript/src/keys.ts`) before tagging.

## 6. Running the gates against staging

Once §1–§4 are in place for staging and a merge to `main` has run `staging.yml`:

```sh
export SMOKE_API_URL=… SMOKE_SUPABASE_URL=… SMOKE_SUPABASE_ANON_KEY=… \
  SMOKE_SIGNING_KEY_ID=… SMOKE_SIGNING_PUBLIC_KEY=… SMOKE_USER_EMAIL=… SMOKE_USER_PASSWORD=…
make smoke-staging   # G7 (also run by staging.yml after every deploy)
make load-test       # 10 minutes at 1,000 events/s; writes the load section of docs/PERF.md
```

The load test also runs from GitHub: Actions → staging → Run workflow → "Also run the 10-minute
load test".

## Production differences

- Its own Supabase project (`sunkcost-prod`), Railway environment `production`, signing key
  (owner-generated, offline), smoke user, and Vercel Production variables.
- Worker: `DRIFT_INTERVAL_S` and `SCHEDULE_INTERVAL_S` unset (hourly drift checks, §4 C7).
- `release.yml` runs `supabase db push --dry-run` before applying migrations, then deploys,
  runs the production smoke (`scripts/smoke.py --quick`: health, ingest, outcome and bundle
  fetch in the dedicated `sunkcost-smoke` organisation), and only then publishes packages.

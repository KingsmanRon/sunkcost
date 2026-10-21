# Self-hosting

Sunkcost is three services and a database: the API and the worker (`services/`, one container
each), the console (`apps/console`, Next.js) and Supabase (Postgres, Auth, PostgREST, Vault).
Your agents talk only to the API. `check()` never leaves the process.

## On one machine, from the README

Prerequisites: Docker, the Supabase CLI, and the README's development prerequisites.

```sh
supabase start                       # Postgres 17, Auth, PostgREST, Mailpit on 127.0.0.1
docker compose up --build            # the API on :8000 and the worker
```

`docker compose` signs bundles with the **public RFC 8032 test key**, which no SDK trusts by
default. That's fine for trying Sunkcost; for anything else, generate your own key (below).
The console runs with `pnpm dev` in `apps/console` (see the README), and sign-in links arrive
in Mailpit at http://127.0.0.1:54324.

Then point an agent at it:

```python
from sunkcost import Sunkcost

sl = Sunkcost(
    api_key="sc_live_…",                     # created in the console: API keys
    endpoint="http://127.0.0.1:8000",
    trusted_keys={"rfc8032-test-1": "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="},
    args_hash_salt="a long random value you keep",
)
```

`scripts/smoke.py` exercises the whole stack end to end. It covers ingest, calibration,
activation, SDK verification and a drift downgrade. It needs a password user in your Supabase
Auth and the `SMOKE_*` variables listed in `docs/DEPLOYMENT.md`.

## Your own signing key

```sh
python scripts/gen-signing-key.py    # prints BUNDLE_SIGNING_KEY_ID, BUNDLE_SIGNING_KEY and the public key
```

Set the two variables on the worker. Give the public key to your SDKs as `trusted_keys`
(`trustedKeys` in TypeScript). The worker publishes it at `GET /v1/signing-keys`, but SDKs never
trust a key just because the server sent it.

## On your own infrastructure

Use a Supabase project (hosted or self-hosted). Apply `supabase/migrations` in order with
`supabase db push`, then set passwords for the `sunkcost_api` and `sunkcost_worker` roles. Run
the two images with the variables in `docs/DEPLOYMENT.md` §2:

- The API connects through a transaction pooler with prepared statements disabled.
- The worker uses a session connection, for its scheduler's advisory lock.
- Every migration is backward compatible with the previous release, so you can migrate before
  you redeploy.

Scale the API horizontally freely. Rate limits are per instance (ADR-0005). The worker can scale
out too: only the instance holding the advisory lock schedules, and jobs are claimed with
`SKIP LOCKED`.

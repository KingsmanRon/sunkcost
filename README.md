# Sunkcost

Sunkcost stops AI agent runs that are going to fail before they spend the rest of their budget, while
keeping a statistically guaranteed share of the runs that would have succeeded. An in-process SDK
scores each step against a signed policy locally, with no network call on the hot path. Teams run it in
shadow mode first and get a savings report computed only from observed spend.

**Status:** pre-release, under construction. Done so far:
- P0: contracts and tooling.
- P1: the core, the calibration library and the public benchmark
  ([`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)).
- P2: the Python SDK and its LangGraph, OpenAI Agents and Cycles adapters, with offline
  [`examples/`](examples/).
- P3: the Supabase schema with row-level security, and the API.
- P4: the worker, which handles calibration, re-signing, reports, drift and webhooks.
- P5: the console (Next.js): sign-in, keys, workloads, savings reports, bundles, mode switches,
  the run explorer and the audit log.
- P6: the TypeScript SDK ([`packages/sdk-typescript`](packages/sdk-typescript/)), passing the same
  vectors, perf and failure gates as the Python SDK.

See [`docs/PROGRESS.md`](docs/PROGRESS.md). Nothing is published yet.

## Documents

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the specification and source of truth
- [`docs/adr/`](docs/adr/): decisions that refine or change the architecture
- [`packages/spec/`](packages/spec/): wire contracts (JSON Schema), the normative feature
  definition, golden test vectors and the price table
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) and [`bench/DATASETS.md`](bench/DATASETS.md): results
  on public agent-trajectory datasets, and their provenance

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/) ≥ 0.12, Node.js 22 with pnpm 10 (via Corepack), and
GNU Make. From phase P3 on, the database gates also need Docker and the Supabase CLI.

Quickstart (`make docs-check` runs exactly this block in a clean container):

<!-- docs-check:start -->
```sh
make setup              # install Python and Node dependencies from the lockfiles
make lint typecheck     # gate G0
make test-unit          # gate G2
uv run python examples/basic_loop.py   # an agent stopped by Sunkcost, offline, no keys
```
<!-- docs-check:end -->

More gates:

```sh
make test-stats         # gate G2b: 500 simulated workloads (about 6 minutes on 4 cores)
make bench              # gate G1: downloads public datasets, regenerates docs/BENCHMARKS.md
make bench-sdk          # gate G3: check() latency (Python and Node), written to docs/PERF.md
make test-failure       # gate G4: failure injection against a local HTTP server (Python and Node)
make security           # gate G8: gitleaks, Semgrep, pip-audit, pnpm audit, Trivy
make docs-check         # gate G8: Markdown links and the quickstart above
```

The database gates and the local stack need Docker and the Supabase CLI:

```sh
supabase start          # local Postgres, Auth and PostgREST
make test-db            # gate G5: reset, pgTAP isolation tests, PostgREST over HTTP
make test-integration   # gate G6: the API and the worker against the local database
make test-e2e           # gate G6b: the console in Chromium against the local stack (Playwright)
docker compose up       # the API on http://localhost:8000 and the worker
make help               # every gate; one that cannot run exits 2 with "NOT RUN"
```

To run the console against that stack, take the anon key from `supabase status` and sign in at
http://127.0.0.1:3000 with any address; the magic link arrives in Mailpit at
http://127.0.0.1:54324.

```sh
cd apps/console
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321 NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key> \
  NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 pnpm dev
```

## Licence

Apache-2.0; see [`LICENSE`](LICENSE).

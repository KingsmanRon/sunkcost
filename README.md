# Sunkcost

Sunkcost stops AI agent runs that are going to fail before they spend the rest of their budget, while
keeping a statistically guaranteed share of the runs that would have succeeded. An in-process SDK
scores each step against a signed policy locally, with no network call on the hot path. Teams run it in
shadow mode first and get a savings report computed only from observed spend.

**Status:** pre-release, under construction. Phase P0 (contracts and tooling) is done; see
[`docs/PROGRESS.md`](docs/PROGRESS.md). There is nothing to install yet.

## Documents

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the specification and source of truth
- [`docs/adr/`](docs/adr/): decisions that refine or change the architecture
- [`packages/spec/`](packages/spec/): wire contracts (JSON Schema), the normative feature
  definition, golden test vectors and the price table

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/) ≥ 0.12, Node.js 22 with pnpm 10 (via Corepack), and
GNU Make. From phase P3 on, the database gates also need Docker and the Supabase CLI.

```sh
make setup              # install Python and Node dependencies from the lockfiles
make lint typecheck     # gate G0
make test-unit          # gate G2
make help               # every gate; ones not built yet exit 2 with "NOT RUN"
```

## Licence

Apache-2.0; see [`LICENSE`](LICENSE).

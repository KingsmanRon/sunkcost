# Sunkcost

Sunkcost stops AI agent runs that are going to fail before they spend the rest of their budget, while
keeping a statistically guaranteed share of the runs that would have succeeded. An in-process SDK
scores each step against a signed policy locally, with no network call on the hot path. Teams run it in
shadow mode first and get a savings report computed only from observed spend.

**Status:** pre-release, under construction. Done so far: P0 (contracts and tooling), P1 (core,
calibration library and the public benchmark, [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)) and P2
(the Python SDK and its LangGraph, OpenAI Agents and Cycles adapters, with offline
[`examples/`](examples/)). See [`docs/PROGRESS.md`](docs/PROGRESS.md). Nothing is published yet.

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

```sh
make setup              # install Python and Node dependencies from the lockfiles
make lint typecheck     # gate G0
make test-unit          # gate G2
make test-stats         # gate G2b: 500 simulated workloads (about 6 minutes on 4 cores)
make bench              # gate G1: downloads public datasets, regenerates docs/BENCHMARKS.md
make bench-sdk          # gate G3: check() latency, written to docs/PERF.md
make test-failure       # gate G4: failure injection against a local HTTP server
uv run python examples/basic_loop.py   # an agent stopped by Sunkcost, offline, no keys
make help               # every gate; ones not built yet exit 2 with "NOT RUN"
```

## Licence

Apache-2.0; see [`LICENSE`](LICENSE).

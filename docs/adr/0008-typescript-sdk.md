# ADR-0008: TypeScript SDK

- Status: accepted
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P6 PR

## Context

§4 C2/C3 says the TypeScript SDK mirrors the Python one in camelCase and depends only on
`@noble/ed25519` plus the platform `fetch`. Porting it needed some decisions the architecture
doesn't make, mostly where JavaScript differs from Python.

## Decisions

1. **The runtime is Node.js 22 or later.**
   - SHA-256, HMAC-SHA256 and the SHA-512 that Ed25519 needs come from `node:crypto`. Web
     Crypto is asynchronous, and `check()` and `recordStep()` must be synchronous.
   - Ed25519 is `@noble/ed25519` (§4 C3), verifying strictly per RFC 8032 (`zip215: false`),
     like the OpenSSL-backed Python SDK.
   - Browsers and edge runtimes are not v1 targets. Node 20 reached end of life in April 2026.
2. **API shape**:
   - Options objects in camelCase: `new Sunkcost({...})`, `startRun(workload, {runId,
     externalRef})`, `recordStep({kind, model, inputTokens, …})`.
   - Durations are in milliseconds (`flushIntervalMs`, `bundleRefreshMs`, `closeTimeoutMs`),
     where Python uses seconds.
   - `check()` is synchronous. `refreshBundles()`, `flush()` and `close()` return promises.
   - `Run.use(fn)` takes the place of Python's context manager, ending the run as `completed`,
     `aborted` (on a `RunAborted`) or `errored`.
3. **Money is exact.**
   - Amounts are `bigint` nano-dollars internally and decimal strings on the wire.
   - `costUsd` accepts a decimal string, a number (read at its shortest decimal form, as Python
     reads a float), or a `bigint` of whole dollars.
   - The price table is compiled in as `src/data/prices.ts`; a test fails if it differs from
     `packages/spec/prices`.
4. **Scores and thresholds are written in Python's `repr` form** (`1e-05`, `1.0`), so both SDKs
   send identical strings.
5. **Canonical JSON**:
   - JavaScript can't tell `1.0` from `1`, so a parsed `1.0` signs as `1`. The vectors already
     exclude that case.
   - Tool arguments that have no JSON form (a `Date`, a `bigint`, a cycle) are hashed over their
     `util.inspect` text instead. Their hashes differ from Python's, which only matters for
     values that aren't JSON.
6. **Flushing at exit**:
   - The SDK's own timers are unref'd, so they never keep a process alive.
   - A `beforeExit` hook flushes every open client, bounded at 3 s.
   - An explicit `process.exit()` skips that hook, so the caller should `await close()` first.
     This is documented in the package README.
7. **`flush()` honours its deadline even while a request hangs.** It races the send against the
   deadline, and `close()` then aborts in-flight requests. G4's slow-API cases found this: before
   the fix, `close()` waited out the 10 s request timeout.
8. **G4's agent-path check.** Node runs the agent on one thread, so the suite counts every socket
   connect and `fetch` started while an SDK call is on the stack, and requires zero. A mutation
   that starts a fetch in `check()` fails it.
9. **Shared disk cache.** The cache file names and the `_sunkcost_floor` field match the Python
   SDK, so both can use one `SUNKCOST_CACHE_DIR`. Each SDK verifies what it reads.
10. **Renamed wire types.** P0's wire types `Decision` and `Bundle` became `DecisionDoc` and
    `BundleDoc`, which frees the names for the SDK's own classes.

## Consequences

- `docs/ARCHITECTURE.md` §4 C2/C3 points here.
- G2, G3 and G4 now cover Node: `make test-unit`, `make bench-sdk` (a Node section in
  `docs/PERF.md`) and `make test-failure`.

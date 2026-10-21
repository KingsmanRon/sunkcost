# ADR-0004: Python SDK and adapter clarifications

- Status: accepted
- Date: 2026-10-21
- Deciders: implementing engineer; recorded for owner review in the P2 PR

## Context

Building the Python SDK (§4 C2) and the adapters (§4 C4) required decisions the architecture
leaves open, and checking each framework's released source showed where a stated hook needs a
nearest equivalent. Framework versions checked on 2026-10-21, from their published wheels:
langgraph 1.2.16, langchain-core 1.6.12, openai-agents 0.24.0, runcycles 0.5.3 (Cycles).

## Decisions

### SDK

1. **Bundle endpoint.** `GET /v1/bundles/active?workload=<name>` with `If-None-Match`.
   - 200: verify and install.
   - 304: keep the current bundle.
   - 404 (no active bundle): drop the cached bundle and its disk copy. That can only reduce
     enforcement.
   - Any other status, and network errors: keep the cached bundle until `expires_at`, and retry
     after 5 s, doubling up to the 300 s refresh interval.
   - A rejected bundle leaves the previous one in place.
2. **Decision order.** In order:
   1. `no_bundle`.
   2. `bundle_expired`: score and threshold are null, and the effective mode is `shadow`.
   3. `below_min_steps`, when fewer than `min_steps` steps are complete. The score is still
      reported.
   4. `below_threshold`.

   When score ≥ threshold (`would_abort`, the same comparison as calibration's `m_i ≥ τ`):
   - with an override: `override_shadow`;
   - else with a shadow bundle: `shadow_mode`;
   - else on a holdout run: `holdout`;
   - otherwise: ABORT, `above_threshold`.
3. **Rollback floor.**
   - A bundle with the same version as the cached one is accepted, because the worker re-signs
     daily.
   - A lower version is refused, including by a fetch that finishes after a newer version was
     installed.
   - With `SUNKCOST_CACHE_DIR`, the floor is stored with the cached bundle and survives restarts.
4. **Ingest status handling.**
   - 2xx: accepted.
   - 408, 425, 429, 5xx and network errors: retried with full-jitter exponential backoff (base
     0.5 s, cap 60 s, honouring `Retry-After` up to 60 s).
   - Any other 4xx: permanent. The batch is dropped and counted in the next `dropped_events`.
   - Batches hold at most 500 events and are gzipped from 1 KiB.
   - `close()` stops at its deadline (3 s by default) even while backing off.
5. **No endpoint, no API key: offline mode.** Decisions still work from installed or cached
   bundles. Events are discarded and counted (`stats.events_discarded_offline`). No default
   endpoint is hard-coded until the production domain exists.
6. **Missing `args_hash_salt`.** A random per-process salt is used, with a warning. Features
   only compare hashes within a run, so decisions are unaffected. Hashes from different
   processes are not comparable.
7. **Input sanitation, never raising.**
   - Strings are cut to the wire limits and NUL is removed; counts are clamped.
   - An invalid `cost_usd` becomes null.
   - An invalid `run_id` is replaced by a new UUIDv7.
   - An invalid workload name gives a degraded run that answers `sdk_error` and emits nothing.
   - Each of these increments `stats.sdk_errors`.
   - A float `cost_usd` is read at its shortest decimal form (`repr`) and quantised to 9 places.
     It is never used as a float for money.
8. **Trusted keys.**
   - `DEFAULT_TRUSTED_KEYS` is empty until the owner generates the production key offline. Until
     then, callers pass `trusted_keys`.
   - The RFC 8032 test key can never be a default, and a test enforces this.
   - `sunkcost.testing` provides a test-key signer, an in-memory API and `demo_client` for
     examples. Each of them must be trusted explicitly.
9. **Python 3.10.** The SDK supports Python ≥ 3.10, but the workspace lockfile resolves for 3.12
   (the worker's floor). The SDK tests on 3.10 therefore use a fresh resolve within the declared
   minimums (`make test-sdk-py310`, CI job "SDK on Python 3.10").

### Adapters

10. **LangGraph.**
    - The adapter is a LangChain callback handler. `check()` runs in `on_chat_model_start` (or
      `on_llm_start`) and `on_tool_start`, and steps are recorded in the `*_end` and `*_error`
      callbacks.
    - LangChain logs and swallows handler exceptions unless the handler sets `raise_error`, so
      the adapter sets it and raises only `RunAborted`.
    - The "error edge" is LangGraph's node `error_handler`, available from 1.2, which receives a
      `NodeError`.
    - `ToolNode(handle_tool_errors=True)` would turn the abort into a tool message, so the
      example passes a narrow exception type.
11. **OpenAI Agents SDK.**
    - `RunHooks.on_llm_start` and `on_tool_start` are awaited immediately before each model call
      and each local tool call, so `check()` runs there.
    - An exception from `on_tool_start` reaches the caller wrapped in
      `UserError("Error running tool …")`. `run_agent` re-raises it as `RunAborted`, and
      `find_abort` finds it in any wrapped exception.
12. **Cycles (`runcycles`).**
    - Cycles has no lifecycle hooks.
    - Its decorator retries a failed `create_reservation` once and re-raises the failure as
      `CyclesProtocolError`, so `check()` cannot run inside `create_reservation`.
    - Nearest equivalent: a `guard` decorator stacked above `@cycles`, which runs `check()`
      before the reservation lifecycle starts. On ABORT, nothing is reserved.
    - For direct client use, `reserve` and `commit` helpers run `check()` and then
      `create_reservation`, and record the step after `commit_reservation`. The committed
      `USD_MICROCENTS` amount is converted at 10⁸ per USD.
    - The current run is bound with a context variable (`bind_run`).

## Consequences

- `docs/ARCHITECTURE.md` §4 C2, §4 C4 and §5.1 ② now state the bundle endpoint, the decision order
  and the adapter hook points.
- Before release, the owner adds the production public key to `sunkcost/keys.py`, and the
  production endpoint is decided (P7 checklist).

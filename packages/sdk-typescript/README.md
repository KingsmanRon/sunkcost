# sunkcost (TypeScript SDK)

Pre-release. The Node.js counterpart of the Python SDK, with the same decisions, features, hashes
and wire format (both pass the shared vectors in `packages/spec/vectors`). `check()` is
synchronous and scores the run so far against a signed policy bundle locally, with no network
call. Events are buffered and sent in the background. Every call is fail-open: an SDK problem
never stops, slows or throws into your agent.

```ts
import { Sunkcost } from "sunkcost";

const sl = new Sunkcost(); // SUNKCOST_API_KEY, SUNKCOST_ENDPOINT, SUNKCOST_ARGS_SALT from the environment

const run = sl.startRun("support.billing_dispute", { externalRef: "ZD-8812" });
let decision = run.check(); // before each step
while (!done && !decision.abort) {
  // ... run the model or tool call; on ABORT your code decides what an abort means
  run.recordStep({ kind: "model", model: "claude-sonnet-4-5", inputTokens: 812, outputTokens: 240 });
  decision = run.check();
}
run.end(decision.abort ? "aborted" : "completed");
run.outcome("success"); // or later, from your business system, via the API
```

- **Runtime**: Node.js 22 or later. The only dependency is `@noble/ed25519`; HTTP uses the
  platform `fetch`.
- **Exit**: buffered events are flushed when the event loop empties (bounded at 3 s). Call
  `await sl.close()` before an explicit `process.exit()`.
- **Offline**: with no API key or endpoint the SDK still decides, with events discarded.
  `sunkcost/testing` has `demoClient()` and an in-memory transport for tests.
- **Design**: `docs/ARCHITECTURE.md` §4 C3 and ADR-0008.

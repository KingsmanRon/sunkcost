# sunkcost (Python SDK)

Pre-release. Stops AI agent runs that are going to fail before they spend the rest of their
budget. `check()` scores the run so far against a signed policy bundle, locally, with no network
call. Events are buffered and sent in the background. Every call is fail-open: an SDK problem
never stops, slows or errors your agent.

```python
from sunkcost import Sunkcost

sl = Sunkcost()  # SUNKCOST_API_KEY, SUNKCOST_ENDPOINT, SUNKCOST_ARGS_SALT from the environment

with sl.start_run("support.billing_dispute", external_ref="ZD-8812") as run:
    while not done:
        decision = run.check()  # before each step
        if decision.abort:
            break  # your code decides what an abort means
        ...  # run the model or tool call
        run.record_step(kind="model", model="claude-sonnet-4-5", input_tokens=812, output_tokens=240)
run.outcome("success")  # or later, from your business system, via the API
```

- **Decisions**: `Decision.action` is `CONTINUE` or `ABORT`; `reason` says why. With no bundle
  yet, every decision is `CONTINUE` / `no_bundle` (shadow data still flows for calibration).
- **Privacy**: tool arguments are HMAC-hashed with your salt in-process and never sent; no prompt
  or completion content is captured.
- **Adapters**: `sunkcost-langgraph`, `sunkcost-openai-agents`, `sunkcost-cycles`; runnable
  offline examples in [`examples/`](../../examples/).
- **Design**: `docs/ARCHITECTURE.md` §4 C2 and ADR-0004.

# sunkcost-cycles

Sunkcost in front of [Cycles](https://runcycles.io) budget reservations: `check()` runs before
the reservation, so an aborted step reserves nothing.

```python
from sunkcost_cycles import bind_run, guard

@guard(kind="tool", name="restart_service")
@cycles(estimate=500_000, action_kind="tool.call", action_name="restart_service")
def restart_service(): ...

with bind_run(sl.start_run("ops.triage_alert")):
    restart_service()  # raises RunAborted before Cycles reserves anything
```

`guard` must sit above `@cycles`. For direct client use, `reserve(client, request)` and
`commit(client, reservation_id, request, kind=..., name=...)` do the same and record the committed
cost. Example: [`examples/cycles_agent.py`](../../examples/cycles_agent.py).

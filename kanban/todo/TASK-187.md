---
"type": "devlegate.ticket"
"title": "Log ticket reset lifecycle transitions in the service journal"
"depends_on": ["TASK-133"]
---

## Problem

The operator runs `devlegate reset TASK-XXX --json` and receives an admitted durable operation, typically at `stopping-worker`, but the service journal does not identify the reset or its progress. The operator sees only indirect control synchronization, worktree and worker-launch messages. In particular, an admitted reset can remain blocked or progress asynchronously without a corresponding human-readable service trace.

The durable reset intent, runtime state, Git reset artifact and existing inspection commands are the source of truth. **This ticket adds operator-facing observability, not a second status or audit protocol.**

## Scope and required behavior

1. Log **newly admitted** ticket resets after durable admission succeeds, identifying canonical ticket ID, durable `operation_id` and initial stage. A duplicate request returning the existing reset operation must not produce another admission event. Rejected reset requests must never be logged as accepted.
2. Log meaningful **completed stage transitions** with consistent searchable prefix (for example `ticket reset`), ticket ID, operation ID and stage/outcome:
   - worker cancellation/retirement proven; advance to `retiring-old-generation`;
   - old resource generation retirement proven; advance to `requeue`;
   - requeue publication and activation proven; advance to `queued`;
   - the fresh execution **actually started**; include its new execution ID when the durable owner state records `started`.
   Distinguish a stage becoming eligible from the external effect actually being completed. Do not log `started` merely because a worker was scheduled, a prompt was built, or a reset was queued.
3. When reset processing becomes **blocked**, write one diagnostic identifying the ticket, operation, current stage and a bounded/sanitized reason. A later successful transition after the blocker clears should be visible. Do not flood the log when the scheduler retries the same unchanged blocker. The existing blocked semantics and actionable error returned to the operator must remain intact.
4. Make messages reflect **persisted or independently proven state**, not intentions before effect. No success message for a failed/uncertain state write, worker retirement, resource retirement, control publication or worker launch. In recovery/replay, log a genuine new transition but do not relabel a previously completed stage as a fresh action on every scheduler pass or duplicate IPC request.
5. Use the established service logging facility and owner-serialized reset lifecycle. All lines must be safe for systemd/journal and `devlegate logs service`: avoid raw exception strings containing paths, credentials, environment, arbitrary Git stderr or private worker data. Use existing sanitization where adequate; otherwise add a narrowly scoped bounded diagnostic. The ticket ID and operation ID are allowed operator identifiers.
6. Include both reset stages implemented in `src/devlegate/ticket_reset.py` and the `started` transition owned by `src/devlegate/runtime.py`. Avoid instrumenting only the IPC/CLI response, since it is not the durable completion boundary.

## Boundaries

- **No change** to reset admission policy, worker cancellation, WAL/CAS ordering, resource identity proof, replay/retry, scheduling, Git commits, canonical tickets, durable reset schema, API or CLI JSON output.
- Do not introduce a new event database, outbox, compatibility layer, periodic status logger or general-purpose tracing framework. The journal is best-effort diagnostic output; after an abrupt crash, persisted reset state remains authoritative. No claim of exactly-once durable logging across crashes.
- Keep the change localized to reset lifecycle logging and its focused tests. Preserve existing log messages for worker, control and execution lifecycle unless an exact duplication needs to be avoided.

## Acceptance tests

1. A disposable owner/runtime fixture admits reset and observes one admission line containing its exact ticket and operation IDs; repeating the same request does not re-log it. A failed admission emits no success line.
2. Exercise the real reset state machine through retirement, requeue and fresh worker start. Assert order, stage wording and identity consistency; no stage success line precedes the relevant durable/proven transition.
3. Inject an uncertain/failed retirement, publication or state persistence boundary. Assert no false completion line; one bounded blocked diagnostic with correct stage; unchanged retries do not spam; a subsequent successful advance reports progress.
4. Simulate service interruption/restart or replay at an already completed stage. Assert no repeated historical success messages and no spurious fresh-worker-start event; existing reset recovery behavior remains unchanged.
5. Run focused unit and real-Git/service tests, `./dev lint`, `git diff --check`, and complete hosted CI on the exact final checkpoint SHA. No live `archbig` deployment or local runtime mutation required for this ticket.

## Operator result

After `devlegate reset TASK-XXX`, `devlegate logs service` should show a coherent, low-noise lifecycle keyed by `ticket_id` and `operation_id`, including the exact point at which a new execution actually begins. For authoritative current status, continue using `devlegate status` or `devlegate inspect ticket`.

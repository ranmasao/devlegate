# CLI domains: universal inspect, workspace prune, fresh execution reset

**Date:** 2026-11-13
**Control baseline:** `3bec84c8efa3325251d08fdfd08d61337d17a7fc`
**Status:** implementation scheduled; no runtime mutation

## Design decision

The old `devlegate clean` domain conflated normal cleanup and an explicit operator
request to start a ticket again. The CLI uses responsibility-specific domains,
without compatibility aliases or migrations.

- `devlegate inspect ticket|execution|workspace|service ...` is a **read-only**
  exact-object observation domain. A workspace view may contain evidence/digest,
  but inspecting is not an authorization to mutate.
- `devlegate workspace ls` and `devlegate workspace prune` manage inventory
  and routine disposal of proven **done-ticket** resources. Prune remains
  separate from execution reset. There is no generic `gc` domain.
- `devlegate reset <ticket-id>` is the **one-argument operator action to start
  the same ticket again from a clean execution**. It does not require the operator
  to obtain/pass a digest, generation, execution ID or request ID. The owner derives
  all identity/transaction information, stops any managed worker for that ticket,
  proves retirement/isolation, handles resources safely, and makes the ticket
  runnable in `todo` with a new execution generation from current product/model.
  Only standard optional output formatting may affect presentation.

**Reset eligibility:** the ticket is in `todo`, or its canonical workflow
permits requeue to `todo`. Other statuses are not rejected by an extra
reset-specific hard-coded list. An illegal transition is a workflow refusal;
uncertain authority, failed worker retirement or unsafe resource state is an
operational blocker and must never be bypassed. The one-argument interface
does **not** remove exact owner/provenance/CAS/lease guarantees. Never report
fresh execution started until that has been proved. Historical reports/reviews
remain evidence; prior worker code, conversation and rework context do not
continue into the new attempt. Reset does not undo previously merged product
changes.

## Ticket plan and history

TASK-160/130/131 established inspect evidence, owner-serialized durable reset
authorization and staged CAS/lease retirement. TASK-161 established read-only
`inspect`. TASK-162 provides `workspace ls/prune`. TASK-132 completes
the operator-facing one-argument fresh-execution reset and its end-to-end
active-worker cancellation, control transition, crash recovery and scheduling
semantics.

The original TASK-132 draft contained a manual
`--plan-digest --generation --request-id` command and backlog-only eligibility;
**these are explicitly superseded** by the one-argument product decision above.
Do not change historical accepted worker/checkpoint reviews. No production code,
runtime/owner state or live Git resources are modified by this planning update.

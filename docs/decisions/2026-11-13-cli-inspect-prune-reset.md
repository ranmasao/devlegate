# CLI domains: universal inspect, workspace prune, fresh execution reset

**Date:** 2026-11-13
**Control baseline:** `3bec84c8efa3325251d08fdfd08d61337d17a7fc`
**Status:** implementation scheduled; no runtime mutation

## Design decision

The current `devlegate clean` domain conflates two unrelated operations:
automatic resource cleanup of already-integrated done tickets, and human-
authorized reset of unsuccessful executions to begin a new generation.
Rename the interface by responsibility, without creating permanent
compatibility aliases or migration scaffolding (no external customer base).

- `devlegate inspect ticket|execution|workspace|service ...` is a universal,
  detailed **read-only** object observation domain. Inspection never grants
  authority to mutate. The existing TASK-160 identity-bound digest is
  exposed through `inspect workspace <ticket-id>` for future reset confirmation.
- `devlegate workspace ls` and `devlegate workspace prune` manage resource
  inventory and normal proven **done-ticket** cleanup, reusing existing owner
  cleanup policy. Prune is read-only by default; effects require `--apply`.
  There is no generic `gc` or `system prune` at this stage.
- `devlegate reset <ticket-id>` is a separate explicit operator action for
  a backlogged ticket with a proved prior generation: safely retire old
  execution resources and requeue the same ticket into a fresh execution
  from **current** product/model configuration, with no old checkpoint,
  worker conversation or previous rework as inherited implementation.
  Historical reports/reviews remain immutable.

An authorization-only pre-effect acknowledgement is not reset completion.
No `reset inspect`, implicit force-delete, unbound resource adoption or
fresh execution admission before proven retirement.

## Ticket plan

TASK-131 has already published a completed worker report and is in review;
do **not** edit or requeue its historical reviewed ticket merely for CLI
semantics. Keep its exact checkpoint and review boundary unchanged.

Create independently reviewable `todo/TASK-161` for the universal read-only
inspect domain (depends on TASK-131), `todo/TASK-162` for workspace list/prune
(depends on TASK-161), and update still-unexecuted `todo/TASK-132` to depend
on TASK-131 and TASK-162 and own the final reset CLI and full-cycle fresh
execution validation. These new tickets can remain todo but are not runnable
until TASK-131 is integrated. No production code, runtime state, immutable
execution report/review, live Git ref or worktree is changed by this decision.

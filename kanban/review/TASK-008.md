---
"type": "devlegate.ticket"
"title": "Report scheduler blocking reasons accurately"
---

## Milestone

Devlegate 0.5.5 self-hosting diagnostics hardening.

## Goal

Make scheduler/service diagnostics state the actual reason runnable-looking todo
work is blocked instead of reporting dependency blocking for unrelated barriers.

## Context

After TASK-002 completed and entered review, the service repeatedly logged:

```text
todo tickets are currently blocked by unfinished dependencies
```

However, TASK-003 through TASK-007 have no `depends_on` entries. They were blocked
by the serial review barrier while TASK-002 awaited review.

The current idle/no-selection logging path uses the presence of todo tickets as a
proxy for unfinished dependencies and can therefore report a false causal reason.

Operator diagnostics must distinguish dependency blocking from review/accepted
serial barriers and other scheduler barriers already represented by the execution
plan/admission model.

## Required behavior

- When todo work is blocked by unfinished declared dependencies, service diagnostics
  may report dependency blocking and identify that class of cause accurately.
- When work is blocked because another ticket is in review, diagnostics report the
  review barrier rather than unfinished dependencies.
- When work is blocked by an accepted/integration barrier or another known scheduler
  barrier, diagnostics report that actual barrier class rather than reusing a
  dependency message.
- The diagnostic reason should come from the same scheduler/admission semantics used
  to decide the plan, not from a separate heuristic based only on todo counts.
- This change must not alter ticket selection, dependency semantics, serial review
  policy, or admission behavior; it changes operator-visible diagnosis only.
- Repeated polling may repeat a correct status message, but must not repeatedly state
  a false reason.

## Acceptance criteria

- With one ticket in review and unrelated todo tickets with no dependencies, the
  service no longer logs `blocked by unfinished dependencies`.
- With a todo ticket genuinely blocked by an unfinished `depends_on`, dependency
  blocking is still reported accurately.
- Review, accepted/integration, and dependency barriers remain distinguishable in
  service diagnostics.
- Scheduler behavior is unchanged.
- Full tests and lint remain green.

## Required regressions

- Given a ticket in review and another independent todo ticket, when the scheduler
  selects no new worker, then the emitted diagnostic identifies the review barrier
  and does not mention unfinished dependencies.
- Given a todo ticket whose declared dependency is not done, when the scheduler
  selects no worker, then the emitted diagnostic identifies unfinished
  dependencies.
- Given an accepted ticket awaiting integration plus additional todo work, when the
  scheduler is blocked by the accepted boundary, then diagnostics identify that
  boundary rather than dependency blocking.

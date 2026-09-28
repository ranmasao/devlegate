---
"type": "devlegate.ticket"
"title": "Introduce a canonical QA workflow stage"
---

## Milestone

Next-iteration validation pipeline.

## Goal

Add a first-class `qa` Kanban stage between worker execution and human review so
deterministic validation has its own durable workflow state.

Every valid worker handoff must enter QA before review. QA is a machine-owned
verification boundary; review remains the human/semantic approval boundary.

## Context

TASK-011 established the current lifecycle:

```text
todo -> worker handoff -> review -> accepted -> done
```

That causes deterministic mechanical failures such as lint errors to reach review,
even though no reviewer judgment is needed to discover them.

The intended model is:

```text
todo
  -> worker execution
  -> qa
       -> review       when deterministic QA passes or is not applicable
       -> todo         when deterministic QA proves the checkpoint invalid
       -> qa           while deterministic QA cannot obtain a trustworthy result
  -> accepted
  -> done
```

This ticket introduces the workflow stage and its durable state-machine semantics.
The concrete validator catalog and validator execution engine are separate tickets.

## Required behavior

- Add a canonical QA path, defaulting to `kanban/qa`, alongside backlog, todo,
  review, accepted, and done.
- A ticket exists in exactly one canonical Kanban stage at a time.
- Every valid worker semantic handoff enters QA rather than review, including
  `completed`, `incomplete`, and `blocked` claims.
- The QA state is bound to the exact ticket, execution identity, checkpoint/product
  delta, and worker handoff that produced it.
- Entering QA does not integrate or otherwise mutate the product checkpoint.
- QA supports three disposition classes without conflating them:
  - validated/not-applicable -> review;
  - deterministic validation failure -> todo;
  - validation unavailable/indeterminate -> remain in QA.
- A ticket in QA is not schedulable for a normal worker execution until a QA
  disposition moves it elsewhere.
- Review semantics remain unchanged: reviewer may send review -> todo or
  review -> accepted; reviewer never bypasses QA for a new worker result.
- Accepted/finalization semantics remain unchanged.
- Dependencies continue to be satisfied only by `done`; QA does not satisfy a
  dependency.
- Status, plan, scheduler diagnostics, role rendering, bootstrap validation, and
  ticket-store validation understand the QA stage explicitly.
- Control advancement, crash/restart, and lifecycle replay remain idempotent and
  fail closed; a replay cannot duplicate a QA transition or attach a QA result to a
  different execution/checkpoint.
- The stage must support an immediate machine transition QA -> review when later
  validation logic determines that no validators apply; it must not require a
  human-visible pause merely because QA exists.

## Acceptance criteria

- A valid worker handoff that previously created `kanban/review/T.md` now creates
  `kanban/qa/T.md` first.
- The ticket store rejects duplicate copies spanning QA and any other stage.
- Status/plan reports distinguish QA waiting from review waiting.
- Scheduler admission never launches an ordinary worker for a ticket parked in QA.
- QA -> review, QA -> todo, and remain-QA dispositions preserve exact execution and
  checkpoint identity.
- Existing review -> todo, review -> accepted, accepted finalization, and dependency
  semantics remain intact.
- Recovery after interruption at each QA transition boundary is idempotent.
- Architect and reviewer protocol material describes the QA boundary accurately.
- Full tests and lint for the implementation remain green.

## Required regressions

- Completed worker handoff -> QA, not review.
- Incomplete worker handoff -> QA, not review.
- Blocked worker handoff -> QA, not review.
- QA validated/not-applicable disposition -> exact same ticket in review.
- QA deterministic failure disposition -> exact same ticket in todo.
- QA infrastructure/indeterminate disposition -> ticket remains in QA.
- Duplicate QA/review or QA/todo copies fail closed.
- A dependency on a QA ticket remains unsatisfied.
- Crash/replay before and after publishing a QA transition does not duplicate or
  skip workflow state.
- Compatible control advancement during worker execution can replay the handoff into
  QA without losing execution provenance.

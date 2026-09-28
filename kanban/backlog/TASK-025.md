---
"type": "devlegate.ticket"
"title": "Introduce a canonical QA workflow stage"
"depends_on": ["TASK-038"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

Add a first-class `qa` Kanban stage/capability so deterministic validation can
have its own durable workflow state when the project workflow policy enables that
gate.

For Devlegate's own dogfooding policy, worker handoff must enter QA before human
review. QA is a machine-owned verification boundary; review remains a human/semantic
approval boundary.

QA is not globally mandatory for every Devlegate-managed project. The route is
selected by the project-owned workflow policy from TASK-038.

## Context

TASK-011 established the current lifecycle:

```text
todo -> worker handoff -> review -> accepted -> done
```

That causes deterministic mechanical failures such as lint errors to reach review,
even though no reviewer judgment is needed to discover them.

For Devlegate itself, the intended configured model is:

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

TASK-038 defines how a project selects that route or a different supported gate
sequence. This ticket implements the QA state and its durable semantics, not a
universal hard-coded worker destination.

## Required behavior

- Add a canonical QA state/path, defaulting to `kanban/qa` when the project
  workflow policy enables the QA gate.
- A project whose configured workflow omits QA must not require the QA directory and
  must preserve its configured non-QA worker handoff.
- A ticket exists in exactly one enabled canonical Kanban state at a time.
- For a route that selects QA, every valid worker semantic handoff enters QA,
  including `completed`, `incomplete`, and `blocked` claims.
- The QA state is bound to the exact ticket, execution identity, checkpoint/product
  delta, worker handoff, and workflow-policy generation that produced it.
- Entering QA does not integrate or otherwise mutate the product checkpoint.
- QA supports three disposition classes without conflating them:
  - validated/not-applicable -> policy-configured QA success target;
  - deterministic validation failure -> todo;
  - validation unavailable/indeterminate -> remain in QA.
- A ticket in QA is not schedulable for a normal worker execution until a QA
  disposition moves it elsewhere.
- When the configured QA success target is human review, existing review semantics
  remain unchanged: reviewer may send review -> todo or review -> accepted.
- When project policy explicitly authorizes QA success to reach accepted directly,
  that transition must preserve exact policy/evidence authority and must not be
  inferred merely from the absence of a review directory.
- Dependencies continue to be satisfied only by `done`; QA does not satisfy a
  dependency.
- Status, plan, scheduler diagnostics, role rendering, bootstrap validation, and
  ticket-store validation understand QA explicitly when enabled.
- Control advancement, crash/restart, and lifecycle replay remain idempotent and
  fail closed; a replay cannot duplicate a QA transition or attach a QA result to a
  different execution/checkpoint/policy generation.
- The QA gate must support an immediate machine transition on NOT_APPLICABLE; it must
  not require a human-visible pause merely because QA exists.

## Acceptance criteria

- Under Devlegate's explicit QA+review policy, a valid worker handoff that previously
  created `kanban/review/T.md` now creates `kanban/qa/T.md` first.
- Under a policy with no QA gate, the same worker handoff follows its configured
  route without creating or requiring QA state.
- The ticket store rejects duplicate copies spanning QA and any other enabled state.
- Status/plan reports distinguish QA waiting from review waiting.
- Scheduler admission never launches an ordinary worker for a ticket parked in QA.
- QA validated/not-applicable follows the exact configured success target.
- QA deterministic failure -> todo.
- QA infrastructure/indeterminate disposition -> ticket remains in QA.
- Existing review -> todo, review -> accepted, accepted finalization, and dependency
  semantics remain intact when those gates/states are configured.
- Recovery after interruption at each QA transition boundary is idempotent.
- Architect and reviewer protocol material describes the configured QA boundary
  accurately rather than asserting QA universally.
- Full tests and lint for the implementation remain green.

## Required regressions

- QA+review policy: completed worker handoff -> QA, not review.
- QA+review policy: incomplete worker handoff -> QA.
- QA+review policy: blocked worker handoff -> QA.
- No-QA policy: worker handoff follows configured next gate and no QA directory is
  required.
- QA validated/not-applicable -> exact configured success target.
- QA deterministic failure -> exact same ticket in todo.
- QA infrastructure/indeterminate disposition -> ticket remains in QA.
- Duplicate QA/review or QA/todo copies fail closed when those states are enabled.
- A dependency on a QA ticket remains unsatisfied.
- Crash/replay before and after publishing a QA transition does not duplicate or
  skip workflow state.
- Compatible control advancement during worker execution can replay the handoff into
  its policy-bound QA gate without losing execution provenance.

---
"type": "devlegate.ticket"
"title": "Route valid worker handoffs through review and accepted finalization"
"depends_on": ["TASK-002"]
---

## Milestone

Devlegate 0.5.5 worker/reviewer workflow hardening.

## Goal

Make `review` the universal boundary after any valid semantic worker handoff and
make `accepted` the universal authorization for Devlegate to finalize a reviewed
result, whether finalization requires a product fast-forward or is a proven
zero-product-delta no-op.

The worker reports what happened. The reviewer/architect decides whether the same
ticket needs more work or whether Devlegate may finalize the reviewed result.

## Context

The current typed worker boundary already distinguishes:

- `completed`: the worker believes the assigned work is complete;
- `incomplete`: useful work was done but work remains;
- `blocked`: safe progress needs external information or action.

`WorkerClaim` also carries `summary`, `remaining`, and `questions`, and
ExecutionReport persists those fields in the control plane.

Today only `completed` moves a ticket from `todo` to `review`.
`incomplete` and `blocked` leave the ticket in `todo`. That lets worker
outcome semantics choose workflow state and does not provide a clean review path for
questions, partial results, or report-only tasks.

Analysis/report tasks introduce a second mismatch. They can complete successfully
with no tracked product delta. They still need durable review evidence and an
explicit architectural acceptance decision, but there is no product commit to
fast-forward.

Use the existing states rather than adding a special blocked/report state:

```text
todo
  |
  | valid worker semantic handoff
  v
review
  |
  +-- more work / question resolved ----------------------------> todo
  |
  +-- reviewed result approved --------------------------------> accepted
                                                               |
                                      +------------------------+
                                      |
                         Devlegate finalizes exact reviewed result
                                      |
                     +----------------+----------------+
                     |                                 |
              product delta                      zero product delta
              fast-forward                       proven no-op
                     |                                 |
                     +----------------+----------------+
                                      |
                                     done
```

There is no direct reviewer-owned `review -> done` path in this model. `done`
remains the terminal state produced after Devlegate has proven and finalized an
accepted result.

## Worker handoff semantics

A worker attempt that exits through one valid typed `devlegate_report` has
successfully handed a semantic result back to the control plane.

Therefore all valid outcomes:

- `completed`;
- `incomplete`;
- `blocked`;

must persist immutable execution evidence and move the SAME ticket from `todo` to
`review`.

The outcome remains evidence about the worker's belief. It does not itself choose
`accepted`, `todo`, retry, or integration.

By contrast, a process crash/non-zero process failure, transport failure,
missing/duplicate/malformed typed egress, or otherwise untrustworthy result remains
an execution failure. Such a failure MUST NOT enter `review` and continues through
the existing failed-execution/retry/recovery machinery.

## Review decisions

For a ticket in `review`, the reviewer/architect chooses one of two workflow
directions.

### Review -> todo

Use the SAME ticket when more worker work is required.

This includes a worker claim of `incomplete`, a `blocked` question that the
architect answers, ordinary review hardening, or any other case where the reviewed
result is not yet the final ticket result.

When resolving a worker question, record the authoritative answer or missing context
in the canonical ticket specification or canonical project context before moving
the ticket back to `todo`. The prior ExecutionReport, questions, report artifact,
and checkpoint evidence remain immutable history.

### Review -> accepted

Move the SAME ticket to `accepted` when the reviewer/architect approves the exact
reviewed execution result as satisfying the ticket.

`accepted` means:

> Devlegate is authorized to finalize this exact reviewed execution result.

It does NOT imply that the result necessarily contains a product-tree delta.

For a product-changing result, finalization integrates the exact reviewed
checkpoint using the existing ancestry-only/fail-closed rules.

For a zero-product-delta result, finalization is an explicit proven no-op: Devlegate
proves that there is no product change to integrate, leaves the product ref
unchanged, safely retires any execution workspace/branch state, preserves durable
control evidence, and then moves the ticket to `done`.

Thus both kinds of successful work use the same terminal path:

```text
review -> accepted -> done
```

## Exact reviewed-result binding

Acceptance/finalization MUST be bound to the exact execution result that caused the
ticket to enter `review`.

Do not select an execution merely because it is the newest report, has a matching
ticket ID, or has convenient commit prose.

The control history/lifecycle evidence must make the reviewed execution identity
deterministic. If the accepted ticket cannot be bound unambiguously to exactly one
reviewed ExecutionReport and its corresponding checkpoint/report evidence,
finalization fails closed.

This exact binding applies equally to product-changing and zero-delta results.

## Durable long-form reports

Some valid worker handoffs produce a report rather than a product patch. Those
results must be reviewable from durable control-plane evidence and must not exist
only in runtime execution logs.

Extend the typed worker egress with an optional long-form human-readable report
payload suitable for research, investigation, audit, or diagnostic output.

Devlegate, not the worker, owns persistence into the control branch.

A suitable durable representation is:

```text
executions/<ticket-id>/<execution-id>.json
executions/<ticket-id>/<execution-id>.md    # optional long-form report
```

Equivalent naming/schema is acceptable if it preserves these invariants:

- the worker cannot write, stage, commit, or push control-plane files directly;
- long-form content crosses a strictly validated typed egress boundary;
- Devlegate persists it under the exact execution identity;
- ExecutionReport carries immutable reference/integrity data binding the optional
  report artifact to that execution;
- persistence is non-overwriting and replay/recovery safe;
- report evidence is preserved after execution workspace/branch retirement;
- absence of a long-form report is valid for ordinary implementation and
  question-only results.

`summary` remains the concise semantic result defined by TASK-002. Do not stuff a
long report into `summary`.

`questions` remains structured unresolved questions requiring external
information or a decision.

`remaining` remains structured concrete work that still remains.

## Zero-product-delta finalization

Zero delta is a first-class successful execution result, not a failure and not a
reason to manufacture history.

A zero-delta result must satisfy explicit proof before accepted finalization:

- the reviewed execution is exactly bound;
- its workspace/checkpoint identity proves no tracked product change relative to
  the admitted product base;
- no synthetic/empty product commit was created merely for workflow purposes;
- product-generation/reconciliation rules prove that the reviewed result is still
  admissible rather than silently finalizing stale or ambiguous work;
- any execution branch/worktree can be retired safely;
- if a remote execution branch exists, deletion/retirement uses exact identity and
  lease-safe semantics rather than an unqualified delete;
- product branch HEAD is unchanged by the no-op finalization;
- control-plane ExecutionReport and optional report artifact remain durable.

After those proofs, Devlegate performs the accepted-to-done control transition just
as it does after an effectful integration.

The no-op path should be understood as a conditional integration whose proven
product effect is the identity operation.

## Role protocol

Update packaged Reviewer/Architect guidance to match this state machine.

For every review ticket, inspect as applicable:

- the canonical ticket specification;
- the exact ExecutionReport/WorkerClaim that caused entry into review;
- `summary`, `remaining`, and `questions`;
- any durable long-form execution report;
- product checkpoint/diff when a delta exists;
- relevant tests, source, history, and project context.

The higher-authority role then either:

- returns the SAME ticket to `todo` with any authoritative answer/hardening
  recorded; or
- moves the SAME ticket to `accepted`, authorizing Devlegate to finalize that
  exact reviewed result.

Reviewer guidance must not require a product delta as a precondition for
`accepted`.

Reviewer/architect guidance must not move reviewed work directly to `done`;
Devlegate owns accepted finalization and the terminal transition.

## Required behavior

- Every valid `completed`, `incomplete`, or `blocked` claim persists durable
  execution evidence and moves `todo -> review`.
- Worker outcome never directly chooses `accepted`, `todo`, or retry.
- Failed/untrustworthy worker execution does not enter review.
- `questions` and `remaining` survive unchanged in durable execution evidence.
- Optional long-form reports can be persisted as Devlegate-owned control evidence
  exactly bound to one execution.
- Review resolves to either `todo` or `accepted`.
- `accepted` can finalize either an effectful product checkpoint or a proven
  zero-delta result.
- `done` is reached only after Devlegate finalizes the accepted result.
- Only `done` satisfies dependencies.
- Existing serial review/accepted scheduling barriers remain deterministic.
- Recovery/replay correctness depends on canonical state/evidence, not commit prose
  or worker free-form output.

## Acceptance criteria

- A valid `completed` claim with product changes enters `review`, can be moved to
  `accepted`, and follows the existing exact product integration path to `done`.
- A valid `incomplete` claim enters `review` with remaining work preserved.
- A valid `blocked` claim enters `review` with questions preserved.
- After a reviewer/architect records an answer and returns the same blocked ticket
  to `todo`, a later worker can continue it normally.
- A valid zero-product-delta report/analysis execution enters `review` without a
  manufactured product commit.
- The reviewer/architect can approve that exact zero-delta result by moving the
  ticket to `accepted`.
- Devlegate proves the zero-delta finalization, leaves product HEAD unchanged,
  retires execution workspace/branch state safely, and moves the ticket to `done`.
- Optional long-form report evidence remains durable and exactly bound after that
  retirement.
- A crash, process failure, transport failure, or invalid/missing typed report
  remains a failed execution and never enters `review`.
- Full tests, lint, recovery tests, and control-plane exactness checks remain green.

## Required regressions

- Given each valid worker outcome, lifecycle application moves `todo -> review`
  exactly once and preserves the claim verbatim.
- Given a blocked claim with questions, restart/replay/compatible control drift
  preserves the exact questions and review state without duplicate evidence.
- Given an incomplete claim with partial product changes, review can return the same
  ticket to `todo` and a later execution continues without losing historical
  evidence or inventing a replacement ticket.
- Given an optional long-form report, lifecycle commit/replay preserves the exact
  artifact and its integrity binding without overwrite.
- Given a reviewed product-changing checkpoint moved to `accepted`, finalization
  still performs only ancestry-safe product integration and then `done`.
- Given a reviewed zero-delta result moved to `accepted`, finalization performs no
  product commit or product-ref change, retires execution state safely, and then
  moves to `done`.
- Given product/control drift that makes a zero-delta reviewed result ambiguous or
  stale under existing generation rules, finalization blocks rather than guessing.
- Given an accepted ticket, finalization identifies exactly the execution that
  entered review; multiple historical reports for the same ticket do not cause
  latest-report guessing.
- Given failed/invalid worker egress, the ticket stays outside review and existing
  failed-execution/retry behavior remains intact.

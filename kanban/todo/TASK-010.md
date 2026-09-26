---
"type": "devlegate.ticket"
"title": "Route every valid worker handoff through review"
"depends_on": ["TASK-002"]
---

## Milestone

Devlegate 0.5.5 worker/reviewer workflow hardening.

## Goal

Make `review` the universal boundary after a worker successfully returns a valid
semantic result, regardless of whether the worker completed implementation,
returned useful partial work, became blocked on a question, or produced an
analysis/report that requires no product-tree change.

The worker reports what happened. The reviewing/architectural authority decides
what the ticket should do next.

## Context

The current typed worker boundary already distinguishes:

- `completed`: the worker believes the assigned work is complete;
- `incomplete`: useful work was done but work remains;
- `blocked`: safe progress needs external information or action.

`WorkerClaim` also already carries `summary`, `remaining`, and `questions`,
and ExecutionReport persists those fields in the control plane.

Today, however, only `completed` moves a ticket from `todo` to `review`.
`incomplete` and `blocked` leave the ticket in `todo`. That makes worker
outcome semantics leak into workflow ownership and makes question/report tasks
awkward: the worker can finish its attempt successfully yet the ticket does not
enter the human/architectural decision boundary.

A previous TASK-010 attempted to exercise a read-only coverage audit with zero
tracked product delta. That task cannot be represented cleanly until the workflow
can review and complete non-implementation handoffs. This ticket replaces that
coverage-audit task entirely.

The intended model is:

```text
todo
  |
  | valid worker semantic handoff
  v
review
  |
  +-- implementation accepted for product integration --> accepted --> done
  |
  +-- report/analysis or other no-integration result accepted ----------> done
  |
  +-- question answered / more work required ---------------------------> todo
```

There is no new kanban state for blocked work or architect attention.

## Worker handoff semantics

A worker attempt that exits through a valid typed `devlegate_report` has
successfully handed something back to the control plane.

Therefore all valid semantic outcomes:

- `completed`;
- `incomplete`;
- `blocked`;

must persist their ExecutionReport and move the SAME ticket from `todo` to
`review`.

The outcome remains evidence about the worker's belief; it does not itself choose
the next workflow state after review.

By contrast, a process crash, non-zero process failure, transport failure,
missing/duplicate/malformed typed egress, or otherwise untrustworthy worker result
is an execution failure. Such failures must continue through failed-execution /
retry/recovery semantics and MUST NOT be promoted to `review`.

## Review decisions

Review becomes the higher-authority decision boundary for every valid worker
handoff.

### Review -> accepted

Use `accepted` only when there is an implementation checkpoint that passed review
and is intended to be integrated into product history.

`accepted` keeps its existing narrow meaning: permission for Devlegate-owned
product integration. It does not mean merely "the reviewer liked the result."

### Review -> done

A reviewer/architect may move the SAME ticket directly from `review` to `done`
when the ticket's goal has been satisfied and there is no product checkpoint that
needs integration.

Examples include:

- a research or investigation report;
- an analysis-only task;
- a diagnostic result;
- a question whose answer makes further implementation unnecessary;
- another zero-product-delta task whose durable evidence satisfies the ticket.

Direct `review -> done` MUST NOT be used to bypass integration for an
implementation checkpoint that changes product history.

### Review -> todo

If more work is needed, including when the worker returned `incomplete` or
`blocked`, the reviewer/architect moves the SAME ticket from `review` back to
`todo`.

When resolving worker questions, the architectural decision or missing context is
recorded in the canonical ticket specification (or other canonical project context
when appropriate) before returning it to `todo`. The original execution report
and questions remain immutable historical evidence.

Ordinary hardening continues to reuse the same ticket identity rather than creating
replacement fix tickets.

## Durable long-form reports

Some successful worker handoffs produce a report rather than a product patch.
Those results must be reviewable from durable control-plane evidence and must not
exist only in the ephemeral/runtime execution log.

Extend the typed worker egress with an optional long-form human-readable report
payload suitable for analysis/research output.

Devlegate, not the worker, owns persistence into the control branch.

A suitable durable shape is:

```text
executions/<ticket-id>/<execution-id>.json
executions/<ticket-id>/<execution-id>.md    # optional long-form report
```

Exact schema details may differ if a cleaner equivalent is found, but the following
invariants are required:

- the worker cannot write or commit control-plane files directly;
- long-form report content crosses the typed egress boundary;
- Devlegate persists it under the exact execution identity;
- ExecutionReport contains enough immutable reference/integrity information to bind
  any report artifact to that execution;
- report persistence and lifecycle transition are replay/recovery safe and cannot
  silently overwrite existing evidence;
- absence of a long-form report remains valid for ordinary implementation attempts
  and question-only blocked results.

`summary` remains the concise semantic result defined by TASK-002. A long report
must not be stuffed into `summary`.

`questions` remains structured unresolved questions requiring external
information or a decision.

`remaining` remains structured concrete work that still remains.

## Zero-product-delta handoffs

A valid semantic worker handoff may have no tracked product-tree delta.

For such an execution:

- Devlegate MUST NOT manufacture a product commit merely to satisfy checkpoint
  machinery;
- the semantic result and any optional report/questions remain durable in control
  history;
- the ticket still moves to `review`;
- review may move it directly to `done` if the ticket goal is satisfied;
- review may return it to `todo` if more work or an answered question remains;
- `accepted` is not required when there is no product change to integrate.

Any execution/base identity retained for provenance must remain exact and
recovery-safe, but zero delta is not itself an execution failure.

## Role protocol

Update the packaged Reviewer/Architect guidance so the documented protocol matches
the state machine.

Reviewer guidance must no longer define `review` only as code/checkpoint review.

For a review ticket, the higher-authority role must inspect as applicable:

- the canonical ticket;
- ExecutionReport and WorkerClaim outcome;
- `summary`, `remaining`, and `questions`;
- any durable long-form execution report;
- the implementation checkpoint/diff when product changes exist;
- relevant tests, source, and project context.

The role then chooses `accepted`, `done`, or `todo` according to the
semantics above.

## Required behavior

- Every valid `completed`, `incomplete`, or `blocked` worker claim persists
  durable execution evidence and moves `todo -> review`.
- Worker outcome does not automatically choose `accepted`, `done`, or a retry.
- Failed/untrustworthy worker execution does not enter review.
- `questions` and `remaining` survive unchanged in durable execution evidence.
- Optional long-form worker reports can be persisted as Devlegate-owned
  control-plane evidence bound to the execution ID.
- Review can resolve to:
  - `accepted` for product integration;
  - `done` when no product integration is required;
  - `todo` when more worker work is required.
- Only `done` satisfies dependencies, preserving the current DAG semantics.
- The existing serial review/accepted barrier remains deterministic.
- Recovery and replay derive correctness from canonical state/evidence, not commit
  prose or worker free-form output.

## Acceptance criteria

- A valid `completed` claim with product changes enters `review` as today and can
  follow `review -> accepted -> done`.
- A valid `incomplete` claim enters `review` with its remaining work preserved.
- A valid `blocked` claim enters `review` with its questions preserved.
- After an architect/reviewer answers a blocked question in the canonical ticket and
  returns the same ticket to `todo`, a later worker execution can continue that
  ticket normally.
- A valid zero-product-delta report/analysis execution enters `review` without a
  manufactured product commit.
- If that report satisfies the ticket, the same ticket can move
  `review -> done` without passing through `accepted`.
- If a product-changing implementation exists, direct `review -> done` is
  prohibited by the role contract; it must pass through `accepted`.
- Optional long-form report evidence is durable in the control branch and exactly
  bound to one execution.
- A crash, process failure, transport failure, or invalid/missing
  `devlegate_report` remains a failed execution and does not enter `review`.
- Full tests, lint, recovery tests, and control-plane exactness checks remain green.

## Required regressions

- Given each of the three valid worker outcomes, when lifecycle evidence is applied,
  then the ticket moves from `todo` to `review` exactly once and the
  ExecutionReport preserves the claim verbatim.
- Given a blocked claim with questions, when the lifecycle is replayed after restart
  or compatible control drift, then the same durable questions and review state are
  recovered without duplicate evidence.
- Given an incomplete claim with a partial implementation checkpoint, when review
  returns the same ticket to `todo`, then a later execution can continue without
  losing the prior checkpoint/evidence or inventing a replacement ticket.
- Given a valid zero-delta completed report, when lifecycle is applied, then no
  product commit is manufactured and the ticket enters `review`.
- Given an optional long-form report, when the lifecycle commit is created/replayed,
  then the report artifact and ExecutionReport binding are exact, immutable, and
  non-overwriting.
- Given a report-only review moved directly to `done`, then dependencies waiting on
  that ticket become eligible exactly as for any other done ticket.
- Given a product-changing reviewed checkpoint, the reviewer protocol does not allow
  direct `done`; normal `accepted` integration remains required.
- Given invalid or failed worker egress, the ticket remains outside `review` and
  existing failed-execution/retry behavior is preserved.

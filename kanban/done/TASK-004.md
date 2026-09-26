---
"type": "devlegate.ticket"
"title": "Publish coherent live status during active executions"
---

## Milestone

Devlegate 0.5.5 self-hosting observability hardening.

## Goal

Keep the operator-facing `devlegate status` view current and internally coherent
while the service is processing and running a ticket.

## Context

During the first self-hosted execution, the service log had already observed control
advance from `c81c4d...` to `df88cdf...` and had started `TASK-001`, while
`devlegate status` still displayed:

- control HEAD `c81c4d...`;
- an empty workflow;
- no current execution.

The hosted service keeps both a lightweight `ServiceSnapshot` and a published
`StatusSnapshot`. Scheduler/execution transitions update the former, while IPC
`status` serves the latter, which can remain stale for the entire active execution.

The existing design goal that IPC readers consume owner-published immutable state is
valuable. Do not fix this by allowing IPC request threads to perform ad-hoc Git or
workflow observation.

The first implementation reached checkpoint
`d34de9cc51fa7e5735ab6b0ad45ee5176736a26d` and produced a valid worker claim,
but review found that both exact-head CI runs `36263067818` and `36263068173`
failed in the full test-with-coverage step. The worker's completed claim therefore
does not satisfy the exact-head acceptance boundary.

Keep the useful owner-side publication work and harden the same ticket. Determine
the exact failing regressions on the checkpoint, fix them without weakening snapshot
coherence or fail-closed behavior, and prove the resulting exact head with the full
CI suite. Do not dismiss the failures as unrelated without evidence.

## Required behavior

- The service owner publishes a coherent operator status after material workflow and
  execution transitions, including control synchronization, ticket admission,
  worker launch/running, finalization, and lifecycle completion.
- During an active execution, `status` identifies the current ticket and execution
  and reflects the current owner-observed control/product identities and workflow
  counts.
- The text status exposes the active execution ID or an unambiguous useful prefix so
  an operator can use `devlegate logs execution` without mining the service journal.
- Published live-execution evidence and the corresponding StatusSnapshot refer to
  the same execution/stage rather than mixing generations.
- IPC status remains read-only over immutable owner-published state and does not
  acquire repository ownership or perform live Git I/O from the request thread.
- Snapshot publication must preserve fail-closed behavior when a coherent view
  cannot be proven.
- Exact-head full-suite failures introduced or exposed by this implementation must
  be understood and corrected before acceptance.

## Acceptance criteria

- After a remote control update admitting a ticket, status no longer shows the
  previous control HEAD/workflow generation.
- While a worker is running, status shows the current ticket, active execution
  identity, and an appropriate running/preparing/finalizing state.
- Repository/workflow information and live execution information in one response are
  generation-consistent.
- Status remains usable through worker completion and lifecycle transition without
  requiring a service restart.
- Exact-head CI for the resulting checkpoint is green, including the full
  test-with-coverage step and lint.
- Full tests and lint remain green.

## Required regressions

- Given a published idle status, when the owner fetches a new control generation and
  admits a ticket, then a subsequent IPC status response reflects that control
  generation and ticket rather than the previous snapshot.
- Given an owned worker-running execution, when status is requested, then the
  response/text identifies the same ticket/execution/stage as owner live evidence.
- Given transition from worker-running to finalization/lifecycle, when status is
  requested at each durable stage, then no response regresses to the pre-admission
  workflow snapshot.
- Given an IPC status request, then the IPC thread does not perform repository
  mutation or owner-only observation I/O.
- Given the exact full CI suite, the failures from runs `36263067818` and
  `36263068173` do not recur.

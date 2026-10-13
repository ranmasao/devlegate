---
"type": "devlegate.ticket"
"title": "Keep hosted service alive when a recoverable execution workspace cannot be prepared"
"depends_on": ["TASK-055", "TASK-058"]
---

## Milestone

Devlegate 0.5.6 — hosted runtime error-handling hardening before release.

## Observed failure — 2026-10-09

After TASK-058 integration, TASK-054 returned from backlog to todo with
a changed specification and an old published execution branch. The product
was at `e10eea1572dfc3632de059f8cf1c39e7cf9898f4`. Hosted
Devlegate on BG0055-DSLW6 logged:

```text
control updated: 75ce18592227 -> b796292209da
devlegate: execution branch devlegate/work/TASK-054 is unrelated to
planned code revision e10eea1572dfc3632de059f8cf1c39e7cf9898f4
systemd: Main process exited, code=exited, status=1/FAILURE
```

Systemd unit policy is `Restart=no`; `ActiveState=failed`.
The CLI caught the terminal `DevlegateError` and returned exit 1; this
was not an unhandled Python traceback, but it was a hosted-service
availability failure from an expected workflow conflict.

Persisted runtime state observed immediately afterward:

```text
phase: agent_pending
execution_stage: lifecycle
execution_ticket_id: TASK-054
execution_id: 950844eff83b489e93a2f6ed4ad14928
execution_base_head: e10eea1572dfc3632de059f8cf1c39e7cf9898f4
execution_start_head: 40b44f744f6439a0399d611ada4c30c43afc19fd
execution_remote_head: 408541fc45bd37a1b9b58f5a9f8f43f2f137d4de
execution_branch: devlegate/work/TASK-054
```

This task concerns **service error-handling semantics only**. Separate
investigation will determine how to safely retire/recover that historical
lineage and whether stale persisted `execution_start_head` is an additional
state-binding defect. Do not reset/rewrite refs or infer a new base as part
of a generic exception handler.

## Root-cause hypothesis to verify

`ExecutionWorkspaceManager.prepare()` rejects an execution branch not
descended from the planned base through `ExecutionWorkspaceError`.
`ServiceEngine._prepare_execution_workspace()` propagates it as
`DevlegateError`. In `ServiceEngine._run_polling()`, a hosted iteration
keeps running for `WorkflowBlockedError` and certain known
`DevlegateError` reconciliation/recovery states, but may re-raise when
a pending workspace is classified `RECOVERABLE` (rather than `UNSAFE`).
This causes the whole service process to exit with status 1.

Reproduce the exact exception path and classify workspace inspections in
a regression before changing behavior. Do not silently catch every
`DevlegateError` as a recoverable workflow failure.

## Required implementation

1. Define explicit fail-closed **ticket/workflow blocked** semantics for
   expected workspace-preparation conflicts, including recoverable old
   generation and operator-recovery-required cases.
2. Keep the hosted service, IPC endpoints, operator status/plan visibility,
   lifecycle controls and recovery commands available while work is blocked.
   A malformed/unsafe execution must **not** spawn a worker or mutate
   branches as a side effect of exception suppression.
3. Distinguish deterministic operator-actionable workflow conflicts from
   genuinely fatal infrastructure/state/ownership failures; retain a
   visible diagnostic and exact blocked reason with deduplicated logs, not
   a busy retry loop.
4. In single-iteration/foreground commands, preserve meaningful nonzero
   exit status. In hosted mode, availability must survive expected blocked
   workflow state without falsely reporting success.
5. Do not conflate `RECOVERABLE` with `SAFE_TO_RESUME`: a classified
   recovery candidate still requires valid exact evidence, generations and
   any required operator authority.
6. Respect owner serialization, durable state, worker isolation and
   systemd `Restart=no`. No blanket restart policy, erased evidence,
   force-reset, forced ref rewriting or overlapping worker execution.
7. Keep this fix narrow: the separate TASK-054 lineage recovery incident
   and TASK-061 recovery-domain extraction are not substitutes for
   correctly handled hosted failures.

## Required regressions

- Reproduce the TASK-054-style old-branch versus new-base conflict and
  verify hosted owner stays alive with blocked/status/IPC available.
- Cover both `RECOVERABLE` and `UNSAFE` workspace inspection outcomes,
  with no spurious worker, deletion, ref reset or branch publication.
- A blocked workflow does not spin, repeatedly log identical errors, or
  prevent legitimate operator command admission.
- Retain nonzero foreground/one-shot errors while hosted mode stays alive.
- Truly fatal state/authority corruption still fails closed, not masked
  as an ordinary recoverable condition.
- Control/product revision changes and stop/restart requests during
  blocked state retain their existing concurrency and ownership guards.

## Acceptance criteria

- The exact regression that produced systemd `status=1/FAILURE` now
  leaves the hosted service available in an honest blocked state.
- Evidence shows expected recovery failures remain non-destructive and
  operator-accessible; fatal failures are not silently swallowed.
- Both service and one-shot behavior are tested, including failure paths.
- Full authoritative CI with tests, coverage, licensing and Ruff is green
  on the exact reviewed checkpoint.

## Non-goals

Do not repair TASK-054's old Git lineage, infer a historical checkpoint
retirement policy, move all recovery code out of runtime.py, introduce a
watchdog, or alter systemd restart policy. TASK-061 owns the larger
decomposition in 0.5.7.

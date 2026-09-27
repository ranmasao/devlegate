---
"type": "devlegate.ticket"
"title": "Fail closed on invalid bound workspace recovery"
---

## Milestone

Devlegate 0.5.5 self-hosting recovery hardening.

## Goal

Make persisted bound-execution planning and operator recovery fail closed when an
`agent_pending` execution workspace can no longer be proven to descend from its
planned product revision.

A read-only plan must never claim that a bound execution can be resumed when the
same persisted execution state will fail workspace validation at service startup.

## Context

Dogfooding in rslab2 exposed a contradictory state for ticket `LAB-153`.

Observed product/control state was clean and synchronized:

```text
Code: master @ 95215689f5db
Control: devlegate/control @ 073b5ecaa0ad
```

Persisted execution state was:

```text
phase=agent_pending
stage=lifecycle
execution=a97c6db5f41e4c349171d756b67ad26a
bound_ticket=LAB-153
```

The service failed during runtime recovery with:

```text
execution branch devlegate/work/LAB-153 is unrelated to planned code revision
95215689f5db9e8e7e3e1189bfc2c7b2435bed1f
```

`devlegate status` correctly surfaced:

```text
Current: LAB-153  •  recovery-required
```

but `devlegate plan` simultaneously reported:

```text
Plan: run-worker · resume persisted bound execution
Bound: yes
```

and YAML status contained:

```yaml
plan:
  action: run-worker
  reason: resume persisted bound execution
  bound: true
```

There was also no ordinary recovery candidate:

```yaml
failed_executions: []
reconciliation: null
```

Current implementation explains the mismatch:

- execution-workspace reuse validates that the planned base is an ancestor of the
  execution branch and fails closed when it is not;
- bound `agent_pending` planning checks ticket identity/state, but does not prove
  the persisted workspace/branch lineage before returning `run-worker`;
- ordinary interrupted retry candidates require `agent_running`;
- current drop candidates require `agent_running` at lifecycle with a durable
  pending ExecutionReport.

Thus an invalid pre-worker binding can produce an impossible resume plan and an
operator recovery dead-end.

## Required behavior

- A bound `agent_pending` plan may return `run-worker` only when the exact
  persisted execution workspace binding required for resume is provably reusable.
- The proof must include the same material lineage/identity invariants used by actual
  workspace recovery, including the exact planned product base and execution branch
  ancestry.
- Planning and execution must not maintain divergent definitions of "resumable".
  Prefer one shared read-only validation boundary or equivalent common semantics.
- If the persisted workspace/branch is missing, unrelated, registered to the wrong
  branch/path, dirty in a way recovery forbids, or otherwise cannot be proven safe,
  `plan` returns a blocked/recovery action rather than `run-worker`.
- The blocked plan reason identifies the concrete failed invariant when it is safe to
  do so; for the reproduced case it must not say `resume persisted bound execution`.
- `status` and `plan` remain semantically coherent for the same persisted
  generation: a `recovery-required` execution cannot simultaneously advertise an
  executable bound resume unless live/current evidence proves that resume.
- Provide a supported explicit operator recovery path for this pre-worker
  `agent_pending` failure class without requiring manual SQLite/state-file edits.
- That recovery path must be identity-bound and fail closed. It must not guess that an
  unrelated branch/worktree belongs to the current execution, silently delete
  unproven work, or rewrite product/control history.
- It is acceptable to extend an existing operator concept such as retry/drop, or to
  introduce a narrowly scoped recovery operation, provided the semantics are explicit
  and proven. Do not choose an operation solely to reuse its name.
- The recovery path must remain usable when ordinary service startup cannot complete
  because this exact persisted workspace validation fails. This may be achieved by
  keeping the service alive in a blocked state or by a safe offline operator path.
- After explicit recovery, Devlegate can either safely resume the same exact bound
  execution if its binding becomes provable, or retire/replace that execution
  according to explicit operator intent and admit a fresh execution. It must never
  launch a worker against an unproven workspace.
- Existing valid `agent_pending` restart/resume behavior remains unchanged.

## Acceptance criteria

- Reproducing an `agent_pending` execution whose
  `devlegate/work/<ticket>` branch is not descended from its persisted
  `execution_base_head` no longer yields `plan.action=run-worker`.
- The plan is blocked/recovery-required with a reason consistent with the workspace
  lineage failure.
- Starting the service does not create a worker, checkpoint, branch mutation,
  lifecycle transition, or ticket transition while the binding is invalid.
- The operator has one documented supported CLI recovery route for the invalid
  pre-worker binding.
- Recovery refuses destructive action if the observed branch/worktree identity has
  changed since authorization or cannot be proven to match the operator-selected
  object.
- A valid persisted `agent_pending` workspace still resumes exactly once and keeps
  the same execution identity.
- Status text, status YAML/JSON, and plan text/YAML/JSON agree on whether the bound
  execution is resumable.
- Full tests, lint, and relevant recovery/subprocess tests remain green.

## Required regressions

- Given persisted `agent_pending` state with a valid execution branch descended
  from the exact planned base and matching registered worktree, plan reports the
  existing bound resume and restart launches at most one worker.
- Given the same state but an execution branch unrelated to the exact planned base,
  plan reports blocked/recovery rather than `run-worker`, and no worker is launched.
- Given a missing or wrong registered execution worktree/branch for the persisted
  binding, plan fails closed with the same semantic result as actual recovery.
- Given an invalid bound workspace and a stopped service after startup recovery
  failure, the supported operator recovery command remains available and performs
  only its explicitly authorized identity-bound effect.
- Given branch/worktree drift between recovery inspection and effect, the recovery
  operation refuses the mutation.
- Given an unrelated branch containing commits, no automatic cleanup deletes or
  rewrites it merely because its conventional branch name matches the ticket.
- Given status and plan produced from one stable persisted generation, they cannot
  disagree as `recovery-required` versus executable bound resume.

---
"type": "devlegate.ticket"
"title": "End-to-end operator reset validation and fresh deferred-ticket execution"
"depends_on": ["TASK-131", "TASK-162"]
---

## Goal

Complete and validate the **fresh-execution reset** lifecycle on the exact integrated TASK-160, TASK-130, TASK-131, TASK-161 and TASK-162 product baseline. Reset is not a synonym for garbage collection: it deliberately ends an unsuccessful execution lineage so that **the same original ticket** can be attempted again from current integrated product HEAD, with a fresh execution ID, generation, worktree and newly constructed worker prompt. Prior unsuccessful checkpoints, conversation, rework instructions and accumulated code must **not** be carried into the new worker context. Immutable reports/reviews and audit history remain preserved.

Use the final public command:

```sh
devlegate inspect workspace TASK-XXX --json
devlegate reset TASK-XXX --plan-digest <digest> \
  --generation <generation> --request-id <operator-id> --json
```

`inspect` is universally read-only and does not authorize reset. `reset` is the single high-level operator transaction that revalidates, authorizes, retires the precise prior resources, closes the old lineage and safely requeues the unchanged ticket for fresh execution. Do **not** introduce `reset inspect`, `gc`, or conflate `workspace prune` with reset. Replace/remove legacy `devlegate clean apply` and any remaining `clean` public CLI surface; no long-lived aliases/migrations.

## Required behavior

1. Build on the integrated owner-serialized digest/generation confirmation from TASK-130 and CAS/lease-guarded staged retirement/restart replay from TASK-131. Preserve their WAL, independent ResourceBinding, ownership, exact preview reobservation and unknown-outcome fail-closed boundaries. A status of `authorized-pre-effect` is **not** a completed reset.
2. Allow normal fresh-execution reset only for an explicitly **backlogged** ticket with an independently provable current resource generation and no live worker/pending authority. Do not offer reset as an ordinary operation on `done`, `accepted`, `review`, `todo` or archived/abandoned tickets; normal done-ticket resource disposal belongs to `workspace prune`. Reject historical unbound generations (e.g., old TASK-129) without guessing/adoption.
3. After a fully proved retirement, durably finalize the reset disposition and requeue the **same immutable ticket blob** from backlog to todo using exact control-lineage/CAS guards. Keep immutable past reports, reviews and resource evidence as historical audit, but ensure scheduler constructs an entirely new generation/worker prompt from the current product HEAD, current model/agent configuration and the ticket specification. Do not implicitly inherit old checkpoint, worker conversation, rework notes or old generation identity. No false fresh admission before completed retirement/disposition.
4. Publish accurate operator-facing completion and recovery semantics. A request may acknowledge authorization/in-progress, but must only claim `completed` after all required stages and durable ticket transition; a crash/timeout must safely replay by the **same request ID** without widening the approved scope. Explain intervention required if an effect is uncertain. No reset auto-start of a live worker outside normal scheduler ownership.
5. Prove the full workflow on disposable independently generation-bound deferred-ticket fixtures: read-only inspect -> explicit confirmation -> WAL intent -> staged worktree/local+remote ref retirement -> interrupted restart/replay -> backlog-to-todo -> new execution ID/base/generation/worktree/prompt. Cover stale preview, changing Git refs, remote lease races, nested submodules, active owner, duplicate requests, no-op/partial stages, and no automatic non-done prune.
6. Validate public CLI consistency with `devlegate inspect {ticket,execution,workspace,service}`, `devlegate workspace {ls,prune}`, and `devlegate reset`; document operator procedure, recovery, explicit fresh-model scenario, and practical effect costs. Run focused real-Git tests, configured CI and Ruff.

## Boundaries

No compatibility/migration scaffolding, no generic VCS abstraction, no TASK-073-specific production branches and **no effects on actual TASK-073, TASK-126, historical TASK-129 or TASK-131 operator resources**. Preserve reviews/reports/pinned evidence and TASK-073 human-only acceptance. Do not self-accept TASK-126. Any scope conflicts with the completed TASK-131 implementation must be addressed as a new reviewed change, not by editing its historical execution.

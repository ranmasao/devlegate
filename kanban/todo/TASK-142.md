---
"type": "devlegate.ticket"
"title": "Extract source-bound update-base rewrite transactions"
"depends_on": ["TASK-141"]
---

## Milestone

TASK-139 historical repair decomposition: 3/5 — reusable existing Git rewrite without behavior change.

## Goal

Factor the existing `reconciliation_transactions` active-only update-base implementation so one proven, source-bound admission can drive its lineage/rebase/replay machinery, while retaining the existing active mode's semantics and tests. Historical mutation will be enabled by TASK-143, not this ticket.

## Context

In the product, `admit_update_base()` consumes `owner.reconciliation_pending()`; `start_fresh_rewrite()` and replay helpers depend on `_execution_workspace_for_recovery()` reading live execution state; `_resolve()` writes active `resume_required`. TASK-141 supplies an independent historical intent but must not be impersonated as an active execution.

## Required behavior

1. Extract an explicit, typed source-bound admission/workspace context from proven inputs: exact ticket, immutable original base/checkpoint, observed worktree/branch/registration, target and original ref/remote identities, expected commit count, evidence refs and authorizations. Avoid a general Git abstraction.
2. Make the current `run_update_base()` path an **active adapter** that still requires valid active pending reconciliation, preserves `_execution_workspace_for_recovery()` ownership validation, stage order, ref CAS, published lease, `rebase --onto`, commit-count/linear proofs and existing failure behavior.
3. Factor shared start/recover/publish mechanics so a *future historical adapter* can pass its separately proven source without populating active runtime `execution_*` fields. Separate resolution callbacks/effects: active success retains `resume_required`; historical success must be able to record a distinct completion in TASK-143.
4. No historical Git mutation, successor admission, or operator request expansion in this task. Preserve current `reconcile update-base` operator interface. Do not relax already implemented fail-closed rewrite/replay checks, including recorded commit count where present.
5. Module responsibilities and ownership must remain clear: ServiceEngine performs authoritative Git and state effects under owner serialization; transaction module sequences proofs/stages.

## Acceptance criteria

- Existing active update-base and its interrupted `local-pending`/`remote-pending` recovery behavior pass unchanged in disposable real-Git tests.
- A small isolated test demonstrates the extracted core accepts a separately constructed immutable source/workspace capability without requiring fabricated active state, **without actually admitting a historical repair**.
- No duplicated parallel rebase or ref-rewrite implementation, no new active-mode safety bypass or state migration.

## Required regressions

Positive active rebase with nonempty multi-commit lineage, remote rewrite authorization, local/remote interruption replay, rebase conflict, changed ref/dirty workspace, commit count mismatch, no-op semantics where supported. Add focused source-context API contract test. Run Ruff and configured CI.

## Boundaries

Do not modify real TASK-134 resources or history; do not modify old reports; no new standalone rewrite subsystem, generic VCS abstraction, reset or compatibility layer.

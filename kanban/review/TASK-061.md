---
"type": "devlegate.ticket"
"title": "Complete update-base reconciliation extraction with explicit WAL-state recovery"
"depends_on": ["TASK-057"]
---

## Current review decision — REQUEST CHANGES

Continue the existing TASK-061 worktree/checkpoint lineage; do not start a redesign or repeat completed recovery extractions. Reviewed execution `e92f1073220441a08f27cf0ccfece05c`, checkpoint `984609187924aff28df0c0f5eb8c5c8db1f2393a`. Its worker outcome was **incomplete**.

[Exact-checkpoint CI #38611841364](https://github.com/ranmasao/devlegate/actions/runs/38611841364): **failure — 19 failed, 1188 passed, 1 skipped**. Most failures originate in `ReconciliationTransactionError: reconciliation owner does not provide update-base effect phases`, because a new mandatory primitive protocol was introduced without wiring the real ServiceEngine. Other failures are associated expected-error assertions and service timeouts. **Fix this regression as part of the substantive extraction**, not as a stand-alone wrapper-only iteration.

## Objective

Finish the **product-lineage update-base transaction** extraction from `src/devlegate/runtime.py` while preserving exact existing behavior: single ServiceEngine mutation/lock authority, durable WAL, crash/restart and operator-stop semantics from TASK-057, fresh admission/TOCTOU checks, Git expected-head lease/CAS, immutable displaced/rewritten evidence, and fail-closed recovery. The extracted `reconciliation_transactions.py` must contain meaningful control flow, not call back into a full legacy aggregate method.

**Prefer simple stage functions and persisted-state dispatch over architecture-by-Protocol.**

## Already accomplished — preserve, do not redo

- `recovery.py`: classification, interruption/binding semantics.
- `recovery_transactions.py`: stranded recovery/adoption, owner-held guarded actions.
- `recovery_workspace.py`: pre-worker workspace recovery and ownership/provenance checks.
- `recovery_workflow.py`: checkpoint/publication, lifecycle report, accepted-integration replay. Obsolete delegated legacy bodies already removed from `runtime.py`.
- `reconciliation.py`: linear-lineage proof, authorization policy; `reconciliation_transactions.py`: admission, lock-aware entry, evidence-before-publish support and rewrite proof helpers.
- Existing tests of these seams, including pin-before-publish and failure prevents publication, should remain.
- The latest checkpoint added `UpdateBasePrimitiveCapabilities` and `execute_update_base()` (`recover -> prepare -> apply -> publish -> finish`) plus mock ordering tests. **These are an unconnected draft, not an approved architecture.** Reuse useful checks if warranted; remove or reshape the forced linear sequencer and redundant Protocol freely.

## Precise implementation: dispatch by durable WAL state

The active `ServiceEngine._reconciliation_apply_update_base_primitives()` still contains ~414 lines of actual reconciliation/rewrite/recovery logic. Physically extract its control flow and eliminate the aggregate implementation. The method must not survive merely renamed as a callback.

### 1. A single serialized entrypoint and explicit outcomes

Under the **existing** ServiceEngine lock, obtain the persisted reconciliation record, workspace/provenance and current observations. Dispatch by **persisted `rewrite_stage`**, approximately:

```python
def run_update_base(owner, request):
    with owner.reconciliation_lock():
        state = owner.read_reconciliation()
        if state.rewrite_stage == "local-pending":
            return recover_local_rewrite(owner, request, state)
        if state.rewrite_stage == "remote-pending":
            return recover_remote_rewrite(owner, request, state)
        if state.rewrite_stage is not None:
            raise ReconciliationTransactionError("unknown rewrite stage")
        return start_update_base(owner, request)
```

**Illustration only.** Preserve existing admission and locking behavior and the actual outcomes of recovery. In particular:
- A recovered `remote-pending` that proves publication **resolves directly**: never falls unconditionally through to prepare/rebase/publish.
- A safely aborted `local-pending` may resume or require retry according to the current logic.
- A disagreement in HEAD, provenance or evidence fails closed, not coerced into a fresh operation.
- Auth for an existing persisted operation may be reused only under its existing durable identity; a new rewrite never inherits stale `rewrite_authorized`.

A result such as `RESOLVED`, `ABORTED_CONTINUE`, `BLOCKED` or a small explicit typed outcome is fine if it makes branches readable. Do not introduce a generic transaction framework.

### 2. Extract recovery branches first

**`recover_local_rewrite`:** handle `local-pending` rebase interruption; inspect dirty worktree and abort only the known rebase; prove rewritten lineage/count before resetting an already-changed local HEAD; restore original checkpoint/base only when verified; clear only the correct WAL fields; preserve operator retry/continuation contract.

**`recover_remote_rewrite`:** handle `remote-pending`; compare observed execution remote to both persisted expected old HEAD and rewritten checkpoint. If never published, prove and safely restore local state; if already published, validate both original displaced evidence and rewritten evidence, exact linear chain to expected tip, and persist resolved state **without issuing another publication**. Unknown remote or mismatched evidence must fail closed.

Do not merge these two branches merely because both are called “recovery.”

### 3. Extract the fresh update-base path as successive guarded steps

1. **Admission/proof:** validate target/product and control generation, execution binding, original lineage, workspace and remote predecessor, and explicit authorization of a published rewrite.
2. **Intent:** repeat the full read-only admission immediately before durable `local-pending` WAL (intentional TOCTOU barrier); preserve displaced checkpoint evidence before irreversible local changes.
3. **Local mutation:** perform existing rebase under sole owner; verify clean worktree, checkpoint count, exact target-to-new-tip linear chain; use existing safe rollback on error.
4. **Publication boundary:** persist `remote-pending` at the existing safe point; pin rewritten evidence **before** any remote push; publish only with expected remote/lease CAS; abort/fail closed on conflicting movement; do not erase recovery evidence.
5. **Resolution:** only after proof of publication (or properly handled unpublished path), persist the resolved reconciliation record, base/head identities, fingerprint and `resume_required`.

These are *logical* stages; use private module functions rather than requiring five matching Protocol callbacks. Choose narrow owner-held operations only where real Git, WAL and service-owned state mutation need them. Never put the entire former 414-line transaction inside a single owner method.

### 4. KISS/DRY constraints

- Do not add more protocol/wrapper layers unless an actual call boundary requires one. Internal private functions with direct, typed inputs are preferable.
- Use common proof/evidence/reset/resolution helpers only where invariants and preconditions are truly identical.
- **Keep** repeated git observations and fresh admission at mutation boundaries; they are not accidental DRY violations.
- One ServiceEngine owns the lock and every persistent mutation; extracted code specifies sequence, not competing authority.
- Remove unused compatibility seams, old aggregate implementation, redundant mock-only sequencer scaffolding. No new generalized state-machine engine, framework, or speculative extension points.
- Keep unchanged CLI/output/error and operational behavior, especially side-effect authorization and TASK-057 interruption versus continuation.

## Tests required for this implementation

Test the **real wired engine path**, not just fake-owner event sequences:
- Existing full tests must recover from current 19 failures, including `test_engine_reconciliation_and_update_base_resumes`, `test_update_base_transplants_multiple_unpublished_checkpoints`, cross-layer `local-pending` replay, published rewrite and actual service restart/IPC.
- `local-pending` crash before/after rebase: correct rollback, refusal on dirty/unproven history, permitted restart.
- `remote-pending` crash before/after remote effect: publication proof, no duplicate publish, expected-head conflict fails closed.
- Original/rewritten evidence refs survive replay; evidence pin failure prevents mutation; target/branch/head conflicts and published rewrite without authorization rejected.
- Retain all existing regression tests, run full authoritative pytest/coverage and Ruff on the **same next checkpoint SHA**; verify exact GitHub Actions conclusion before requesting acceptance.

## Deferred, not part of TASK-061 execution

`_run_iteration_body()` (~1173 lines in reviewed baseline) deserves a **separate KISS/DRY decomposition**, but **do not work on it now**. Future stage boundaries: pending recovery dispatch; product sync/`merge_pending`; planning/authorization; accepted integration; ticket admission/retry; worker launch/retirement; checkpoint/report WAL; product drift/reconciliation; publication/lifecycle. Preserve control-flow order, early returns and all durable stages when separately scheduled. Do not create the follow-up ticket without an operator request.

Do not modify TASK-066, previously published v0.5.6, unrelated CLI/logging or architecture files.

## Acceptance criteria

1. The actual update-base/recovery sequence resides in extracted reconciliation code with explicit dispatch on persisted WAL state; `remote-pending` resolution and `local-pending` recovery do not automatically flow into a new rewrite.
2. The old aggregate `_reconciliation_apply_update_base_primitives()` is deleted, not renamed; `ServiceEngine` exposes only focused owner-held observations/mutations and short entrypoints.
3. Single mutation authority, fresh TOCTOU validation, immutable evidence, lease/CAS, rollback, restart and interruption/continuation invariants demonstrably preserved.
4. Full real-engine and restart regression suite plus Ruff green on the authoritative exact checkpoint.
5. Worker reports precisely what it accomplished and **only true remaining blockers**. No review-history appendices, further abstraction-only iterations, or rediscovery of already documented architecture.

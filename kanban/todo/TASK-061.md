---
"type": "devlegate.ticket"
"title": "Finish staged recovery/reconciliation extraction with KISS and DRY"
"depends_on": ["TASK-057"]
---

## Objective and exact working baseline

Devlegate 0.5.7. Continue TASK-061 from the existing execution worktree/checkpoint chain; do not start a fresh design or reimplement completed work. **Latest execution:** `e92f1073220441a08f27cf0ccfece05c`; **checkpoint:** `984609187924aff28df0c0f5eb8c5c8db1f2393a`. The worker explicitly marked that checkpoint `incomplete`.

Goal: finish the **remaining update-base reconciliation transaction** extraction while keeping `ServiceEngine` the sole serialized owner of state, worker admission, Git ref mutations and durable receipts. Prefer short, intelligible stage functions over more protocols or dispatch layers.

## Already implemented — preserve, do not redo

- `recovery.py`: interruption/stage/stranded classifiers and binding validation.
- `recovery_transactions.py`: stranded recovery and control-lineage adoption with renewed admission checks before evidence pin and worktree mutation.
- `recovery_workspace.py`: pre-worker workspace inspection/repair, provenance and Git CAS checks.
- `recovery_workflow.py`: checkpoint/publication, lifecycle report and accepted integration replay.
- `reconciliation.py`: lineage proof helpers; `reconciliation_transactions.py`: update-base admission, lineage proofs, explicit rewrite authorization, evidence-before-publication support.
- Obsolete workspace/checkpoint/lifecycle recovery bodies in `runtime.py` were physically removed. Do not demand their removal again.
- The **latest checkpoint already added** `UpdateBasePrimitiveCapabilities` and `execute_update_base()` with the intended sequence `recover → prepare → apply → publish → finish`, plus order/failure tests. **This is presently an unconnected scaffold**, not a finished transaction. Reuse and adjust it; do not invent another wrapper/Protocol.
- Previous full-suite runs repeatedly passed over 1,200 tests. Do not weaken the existing regression suite.

## Only required implementation work

### A. Finish product-lineage update-base transaction (the main blocker)

Current `ServiceEngine._reconciliation_apply_update_base_primitives()` is still ~414 lines containing actual WAL, rebase, recovery, rollback, lease/CAS, evidence and final state logic. Its name is misleading: it is an aggregate transaction, not a primitive. It must **disappear as a transaction implementation**, leaving short service entrypoints and narrow concrete operations.

Use the **existing** `reconciliation_transactions.py` sequencer as a starting point. Review the actual old method once to map exact mutation/observation boundaries. Then move its control flow into cohesive stage functions, approximately:

1. **Dispatch and fresh admission.** Under the existing owner lock, establish ticket/base/target/workspace/evidence binding, product/control/remote observations, continuation authorization, and current `rewrite_stage`. Do not change retry intent.
2. **Recover `local-pending`.** Handle interrupted local rebase: inspect dirtiness, safely abort/reset only when provenance and expected lineage permit it, clean the corresponding WAL fields, and either continue under existing semantics or fail closed.
3. **Recover `remote-pending`.** Compare the exact observed remote with expected old head or rewritten checkpoint: safely roll back an un-published rewrite or prove already-published lineage and evidence, resolve once, otherwise block. Do not unconditionally rerun publish/rebase after a crash.
4. **Prepare fresh rewrite.** Prove original and intended lineage, remote predecessor and explicit published rewrite permission; **repeat fresh admission immediately before recording the WAL intent** (intentional TOCTOU defense). Persist the existing `local-pending` intent and displaced checkpoint evidence before destructive change.
5. **Apply local rewrite.** Execute the existing rebase under owner-held capability, validate the rewritten linear chain, exact length, clean worktree, and target/tip. On failure keep or restore durable state and old checkpoint **exactly as prior semantics permit**; do not erase evidence.
6. **Publish / remote phase.** Persist `remote-pending` before potentially ambiguous remote effects, pin rewritten evidence before lease-guarded publish, enforce exact expected old remote/CAS and check observed completion. Unpublished and published paths must retain their distinct semantics.
7. **Resolve.** Build and persist the existing resolved reconciliation record, execution base/remote identity, todo fingerprint and `resume_required`. Finalize only after publication proof; support idempotent restart without duplicate publication.

The five already introduced primitive capability names (`reconciliation_recover_pending_rewrite`, `reconciliation_prepare_rewrite`, `reconciliation_apply_rewrite`, `reconciliation_publish_rewrite_effect`, `reconciliation_finish_rewrite`) are a **starting sketch**, not a requirement to keep a flawed linear flow: `remote-pending` may be terminal, and `local-pending` may revert then continue. Represent these as explicit outcomes; do not force every recovery through prepare/apply/publish/finish. Do not move a whole stage back into a single 400-line owner callback. Prefer meaningful typed stage results over untyped `object` where practical, but avoid gratuitous new types.

**KISS:** normal private functions for transaction branching are fine. Owner-provided primitives should be *small effects or proofs* (save WAL, observe/prove Git state, workspace rebase/restore, pin evidence, lease/CAS push, finalize), not duplicate orchestrators. Preserve existing error taxonomy and public CLI behavior. Eliminate now-unused `UpdateBaseEffectCapabilities` compatibility callbacks and renamed old aggregate bodies once replaced. Do not move the Git lock into a second actor.

### B. Precise DRY constraints

Share *identical* linear-chain proof, evidence-ref identity checks, safe old-checkpoint reset, and resolved record construction between initial and crash-replay paths **when their preconditions are identical**. Do not deduplicate mandatory repeated Git observations/admission checks: these protect against TOCTOU between proof, WAL write and ref mutation.

### C. Focused tests and verification

Add tests *of real new sequence*, not merely that mocks receive `prepare/apply/publish/finish` calls:
- fresh unpublished and authorized published rewrite, including remote lease/CAS conflict;
- crash after `local-pending` and after rebase; safe abort, exact history checks, and retry;
- crash before/after remote publish at `remote-pending`; proof-based resolution or fail closed, no double publication;
- evidence pin failure prevents publication; displacement and rewritten evidence cannot be silently overwritten;
- divergent/merge lineage, changed control/product observations, dirty/occupied workspace, explicit operator stop versus auto-resume.

Run full authoritative GitHub Actions tests/coverage/Ruff against the **next exact checkpoint**. Latest checkpoint `984609187924` has CI run [#38611841364](https://github.com/ranmasao/devlegate/actions/runs/38611841364), currently reported as failed; inspect the failure and fix it, but **do not replace main extraction with another lint-only iteration**.

## Explicitly out of scope for TASK-061

The 1,173-line `_run_iteration_body()` is a separate **follow-up refactor**, not part of the remaining reconciliation acceptance gate. Do not touch it just to shorten it, and do not add new tickets without operator instruction. Proposed later KISS/DRY decomposition, to preserve for planning:

1. pending recovery and resume dispatch;
2. product fetch/synchronization and `merge_pending` recovery;
3. execution planning and authorization;
4. accepted integration and replay;
5. ticket selection, retry and execution admission;
6. workspace preparation and worker launch/retirement;
7. checkpoint/report WAL;
8. product drift / reconciliation disposition;
9. publication and lifecycle completion.

Those stages must preserve current order, early exits and durable boundaries. They are a roadmap **only**, not work to execute under TASK-061.

Do not change TASK-066 logging, published v0.5.6, CLI formats, runtime schema, independent worker scheduling, or unrelated domains.

## Acceptance / review contract

- The existing aggregate `_reconciliation_apply_update_base_primitives()` transaction body is gone; `ServiceEngine` retains only short dispatch and narrow owner-held effects.
- Stage sequencing including both interrupted rewrite states lives in extracted reconciliation code. No replacement 400-line callback or compatibility fallback.
- Existing WAL, single-owner lock, interruption intent, TOCTOU checks, idempotent recovery, Git lease/CAS and immutable evidence semantics preserved.
- Focused replay/conflict tests plus full tests/coverage/Ruff green on the exact checkpoint.
- Worker report names concrete achieved changes and **only actual remaining blockers**. Do not spend the next execution on a new architecture proposal or reiteration of prior reviews.

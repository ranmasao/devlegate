---
"type": "devlegate.ticket"
"title": "Complete update-base reconciliation extraction using durable-state dispatch"
"depends_on": ["TASK-057"]
---

## Current review: REQUEST CHANGES

Resume the existing TASK-061 worktree/checkpoint chain. The latest reviewed worker execution is `027ebb7aa30a40b19432ca4f710be38b`, checkpoint `f62eedab941926250ac2ac111fdcf15a10091bcd`, outcome `incomplete`.

[Authoritative CI #38617019692](https://github.com/ranmasao/devlegate/actions/runs/38617019692): **SUCCESS**, 1207 passed, 1 skipped; Ruff passed. **Do not spend another iteration fixing the previously failing primitive protocol or reviewing already-green CI.** A new checkpoint must pass its own CI.

### What is already done — preserve

- Typed and bounded recovery/reconciliation helpers exist in `recovery.py`, `recovery_transactions.py`, `recovery_workspace.py`, `recovery_workflow.py`, and `reconciliation.py`.
- Previous obsolete workspace and workflow legacy bodies were removed from `runtime.py`.
- `reconciliation_transactions.py` provides admission, read-only linear-lineage proofs, published rewrite authorization, evidence pin before lease/CAS publication, and a lock-aware `run_update_base()`.
- **Latest checkpoint repaired the real engine path.** `run_update_base()` checks durable `rewrite_stage` and branches to `local-pending`, `remote-pending`, or fresh rewrite. It removed the mandatory `UpdateBasePrimitiveCapabilities` requirement that caused 19 CI failures in checkpoint `984609187924`. The production CI is green again.
- Regression tests for admission, evidence pin failures, previously published lineage and cross-layer recovery exist; preserve them.

### Precise remaining defect

The branches added in the latest checkpoint are still thin aliases to **one old large method**:

- `ServiceEngine._reconciliation_start_update_base()` contains roughly 400+ lines of recovery, Git/rebase effects, WAL transitions, publish and resolution.
- `ServiceEngine._reconciliation_recover_local_rewrite()` forwards back to that method.
- `ServiceEngine._reconciliation_recover_remote_rewrite()` also forwards back to that method.
- `reconciliation_transactions.execute_update_base()` is explicitly described in source as a **legacy seam for isolated boundary tests**, not used on the production path. It still models a linear five-stage flow. Remove or replace that dead scaffold and its mock-only tests if it has no purpose after extraction.

**Goal:** physically move the real branching/transaction sequencing out of the 400+ line `ServiceEngine` method and leave a small owner dispatch plus focused owner-held effects. Do not satisfy this by renaming or wrapping the existing aggregate method.

## Concrete decomposition — KISS, not framework-building

Implement the existing behavior as straightforward private procedures in `reconciliation_transactions.py` (or a clearly cohesive sibling module if size requires). Keep the current lock ownership and WAL fields. Suggested logical boundaries; adjust names only when clearly justified:

**1. Durable-stage dispatch:** While holding the existing serialized ServiceEngine owner lock, load/validate the persisted reconciliation record, ticket binding, target, authorization and workspace provenance. Branch on `rewrite_stage`. Unknown state fails closed. Keep existing admission and error semantics.

**2. `recover_local_pending`:** Handle the existing interrupted-rebase path. Inspect rebase/workspace state and original checkpoint; only abort/reset after proving a safe known origin or exact rewritten linear chain and expected count. Clear the appropriate WAL fields and restore original base. Match the current rule about whether to retry/continue. Do not perform an unconditional fresh rebase.

**3. `recover_remote_pending`:** Distinguish (a) remote still equal to recorded expected old HEAD, (b) remote exactly equal to recorded rewritten checkpoint, and (c) any other HEAD. Case (a) safely aborts/restores according to prior behavior; case (b) verifies displaced and rewritten evidence and exact rewritten lineage, then resolves **without a second publish**; case (c) fails closed. Reuse saved authorization only when bound to that existing durable operation. This path may terminate the transaction; it must not fall through to fresh rewrite.

**4. `start_fresh_rewrite`:** Prove original execution chain, checkpoint, published-tip authorization, workspace and current remote/control/product identities. Repeat full read-only admission at the original TOCTOU boundary just before persisting `local-pending` WAL and performing effects. Preserve immutable displaced evidence and intent ordering.

**5. `perform_rebase_and_verify`:** Apply local rebase, verify clean worktree, commit count, single-parent chain from exact new base through rewritten tip; preserve the existing recovery/rollback path on failure, including restoring the previous execution base and WAL state. Don't silently discard a partially applied operation.

**6. `publish_rewritten_history`:** At the original safe point, persist `remote-pending` if published history is being rewritten; pin rewritten history evidence before remote mutation; use the **existing expected-head lease/CAS** and fail closed if the remote changed. Keep unpublished and published behavior distinct. Avoid inventing extra persistence stages not supported by current WAL/replay contract.

**7. `resolve_reconciliation`:** Resolve only after the necessary publication proof; persist the established resolved record, execution base/start/remote identities, control/todo fingerprint and `resume_required`. Idempotent recovery must not produce duplicate publication or duplicate lifecycle effects.

These are logical steps, **not seven new Protocol methods**. Use plain helpers for pure validation, result construction and branch control. Where an extracted function needs an actual Git or state mutation, call an existing small owner-held capability or expose a **narrow effect**. The owner remains the only mutation authority, and transaction control flow should not bounce through a second 400-line service callback.

### DRY / safety rules

- Deduplicate equivalent lineage proof, evidence validation, safe checkpoint restoration and resolved-state construction **only when preconditions match**.
- Repeated observations and admission immediately before WAL/ref changes are deliberate TOCTOU protection; **do not remove them for DRY**.
- Preserve lease/CAS, rebase rollback, fail-closed unknown state, operator interruption/continuation semantics from TASK-057, identity checks, and exact existing error behavior.
- No new generic state-machine framework, dynamic `getattr` dispatch, runtime-checkable Protocol hierarchy or compatibility callback only to make source shorter.
- Physically remove the replaced aggregate `_reconciliation_start_update_base()` and the old alias callbacks when no longer used. If any short service entrypoint remains, it should contain **no complete rewrite/recovery algorithm**.
- Do not restructure `_run_iteration_body()` under TASK-061. Its ~1173-line staged decomposition is independent follow-up work; do not create it without explicit operator request.
- TASK-066, previously published 0.5.6, unrelated CLI/logging, public data formats and other subsystems are out of scope.

## Regression proof needed

Prioritize **real owner/engine behavior**, not newly mocked call-order-only tests:

- Fresh unpublished update-base and authorized published exact-lineage rewrite.
- `local-pending` crash immediately after WAL, during/after rebase, safe recovery and refusal to reset unproven history.
- `remote-pending` crash before remote mutation and after successful remote mutation, exact evidence proof, no repeated publication, unexpected remote HEAD fails closed.
- Failure to pin evidence prevents publication; lease/CAS conflict preserves original checkpoint and remote.
- Dirty or occupied workspace, changed product/control generation, merge/nonlinear lineage and explicit published rewrite authorization.
- Retry/resume, persisted result and service restart/IPC semantics of existing regression suite.

Use existing regression coverage first, add targeted tests for **newly extracted branches** rather than reimplementing scenarios already covered. Run and report full pytest/coverage plus Ruff through authoritative GitHub Actions on the **next exact SHA**.

## Completion gate

The substantive WAL-state-dependent update-base/recovery sequence resides in extracted reconciliation code and the monolithic ServiceEngine method is gone; no replacement aggregate capability or forced linear sequencer. The existing green real-engine behavior is preserved, focused crash/replay regressions pass, and exact-checkpoint CI is green.

**Worker instruction:** Implement this delta directly from current code. Do not undertake another design investigation, produce wrapper-only checkpoints, revisit previously completed work, or append historical reviews to this ticket. If blocked, report the specific code-level invariant preventing extraction.

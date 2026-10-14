---
"type": "devlegate.ticket"
"title": "Extract workflow recovery and reconciliation from ServiceEngine"
"depends_on": ["TASK-057"]
---

## Milestone

Devlegate 0.5.7 — runtime domain decomposition and workflow recovery boundary.

## Goal

Extract the orchestration's workflow/execution recovery and reconciliation
mechanisms from the monolithic `src/devlegate/runtime.py` into cohesive,
testable internal modules with explicit ownership, state, and Git/provenance
interfaces. Preserve all observable workflow, recovery, and fail-closed
semantics. The result is a smaller coordinating `ServiceEngine`, not a
renamed monolith or a parallel workflow authority.

This work intentionally follows the 0.5.6 TASK-057 change to interruption
mechanism versus durable continuation intent. Do not implement on or expand
the 0.5.6 release branch.

## Motivation and observed baseline

The `release/0.5.6` runtime.py at commit
`6e0d3ff8972ae0c1dba1aab0e8cb02bb18afe3a9` has 9,790 lines;
`ServiceEngine` has 187 methods, with a 1,158-line
`_run_iteration_body` and a roughly 500-line
`_reconcile_update_base_owned`.

The file combines distinct domains: service hosting, operator admission and
durable receipts, state persistence, control/product Git observations, ticket
scheduling, worker admission, execution workspace and publication, checkpoint
lifecycle, accepted integration, recovery/reconciliation, status projections,
and project bootstrap/render/check operations. The most risky coupling is
workflow recovery and Git lineage reconciliation embedded alongside the
normal scheduling loop and public service facade.

## Recovery/reconciliation scope

Extract the *existing* interdependent mechanisms, according to actual call
and state boundaries:

- detection/classification of interrupted, stale, stranded and recoverable
  execution generations;
- safe retry/forced-retry, zero-delta handling and historical checkpoint
  retirement where these are part of recovery;
- pre-worker execution-workspace recovery, old-generation checkpoint
  preservation, and proof of existing execution branch ownership;
- recovery of checkpoint commit, publication, worker report and workflow
  lifecycle after crashes at individual transaction stages;
- replay and proof of ticket transitions on the control plane, including
  descendant-control generation handling;
- accepted-integration recovery, with exact checkpoint/ancestry checks;
- same-base resume, product-base update, and lease-guarded published
  execution-lineage rewrite, retaining displaced-history evidence;
- operator-authorized drop/retirement recovery when needed to keep
  lifecycle and evidence semantics consistent;
- reconciliation evidence records, stale-state detection and durable
  resolution/continuation intent.

Identify actual method families in the current branch before extraction;
the baseline names include `_inspect_bound_execution_workspace`,
`_repair_bound_execution_workspace`, `_recover_checkpoint_publication`,
`_recover_lifecycle_report`, `_recover_accepted_integration`,
`_reconcile_update_base_owned`, `_reconcile_resume_owned`,
`_reconcile_stranded_execution`, `_normalize_stranded_execution`,
`_recover_interrupted_execution`, and their proof/evidence helpers.
Do not mechanically move only this example name list while leaving most
recovery decisions in `runtime.py`.

## Ownership and architectural constraints

- `ServiceEngine` remains the single serialized mutation owner and public
  runtime coordinator. Do not add a separately scheduling or independently
  persisting recovery service.
- Extracted modules operate behind explicit typed inputs, state snapshots,
  proof results and narrowly scoped mutation operations. Establish one
  authoritative place for each recovery decision and each write-ahead
  transition. Do not pass an unrestricted `ServiceEngine` instance into a
  second 3,000-line object merely to rename the coupling.
- Keep runtime state and transaction stage identifiers authoritative.
  Mutations still require the existing owner lock, identity/lease checks,
  durable receipts, compare-and-swap refs, and fail-closed proof of exact
  product/control/workspace/worker ownership.
- Preserve the distinction between interruption mechanism and continuation
  intent introduced by TASK-057. Recovery cannot turn a deliberate operator
  stop into an automatic retry.
- Recovery must remain idempotent under crash/restart and must never replay
  a completed ticket, spawn overlapping workers, or promote an unproven
  checkpoint. Historical execution evidence must not be destroyed.
- Keep service-facing command semantics and the established
  `status`/`plan`/retry/reconcile/drop outputs stable unless a documented
  correction is explicitly justified by failing evidence.
- Avoid circular imports with `runtime.py`. Put shareable types or
  protocols at the appropriate lower-level layer. Reuse
  `execution_workspace`, execution reports, runtime store, Git support and
  worker supervision modules; do not duplicate their existing responsibilities.
- Keep source runtime stdlib-only and existing Linux behavior intact.

## Implementation approach

1. Inventory the current recovery state machine, transitions, call graph,
   persistent state keys, external commands and proof dependencies.
   Document the boundaries and choose a small set of coherent modules
   (for example recovery classification, workflow/lifecycle replay, and
   product-lineage reconciliation), not one indiscriminate utility file.
2. Extract cohesive internal contracts and proofs. Use separate, testable
   classification/decision logic where practical, and service-owned
   transactional mutation procedures with explicit capabilities.
3. Reduce `runtime.py` to coordination, admission and dispatch for these
   behaviors; move substantive recovery/reconciliation logic and invariants
   into the owning modules. Keep wrappers only for justified compatibility
   seams and avoid duplicate implementations.
4. Update architecture/operations documentation with a domain/ownership
   map and the recovery transaction phases. Describe what can be resumed,
   must be retried, must be reconciled, or fails closed.
5. Preserve current tests and add focused boundary tests where the refactor
   introduces seams. Do not weaken regressions simply because test mocks
   previously reached private `ServiceEngine` methods.

## Required regressions

Cover at minimum:

- clean fresh ticket, ordinary successful completion and accepted integration;
- exact once-only recovery across pre-worker, worker, checkpoint, publication,
  control lifecycle and accepted integration interruption boundaries;
- same-base resume, moved descendant product base, provable same-parent
  replacement and rejected divergent/unobservable product histories;
- unpublished and published multi-checkpoint lineage, including CAS/lease
  conflict and exact preservation of displaced evidence;
- restart/force-restart continuation from TASK-057 versus force-stop manual
  retry, with no overlapping worker generations;
- normal retry, forced retry, zero-delta drift retry and genuinely
  non-retryable failure, without duplicate admission;
- changed ticket generation and old work-branch history: historical evidence
  preserved, only the correctly authorized new generation admitted; never
  blindly merge or reuse outdated implementation;
- wrong/missing/dirty/occupied worktrees, malformed reports, changed control
  generations, missing or mismatched evidence, and uncertain worker
  ownership all fail closed without destructive side effects;
- replayed operator commands and service restarts do not duplicate
  mutation, execution or publication;
- existing end-to-end self-hosting and IPC/status projections remain green.

## Acceptance criteria

- A documented responsibility/transaction map identifies which recovery
  decisions and side effects belong to each extracted module, with
  no second mutation authority.
- Substantive workflow recovery, lineage proof/rewrite and crash replay
  mechanics no longer reside inside `ServiceEngine`; `runtime.py` shrinks
  materially. Reduction in LoC is evidence, not the primary correctness goal.
- Recovery modules are independently unit-testable through typed interfaces,
  avoid circular imports, and reuse existing authoritative state/provenance.
- Existing durable state and workflow contracts retain their behavior,
  including exact-once, owner isolation and failure-closed invariants.
- TASK-057 continuation semantics and existing host/worker/reconciliation
  regressions are preserved with new seam tests where necessary.
- Comprehensive authoritative CI (tests, coverage, Ruff) passes on the exact
  reviewed checkpoint; coverage quality is assessed for moved paths,
  not merely aggregate percentage.

## Non-goals

This ticket does not add a watchdog (TASK-056), redesign the public CLI,
implement parallel workers, alter package formats, introduce plugins or
providers, split all unrelated domains out of runtime.py, or create a
new persistent database/state machine. Broad configuration (TASK-014)
and OpenCode adapter isolation (TASK-012) remain separate work.

## Review — 2026-10-14, checkpoint adef7f47388f (REQUEST CHANGES)

Reviewed exact worker checkpoint
`adef7f47388f82cf78933dbb00fddcf073c73c93` from execution
`5e566f156c1a4093885abcd4ce72bffc`.

**CI FAIL**: [GitHub Actions #38523276816](https://github.com/ranmasao/devlegate/actions/runs/38523276816),
`tests/test_licensing.py::test_devlegate_owned_source_has_exact_eupl_header`
failed because newly added `tests/test_recovery_boundaries.py` lacks the
required EUPL-1.2 copyright/SPDX header. 1193 tests passed, 1 skipped;
Ruff was skipped because the test step failed. Fix the test header and
obtain authoritative clean tests + Ruff on the new checkpoint.

**Primary acceptance blocker — extraction insufficient**: only small
read-only helpers (97 lines `recovery.py`, 67 lines
`reconciliation.py`) were extracted, while the substantive checkpoint
publication/replay, workflow/lifecycle recovery, accepted integration,
workspace repair, and reconciliation mutation procedures remain in
`ServiceEngine`. `runtime.py` was reduced by only ~25 net lines.
The ticket explicitly requires material decomposition of those mechanisms,
not a few classification functions and wrappers.

**Required rework**:
1. Preserve current checkpoint and tests as a starting point, but implement
   cohesive recovery and reconciliation domain boundaries for the substantial
   transaction logic identified in the ticket. Move or delegate recovery
   transactions through explicit narrow owner-held capabilities or
   service-owned procedures; document precise ownership. Avoid a second
   mutation authority, circular imports, or passing an unrestricted
   `ServiceEngine` object into a renamed monolith.
2. Include checkpoint publication, control lifecycle/accepted integration
   replay and workspace recovery, plus lineage reconciliation and its
   proof/lease/CAS/evidence invariants. If implementation must be split
   into additional internal modules, do so coherently. Maintain TASK-057
   manual retry/continuation intent and fail-closed behavior.
3. Add substantive boundary/transaction regression coverage in addition to
   pure classifier tests. Preserve all existing recovery behavior.
4. Fix the missing test SPDX/license header, run focused checks where
   supported, and ensure full CI/coverage/Ruff pass on the next exact SHA.
5. Document any genuinely blocked work, without reporting the requested
   extraction complete prematurely.

Return to `todo` for **focused continuation** of the same ticket;
do not discard the existing worker checkpoint or modify the 0.5.6 tag.

## Review — 2026-10-14, checkpoint 357c4be59b9c (REQUEST CHANGES)

Reviewed continuation execution `59c6db9d1bbc4bbe997221ac1795c8fa`,
checkpoint `357c4be59b9c2e5a843b139a30d2ea0beaed5cfc`.
[Exact-checkpoint CI #38533174124](https://github.com/ranmasao/devlegate/actions/runs/38533174124)
passed: **1196 passed, 1 skipped**, Ruff passed. The missing EUPL test
header is fixed.

This iteration moves meaningful owner-held transactions into the new
`recovery_transactions.py` through typed capabilities: stranded-worker
normalization and control-lineage adoption, with focused tests and updated
ownership documentation. Preserve this work and its security invariants.

**Acceptance blocker**: the worker explicitly reports `incomplete` and
checkpoint/publication replay, lifecycle and accepted-integration recovery,
workspace inspection/repair, and product-lineage reconciliation/rewrite
remain in `ServiceEngine`. These are the principal requested extraction
domains and cannot be deferred while declaring TASK-061 done.

**Next focused continuation**:
1. Finish extracting the remaining substantive recovery and reconciliation
   transaction families into coherent owner-held modules/procedures, with
   narrowly scoped typed capabilities. Preserve only one authority for
   locks, Git mutations, SQLite/WAL, leases, CAS, and durable evidence.
2. Test crash replay, lifecycle/report/checkpoint recovery, workspace
   ownership/fail-closed states, product lineage rewrite, and forced-stop
   manual-retry semantics at the new seams; do not regress existing tests.
3. Keep new transaction boundaries documented and report exact remaining
   scope truthfully. Re-run exact-checkpoint CI and Ruff.
4. Avoid unrelated TASK-066 log-reader changes and do not modify v0.5.6.

**Disposition: REQUEST CHANGES; review -> todo**, preserving worker
continuation lineage.

## Review — 2026-10-14, checkpoint 962e872e4522 (REQUEST CHANGES)

Reviewed execution `af1293ce4bda414f8aee11ba5e3021f6` at
`962e872e4522d984dce5db3590e30c77d42f6340`.
[Exact-checkpoint GitHub Actions #38536241642](https://github.com/ranmasao/devlegate/actions/runs/38536241642)
passed: **1197 passed, 1 skipped**, Ruff passed.

The new `recovery_workspace.py` meaningfully extracts pre-worker
workspace inspection and repair with owner-supplied capabilities and
fail-closed provenance/CAS checks. Preserve this work.

**Still not acceptable as TASK-061 completion**:

1. Checkpoint/publication replay, lifecycle/report recovery,
   accepted-integration recovery, and product-lineage reconciliation/rewrite
   remain substantively implemented in `ServiceEngine`; the worker lists
   them as outstanding. Finish the extraction into cohesive, limited-capability
   transaction modules without delegating independent mutation authority.
2. Remove unreachable legacy code after immediate returns in
   `_inspect_bound_execution_workspace` and
   `_repair_bound_execution_workspace`. Having old implementations remain
   in `runtime.py` violates single authoritative ownership and DRY.
3. Preserve existing fail-closed proof, WAL, lease/CAS, displaced-history
   retention, exact-once replay, and TASK-057 manual-retry invariants with
   focused boundary tests, plus exact-checkpoint green full CI and Ruff.
4. Keep unrelated TASK-066 log-stream changes and published v0.5.6 out of scope.

**Disposition: REQUEST CHANGES; review -> todo.** Continue the established
TASK-061 worktree/checkpoint lineage. CI success alone is not proof of
the full extraction acceptance criteria.

## Review — 2026-10-14, checkpoint aa5953b77c99 (REQUEST CHANGES)

Reviewed execution `b3a6b74b5a0a43c39e4a4732a8e50f81`,
checkpoint `aa5953b77c999bebee058b37a52cf20b488335ef`.
[Exact-checkpoint CI #38568431656](https://github.com/ranmasao/devlegate/actions/runs/38568431656)
**failed** at Ruff: 38 lint errors, including line length and import
formatting. The test phase passed **1197 tests, 1 skipped**.

Positive progress: `recovery_workflow.py` now holds the checkpoint and
publication WAL replay, lifecycle report recovery, and accepted-integration
replay with a typed capabilities interface. Preserve this extraction.

**Outstanding acceptance blockers**:
1. Complete product-lineage reconciliation and published-lineage rewrite
   extraction with explicit narrow owner-held capabilities, preserving lease,
   CAS, rollback/displaced-history evidence, and fail-closed behavior.
2. Remove unreachable duplicate legacy implementations from `runtime.py`,
   including old workspace and recovery/replay bodies after delegating to
   new modules. Do not leave parallel authority paths.
3. Add focused boundary/transaction tests for new `recovery_workflow`
   paths (crash/replay, checkpoint, lifecycle, accepted integration, invalid
   evidence), not only the pre-existing runtime tests.
4. Fix all 38 Ruff errors, run focused checks as available, and achieve
   green authoritative CI on the next exact checkpoint. Do not claim the
   entire TASK-061 complete while substantial requested parts remain.
5. Keep TASK-066 logging refactor and stable v0.5.6 out of scope.

**Disposition: REQUEST CHANGES; review -> todo.** Continue the existing
workspace/checkpoint lineage.

## Review — 2026-10-14, checkpoint 897fa6dbde6e (REQUEST CHANGES)

Reviewed execution `479fe3f933ea40e397c9d908bdf82b77`,
checkpoint `897fa6dbde6e38dd16567649e4050089cbb0fe81`.
[Exact-checkpoint CI #38573288086](https://github.com/ranmasao/devlegate/actions/runs/38573288086)
**failed**: `tests/test_control_scheduler.py::test_control_reconciliation_remote_change_is_side_effect_free`
(DID NOT RAISE DevlegateError). **1198 passed, 1 skipped**;
Ruff did not run because the test step failed.

Useful progress: `reconciliation_transactions.py` extracts typed update-base
admission and lineage/evidence proofs. Retain the work with strict ownership.

**Required changes**:
1. Restore the repeated fresh control-reconciliation admission/proof
   immediately before the Git mutation/evidence pin. It is an intentional
   time-of-check/time-of-use safeguard, not redundant validation.
   Preserve `test_control_reconciliation_remote_change_is_side_effect_free`
   and add focused coverage for remote/control movement between checks.
2. Complete substantive extraction of product-lineage rewrite effects and
   its lease, CAS, displaced-history preservation, and exactly-once steps;
   admission/proof extraction alone is not the transaction boundary
   required by TASK-061.
3. Remove unreachable old checkpoint, lifecycle and workspace recovery
   bodies from `runtime.py`, leaving only justified narrow wrappers and
   one implementation of each mutation path.
4. Avoid blanket `# ruff: noqa: E501` in `runtime.py` and
   `recovery_workflow.py`; format affected code explicitly and prove
   clean Ruff and full CI on next exact SHA.
5. Add/refine transaction-boundary tests for rewrite/replay and preserve
   fail-closed behavior; avoid unrelated TASK-066 changes.

**Disposition: REQUEST CHANGES; review -> todo**, continue established
checkpoint lineage. Published v0.5.6 remains untouched.

## Review — 2026-10-14, checkpoint 3413d5147c3e (REQUEST CHANGES)

Execution `9e73af98d0a84e36abe386cbb4224bf8`, checkpoint
`3413d5147c3e91c5bb7d2d1fe11f6543202d7339`.
[Exact-checkpoint CI #38575535707](https://github.com/ranmasao/devlegate/actions/runs/38575535707):
**1199 passed, 1 skipped**; Ruff **failed** on one F401 unused import
`validate_execution_binding` in `src/devlegate/runtime.py:54`.

Progress: control-lineage admission has been restored before evidence pin
and again immediately before control-worktree mutation, addressing the
remote-movement TOCTOU failure. Unreachable legacy implementations in
`_inspect_bound_execution_workspace` and
`_repair_bound_execution_workspace` were physically removed.

**Remaining blockers**:
1. Move the substantive product-lineage rewrite and publication/CAS/lease/
   displaced-history transaction from `ServiceEngine` into coherent
   owner-held capability-based implementation; admission-only extraction
   remains insufficient.
2. Physically delete remaining unreachable legacy
   checkpoint/publication and lifecycle replay bodies from `runtime.py`,
   avoiding duplicate implementations.
3. Add focused transaction tests for product-lineage rewrite, lease/CAS
   conflict and evidence, checkpoint/lifecycle crash replay and fail-closed
   behavior; retain the already green behavior suite.
4. Remove the unused import, resolve any other Ruff violations, and get
   full authoritative tests and Ruff green on the exact next checkpoint.
5. Preserve controls from TASK-057, avoid TASK-066 log-reader changes and
   do not modify the v0.5.6 release.

**Disposition: REQUEST CHANGES; review -> todo**, continue existing
TASK-061 execution lineage.

## Review — 2026-10-14, checkpoint 436d3f852c43 (REQUEST CHANGES)

Reviewed execution `ab8609fcd7df4e789def28d034afa4dd`,
checkpoint `436d3f852c4364dc0f1a5cade4dd659d0a2df919`.
[Exact-checkpoint CI #38583834141](https://github.com/ranmasao/devlegate/actions/runs/38583834141)
**passed**: 1199 tests passed, 1 skipped, Ruff clean.

Progress accepted as continuation: `publish_rewritten_lineage()` now
sequences evidence pinning and lease-guarded publication via typed owner
capabilities. Removed unused import and blanket E501 suppressions.
Preserve this code and existing exact-checkpoint green tests.

**Remaining acceptance blockers**:
1. Physically remove unreachable legacy checkpoint/publication and lifecycle
   replay bodies in `runtime.py`. Only the delegated
   `recovery_workflow` paths should implement these transactions; keep
   necessary thin compatibility wrappers, never duplicate algorithms.
2. Verify all transferred recovery and lineage operations have coherent
   owner-held capability boundaries, with no second mutation authority,
   loss of retry intent, or proof/CAS/lease/evidence safeguards.
3. Add focused boundary tests for checkpoint/publication and lifecycle
   crash replay, plus rewrite lease/CAS conflict and displaced evidence
   where existing tests do not directly exercise the new seams. Maintain
   full CI/coverage/Ruff green on the exact next checkpoint.
4. Avoid unrelated TASK-066 changes and do not modify v0.5.6.

**Disposition: REQUEST CHANGES; review -> todo**, preserving current
workspace and checkpoint lineage.

## Review — 2026-10-14, checkpoint 51e99b525948 (REQUEST CHANGES)

Execution `9da516ebe6e54cd18edb095ad6e5bea5`, checkpoint
`51e99b5259486f372dddabf10cce22b022bb9692`.
[Authoritative exact-checkpoint CI #38587546197](https://github.com/ranmasao/devlegate/actions/runs/38587546197)
**completed successfully**. CI success is acknowledged and is not the
reason for requesting changes.

Useful progress: physically deleted unreachable legacy
`_recover_checkpoint_publication_legacy` and
`_recover_lifecycle_report_legacy` bodies from `runtime.py`; added two
focused tests for evidence-before-publish ordering in published and
unpublished rewrite cases. Preserve current checkpoint lineage.

**Remaining acceptance blockers** (also expressly acknowledged in the
worker's `incomplete` claim):
1. Finish substantive product-lineage reconciliation/rewrite transaction
   migration out of `ServiceEngine` into narrowly capability-scoped
   owner-held modules, retaining WAL, lease, CAS, rollback and displaced
   evidence semantics. Extract more than admission and two-operation
   publication wrappers.
2. Add focused crash/replay and fail-closed tests at the new
   `recovery_workflow` and product rewrite seams, including interruption
   boundaries and conflict cases.
3. Re-run and pass authoritative full CI/coverage/Ruff on the next exact
   checkpoint; preserve TASK-057 interruption/continuation behavior and
   single mutation owner. Keep TASK-066 and published v0.5.6 untouched.

**Disposition: REQUEST CHANGES; review -> todo.**

## Review — 2026-10-14, checkpoint 0e2ce34c40da (REQUEST CHANGES)

Reviewed execution `7bc2843f97044c60a30dd573ccdd7ea2` at
`0e2ce34c40dab2d692334e2a3f160adf2e6a5a0c`.
[Exact SHA CI #38596475701](https://github.com/ranmasao/devlegate/actions/runs/38596475701)
failed at Ruff: two I001 unsorted imports in `src/devlegate/runtime.py`
and `tests/test_recovery_boundaries.py`. Tests: **1202 passed, 1 skipped**.

New `prove_workspace_lineage` and `authorize_workspace_rewrite`
routines move linear-chain proof and published-tip authorization to
`reconciliation_transactions.py`, with boundary coverage. Preserve them.

**Unfinished acceptance requirements**:
1. The principal product-lineage rewrite and reconciliation transaction
   is still sequenced in `ServiceEngine`: WAL state transitions, rebase,
   crash recovery, rollback, lease/CAS publication, and final durable
   resolution. Extract this **substantive orchestration** to a cohesive
   module with narrow owner-held capabilities, rather than only adding
   further proof wrappers. Retain lock/provenance and sole mutation authority.
2. Remove any remaining duplicate remote-pending rewrite proof and
   lifecycle/recovery implementations from `runtime.py`. Do not retain
   old code after delegation.
3. Add focused replay/fail-closed tests across new transaction seams
   (rebase interrupted, published lease conflict, immutable displaced
   evidence, restart/idempotency). Preserve existing regression tests.
4. Fix both I001 import sorting issues and obtain green authoritative
   full tests, coverage and Ruff on the exact next checkpoint.
5. Preserve TASK-057 interruption intent; avoid TASK-066 and v0.5.6 changes.

**Disposition: REQUEST CHANGES; review -> todo**, preserve the existing
checkpoint/worktree lineage.

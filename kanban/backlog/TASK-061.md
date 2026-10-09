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

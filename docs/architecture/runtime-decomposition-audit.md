# Runtime Decomposition Audit

## Version 1: first execution

**Scope.** This is a structural audit, not an implementation change. It answers
only the first-review question: which high-impact regions of
`ServiceEngine` mix independent work or repeat an existing proof/transaction
algorithm, and which remain owner-only coordination.

**Snapshot.** Observed integrated product HEAD:
`295b7f04366979567090cc2bbe32a4cae0bb926a` (`TASK-098: Replace brittle
exact Git-command trace assertions with behavioral recovery invariants`). The
working tree was clean before this documentation was added. Line ranges and
physical LOC below are measured from that revision; a physical LOC includes
blank lines and comments inside the method range. This report does not use
measurements from an earlier worker revision.

**Method and terminology.** Source was inspected at the snapshot above:
`src/devlegate/runtime.py`, `git.py`, `execution_workspace.py`,
`recovery_workflow.py`, `recovery_transactions.py`,
`reconciliation_transactions.py`, `control_git_workflow.py`, and
`docs/GIT_COMMAND_INVENTORY.md`. "Observed" means directly supported by the
cited code. "Hypothesis" is explicitly labelled and is not a recommendation
to move behavior. A proof is read-only only when it does not alter Git or
state; `fetch` is an effect because it changes local Git objects/tracking
refs, per `docs/GIT_COMMAND_INVENTORY.md:3-7`.

## Existing boundaries

The current division is meaningful and must not be erased just to shorten
`runtime.py`.

| Owner | Observed responsibility | Evidence |
| --- | --- | --- |
| `git.py` | Typed Git transport plus local/remote observations and guarded ref/worktree effects. It distinguishes a valid negative proof from a command failure. | `src/devlegate/git.py:31-99,220-312,359-414,439-513,903-930` |
| `ExecutionWorkspaceManager` | One execution branch/worktree's topology, preparation, rework replay, inspection, checkpoint, and retirement proof. The caller injects its Git runner. | `src/devlegate/execution_workspace.py:74-92,94-429,584-621,764-838` |
| `control_git_workflow.py` | Ordered control-checkout operations with local validation; no durable runtime-state policy. | `src/devlegate/control_git_workflow.py:4-9,44-368` |
| Recovery modules | Sequence capability calls and validate recovery-specific proof; they do not acquire service authority themselves. | `src/devlegate/recovery_workflow.py:72-276`; `src/devlegate/recovery_transactions.py:44-162` |
| `ServiceEngine` | Owns lock/owner-thread authority, state/WAL transitions, admission, policy, error translation, and the injected runner. | `docs/GIT_COMMAND_INVENTORY.md:9-15,40-50`; `src/devlegate/runtime.py:3852-3869` |

This ownership matches the Git inventory. An extraction that causes a domain
helper to fetch, mutate refs, or write `_state` merely to hide calls from the
engine would violate it.

## High-impact runtime inventory

The table prioritizes control-flow and effect coupling rather than a broad
module LOC ranking. "Existing helper" names an equivalent or closely related
domain capability, not proof that the runtime code should be forwarded to it.

| Runtime function (physical LOC) | Classification | Main responsibilities and dependencies | Callers / existing equivalent |
| --- | --- | --- | --- |
| `__init__` (753-874, 122) | owner policy/serialization | Establishes repository, state/worktree topology, SQLite store, worker supervisor, locks, and lifecycle/snapshot fields. Depends on locator, store, runner, and Git validation. | Service/CLI construction. Retain in engine. |
| `_workspace_cleanup_cohort` (2330-2473, 144) | read-only observation/proof | Collects a bounded, stable candidate cohort from registrations, work-root children, local/remote refs; rejects a symlinked work root rather than traversing substituted ownership. Reads filesystem, manager registrations, `_git`, remote/state settings. | Operator admission at 2265, backlog at 5205, inventory at 5293, public cleanup at 7191. No duplicate cohort construction found. |
| `_validate_state_invariant` (3267-3714, 448) | read-only observation/proof | Validates full runtime WAL schema and cross-field execution, reconciliation, worker, and accepted-integration invariants. No Git effects; parses/proves state/report fields. | `_load_state`, `_save_state`, status paths. Natural schema seam, not a transaction owner. |
| `_save_state` (3716-3782, 67) | owner policy/serialization | Creates the next state payload, clears phase-specific fields, validates it, and durably replaces the SQLite state. Couples transition policy to persistence. | Nearly every mutation path; `SQLiteRuntimeStore.replace` is storage-only at `runtime_store.py:193-226`. Retain save authority in engine. |
| `_run_iteration_body` (3888-5192, 1,305) | mixed | Scheduler plus recovery dispatch, sync/plan/admission, workspace preparation, worker execution, checkpoint, publication, and lifecycle. Touches `_state`/WAL, locks/owner thread, runner, both Git worktrees, workspace manager, ticket store. | `run_iteration` at 3871-3886 and polling. It is an orchestration root, not one extractable transaction. |
| `_reconcile_workspace_backlog` (5194-5280, 87) | owner policy/serialization | Chooses a bounded automatic cleanup cohort, applies it, then durably updates cursor/inventory. | Idle path at 3938-3939; calls `cleanup_workspaces`. Keep policy and cursor state in engine. |
| `_prepare_execution_workspace` (5688-5815, 128) | mixed | Binds plan to execution identity, validates/adopts product/control descendants, stages rework evidence, prepares workspace. Uses WAL, Git, ticket store, and `ExecutionWorkspaceManager`. | Scheduler at 4794. Manager equivalents: `prepare` (94-156), `prepare_rework` (158-323), `inspect` (325-373). |
| `_adopt_unstarted_product_descendant` (5817-5969, 153) | domain transaction | WAL-before-fast-forward adoption of a pre-worker product advance, with topology/ancestry proof and replay markers. | `_prepare_execution_workspace` at 5726. No corresponding extracted transaction. |
| `_integrate_accepted` (6455-6590, 136) | owner policy/serialization | Proves/publishes accepted checkpoint into product, synchronizes checkout, and completes control lifecycle. Spans product/control Git and accepted WAL/report. | Scheduler at 4357 and 4390; recovery capability calls it via `recovery_workflow.py:254-276`. Retain. |
| `_retire_execution_refs` (6690-6837, 148) | low-level Git semantic | Deletes local/remote execution refs after expected-SHA, occupancy, and dependency proof; participates in cleanup effect choreography. | Integrated cleanup at 6631-6688 and generic cleanup at 7251-7270. Its ref effect could consume a precomputed proof, but not decide it. |
| `_assert_cleanup_has_no_execution_dependency` (6838-7098, 261) | owner policy/serialization | Rejects destructive cleanup while active execution, reconciliation, retry, or accepted integration still depends on resources. Reads active WAL/state plus Git/worktree identity. | Called by cleanup application after candidate observation. Retain: it is the cross-WAL authority gate. |
| `cleanup_workspaces` (7176-7350, 175) | mixed | Admitted dry-run/apply cleanup; re-observes target identity/evidence before each destructive stage and reports partial resources. Depends on manager, ticket store, refs/remote Git, state. | Public/operator API, backlog, tests. Candidate for a narrow executor only. |
| `_apply_execution_lifecycle` (8811-8960, 150) | domain transaction | Idempotent lifecycle publication with fetch, fast-forward, replay, and push retries; mutates ticket/report state. | Normal path at 5173, recovery at 10417, historical report path at 8962. `recovery_workflow` sequences recovery but not this effect. |
| `_collect_status_attempt` (9457-9512, 56) and `_make_execution_plan` (9859-10049, 191) | owner policy/serialization | Establish a stable before/after snapshot and derive admission barriers/plans, including worker-ownership refusal. Depend on store revision, Git observations, tickets, worker supervisor. | Status/retry/scheduler paths. Retain because snapshot and owner rules are service authority. |
| `_recover_interrupted_execution` (10942-11135, 194) | mixed | Normalizes/retries stranded execution after fresh product/control/workspace/worker proof. Uses WAL stages, worker identity, recovery and workspace proof. | Recovery paths; `recover_stranded_execution` provides classification/sequencing at `recovery_transactions.py:116-162`. |

## Repeated algorithms and distinct gates

### Observed duplication: reconciliation persistence

The normal worker path pins evidence, constructs a `reconciliation` payload,
and saves it inline at `runtime.py:5094-5137`. The same persistence algorithm
exists as `_persist_product_reconciliation` at `runtime.py:8402-8460`.
Recovery already reaches the latter through the capability path at
`recovery_workflow.py:158-164`. This is concrete same-owner duplication, not
a different safety gate: both persist product reconciliation evidence after
the relevant proof. A future extraction can make the normal path call the
existing helper without granting a recovery module `_state` authority.

### Observed choreography overlap: workspace/ref cleanup

Three paths repeat registration/identity validation followed by retirement and
ref deletion:

| Call site | Differing gate that must remain explicit |
| --- | --- |
| Zero-delta cleanup, `runtime.py:5437-5465` | Only an execution with no product delta is eligible. |
| Immediate integrated cleanup, `runtime.py:6631-6688` | Accepted integration has its own completed lifecycle/evidence proof. |
| Generic cleanup apply, `runtime.py:7251-7270` | Re-observes an admitted candidate and reports partial resource progress. |

The primitives already live in `ExecutionWorkspaceManager._registrations`
(`execution_workspace.py:764-771`), `_validate_existing` (773-838), and
`retire` (383-429), plus `_retire_execution_refs` (6690-6837). This is not a
case for one generic "cleanup manager": the gates differ and
`_assert_cleanup_has_no_execution_dependency` must stay engine-owned. The
duplicated portion is the post-proof resource-effect order.

### Observed duplicated stage choreography: checkpoint/publication

The normal path writes `checkpointing`, `post-checkpoint`, `publishing`,
`post-publication`, and `lifecycle` at `runtime.py:5004-5031,5151-5169`.
`recovery_workflow.py:72-208` independently sequences replay of those
durable stages through capability calls. Both use
`ExecutionWorkspaceManager.checkpoint` (`execution_workspace.py:584-621`).

These are intentionally not identical gates. Recovery proves product state
before recreating a missing checkpoint (`recovery_workflow.py:100-127`) and
requires exact local checkpoint/clean workspace/expected remote before a
publication retry (165-202); normal execution checkpoints first and observes
product afterwards (`runtime.py:5011-5047`). The shared seam is stage
sequencing with explicit normal-versus-replay preconditions, not the removal
of recovery proof.

### Similar observation, intentionally different action policy

`_observe_product_generation` (`runtime.py:8170-8232`) supports normal
post-worker processing (`5047-5150`), lifecycle completion (`5377-5388`),
pre-worker adoption (`5848-5921`), and recovery (`recovery_workflow.py:140-164`).
The observation is shared already. The action choices are deliberately
different, so no additional wrapper is justified now.

## Leading seams: finite and bounded

Only these three seams merit follow-up investigation. They are ordered by
smallest verified overlap first, not by source size.

| Priority and seam | Present -> proposed owner | Minimum contract and invariant | Coupling / independent scope |
| --- | --- | --- | --- |
| 1. Reconciliation persistence | Inline normal code plus runtime helper -> one private runtime-owned persistence routine | Inputs: proven evidence identity and product-generation facts; output: one validated reconciliation state update. It must not fetch, mutate a ref, or acquire a lock. Preserve exact evidence identity and save-after-proof ordering. | Independently extractable as local deduplication. It crosses WAL/state mutation, so keep the method on `ServiceEngine`; no Protocol needed. |
| 2. Cleanup effect executor | Three runtime paths -> an executor consuming an already proven immutable cleanup target and effect plan | Inputs: target identities, allowed resource stages, expected SHAs, and callbacks for engine-only dependency/evidence proof; output: per-stage completion/remaining observations. Preserve re-observation before each destructive effect and never expand the admitted cohort. | Depends on WAL/state/locks indirectly through engine-owned proof callbacks. Do not move `_assert_cleanup_has_no_execution_dependency` or cohort admission. Can follow seam 1 independently. |
| 3. Explicit checkpoint/publication state sequence | Normal scheduler duplicates portions of recovery sequencing -> recovery domain sequence parameterized by normal/replay proof mode | Capabilities must declare every checkpoint exactness, report-match, reconciliation persistence, state-write, and publication operation. Preserve WAL-before-effect, replay exactness, and stricter recovery gates. | Crosses WAL, crash replay, locks, runner, and state mutation. Do not start until the normal/replay data-flow and exception/shutdown propagation are separately mapped. |

`_validate_state_invariant` has a potential fourth, later seam: a typed state
schema/validation module. It is not in the three leading seams because its
448 physical LOC is proof density, not demonstrated duplicated transaction
logic. The engine must still control `_save_state`, phase clearing, and the
store replacement boundary.

## Retain in ServiceEngine

The following are positive retain decisions, not deferred refactoring:

- `_run_iteration_body`: scheduler-wide ordering across policy, state, Git,
  runner, and two worktrees.
- `_submit_operator_command` (1194-1295): operator admission, receipt
  idempotency, serialization, timeout/cancellation.
- `_save_state`, lock contexts, and owner-aware Git wrappers: transition
  authority and shutdown ownership.
- `_apply_execution_lifecycle` and `_integrate_accepted`: multi-repository
  publication/lifecycle authority and report/WAL coupling.
- `_assert_cleanup_has_no_execution_dependency`: current execution and
  reconciliation state are the authority for destructive cleanup refusal.
- `_collect_status_attempt` and `_make_execution_plan`: stable snapshot and
  worker-ownership rules are service policy.

## Effect accounting and tests for any future ticket

The Git inventory is the baseline effect ledger. Future extraction acceptance
must show unchanged command/effect order, not just fewer runtime lines.
For an affected path, record: pre-effect proof, WAL transition (if any), Git
effect, post-effect observation, and crash-replay behavior. Direct command
count is insufficient because `fetch` mutates locally and Git may invoke
remote transports, hooks, or recursive submodule work.

Existing focused regression evidence includes:

- Cleanup crash-boundary convergence and changed-ref refusal:
  `tests/test_control_workspace.py:782-924`.
- Cleanup cohort/admission and rework preparation coverage:
  `tests/test_control_workspace.py:546-602,2020,2745-2813,3147-3219`.
- Accepted/done cleanup process-loss recovery:
  `tests/test_control_cross_layer.py:132-191`.
- Checkpoint/publication replay and stale-product refusal:
  `tests/test_control_recovery.py:955-1243`.
- Stranded-worker/recovery transaction boundaries:
  `tests/test_recovery_boundaries.py:117-149,311-470`.
- Reconciliation local-pending, lease drift, and published rewrite evidence:
  `tests/test_control_cross_layer.py:655-822`.
- Git proof semantics and guarded ref mutation:
  `tests/test_git.py:13-105,177-240,339-454`.
- Control workflow ordering and no retry of an interrupted merge:
  `tests/test_control_git_workflow.py:54-287`.

Narrow future tests, rather than a broad E2E duplication, should cover: one
normal-path reconciliation persistence call through the shared routine; each
cleanup executor stage after a changed registration/ref; normal and replay
checkpoint/publication effect ledgers; and crash replay at every newly moved
WAL/effect boundary. A protocol conformance/static test is needed if seam 3
adds capabilities: the current workflow implementation calls
`recovery_checkpoint_exact`, `recovery_persist_reconciliation`, and
`recovery_report_matches_state` without declaring them in
`WorkflowRecoveryCapabilities` (`recovery_workflow.py:122,159,250`).

## Risks and open questions for reviewer-directed iteration

### Observed risks

- Cosmetic forwarding wrappers would add import edges and Protocol methods
  without moving an ownership boundary. The first seam should remain a
  private runtime helper call unless a reusable data contract is proven.
- Recovery capability protocols can drift from implementations; the three
  undeclared calls cited above demonstrate the risk of excessive Protocol
  growth.
- `reconciliation_transactions.py` directly reads `owner._state` and calls
  `owner._save_state` (`295-332,338,453,523,577-589`). This weakens its stated
  capability boundary and is a circular-import/hidden-state-machine risk.
- `run_git` has no timeout, process-group management, prompt suppression, or
  sanitized Git environment (`git.py:31-49`). Remote helpers, hooks, and
  recursive submodules can create hidden subprocess effects outside a simple
  argv inventory.
- Some runtime cleanup/publication effects call `_git` directly rather than
  the owner/shutdown wrapper: remote/local deletion at `runtime.py:8114-8122,
  8147-8151` and publication at `6273-6289`; `_git_runtime` is at 2881-2891.
  This is an audit finding, not authorization to change it here.
- `ExecutionWorkspaceManager` defaults to `run_git` even though `prepare`,
  `retire`, and `checkpoint` can mutate (`execution_workspace.py:64-92`). An
  extraction must keep explicit runner injection and not make this default a
  service-side escape hatch.

### Not yet known

This first bounded pass has not established the exact normal-versus-recovery
exception and shutdown propagation for seam 3, nor whether cleanup's three
paths have a common enough partial-failure result to share safely. It has not
measured import cycles for a proposed state-schema module, and has not
performed a command-by-command before/after effect account because there is
no implementation proposal yet. It also has not determined whether the
direct `_git` calls are intentional exceptions to the inventory's runner
contract or a defect.

The highest-value next question is: map the normal and recovery
checkpoint-to-lifecycle control/data flow, including every state write,
runner call, exception translation, and shutdown check, then decide whether
a single explicit sequence can preserve the stricter replay proofs. A second
valuable alternative is to map cleanup ownership races from cohort admission
through every destructive stage and decide whether a proof-result executor
has a real contract. No runtime refactor is justified from this report alone.

## Version 2: cleanup post-proof executor investigation

**Scope and snapshot.** This is the reviewer-directed second pass. It answers
only whether zero-delta retirement, accepted-integration retirement, and
generic cleanup can share post-admission destructive choreography. No runtime
code was changed. Sources and tests were re-read at workspace HEAD
`4ecb6d1c39214380c0b82f6b4cc225810a15d514` (`TASK-105: Iteratively audit
runtime decomposition and DRY boundaries before final Git story acceptance`).
The prior Version 1 snapshot remains historical evidence; its reported
integrated product HEAD was `295b7f04366979567090cc2bbe32a4cae0bb926a`.
Line references in this version are measured at the Version 2 snapshot.

### Actual cleanup flows

The shared calls do not establish a common transaction. In each case,
`ExecutionWorkspaceManager.retire` owns only a validated, clean worktree
removal (`src/devlegate/execution_workspace.py:383-429`), and
`_retire_execution_refs` owns the local compare-and-delete plus remote
lease-delete sequence (`src/devlegate/runtime.py:6690-6818`). Neither owns
admission or its durable consequence.

|  | Zero-delta retry | Accepted integration | Generic cleanup |
| --- | --- | --- | --- |
| Caller and entry | `_complete_execution_lifecycle` dispatches to `_resolve_zero_delta_retry` when a pending retry exists (`runtime.py:5371-5389`); it invokes retirement at `5496`. | The accepted-integration path records `_pending_integrated_cleanup` after `_complete_accepted` (`runtime.py:6578-6590`), then `_finish_integrated_workspace_cleanup` invokes it (`6592-6630`). Restart instead enters accepted-integration recovery before idle cleanup (`3929-3939`). | Operator/admitted command supplies its immutable cohort, or the idle backlog calls `_reconcile_workspace_backlog` then `cleanup_workspaces` (`runtime.py:3938-3939,5194-5280,7176-7350`). |
| Precondition and durable stage | `automatic_retry` must be pending and exactly bind ticket, stale execution, base, and checkpoint (`5467-5487`). Historical report persistence and current product-drift proof happen before retirement (`5481-5496`). The state remains an active lifecycle/automatic-retry record until `_finalize_execution_lifecycle` after retirement (`5508-5536`). | Product/control publication and the accepted-to-done transition must already be complete; cleanup is deliberately optional after that durable publication (`6592-6629`). `_retire_integrated_workspace` proves exact accepted provenance and requires ticket `done` (`6631-6664`). | Admission bounds `targets` to the receipt cohort and `limit`; it rejects malformed target identities before effects (`7183-7249`). Each target must now be `done`, correctly bound to its canonical path/branch/SHA, published, and not resource-live (`7099-7148`). |
| Worktree registration/topology and local SHA proof | Constructs manager with the report ticket and injected owner runner, reads registrations, validates the canonical registration at `checkpoint`, or refuses an extant/branch-attached ambiguous path (`5437-5465`). `retire` itself rechecks path, branch, clean status, symlinks, registrations, submodules, and expected HEAD (`execution_workspace.py:383-429`). | Binds report path and branch exactly to the canonical manager (`6654-6663`), then requires a registration or proves nothing ambiguous remains (`6665-6679`). Before removal it repeats accepted evidence, ref, product, dependency, and registration proof (`6680-6684,7062-7097`); `retire` repeats its own local worktree proof. | `_observe_cleanup_candidate` proves canonical target binding, ticket state, registration head/branch, clean worktree, submodules, exact local/remote ref heads, no dependency, and published ancestry (`7099-7148`). Apply repeats the complete observation immediately before worktree removal and again before ref retirement (`7259-7270`). |
| Effects and ordering | Remove worktree if registered; then remove local ref if it still equals checkpoint and is unattached; then observe remote SHA and remove it with an expected-SHA lease (`5453-5465,6690-6818`). There is no generic-candidate re-observation between worktree and ref removal. | After the pre-removal revalidation, remove worktree, then use `_retire_execution_refs` with the report proof mode (`6683-6687`). That method repeats `_revalidate_cleanup_retirement` immediately before local deletion and again before remote deletion, then repeats remote observation before the lease delete (`6728-6818`). | If present, remove worktree only after a fresh observation; re-observe after removal; call `_retire_execution_refs` in candidate proof mode. That method re-observes the candidate after local deletion and again before remote lease delete (`7260-7270,6733-6789`). |
| Durable record and retry | No cleanup-specific durable completion is written between effects. A crash preserves the pending retry/lifecycle state; replay re-enters the same retirement before finalizing lifecycle. The focused crash-before-retirement test proves no duplicate admission and later fresh admission (`tests/test_control_recovery.py:1610-1657`). | No per-stage durable record is written. Success/blocked result becomes the in-memory `workspace_cleanup_event`; a cleanup failure cannot recreate pending accepted integration (`6599-6629`). A process loss before retirement leaves published done resources for startup/backlog discovery (`tests/test_control_cross_layer.py:194-257`); a loss before state clear instead replays accepted integration first (`132-191`). | Per-target result contains resource observations after error, but is returned/status-published rather than a per-effect WAL transition (`7289-7350`). Re-entry reconstructs/adopts a new bounded cohort and treats absent worktree/local ref as a valid partial completion only after fresh proof. Crash convergence at each destructive boundary is covered by `tests/test_control_workspace.py:774-859`. |
| Intentional absence | No `done`-ticket requirement, accepted-integration evidence, `_assert_cleanup_has_no_execution_dependency`, generic product fetch, or per-resource result. Those would reject the retry that this flow is still resolving. | No arbitrary cohort, dry-run, or partial-resource response. The exact accepted report and integration commit are stronger than generic current-state eligibility. | No accepted-report requirement and no zero-delta retry lifecycle transition. It permits a historical `done` resource without a report when current Git ownership/product ancestry prove it (`tests/test_control_workspace.py:681-760`). |

### Freshness and authority boundary

The minimum useful immutable input is not a `CleanupTarget` alone. A possible
internal effect-only input would be:

```
RetirementEffectsInput(
    ticket_id, branch, canonical_path, expected_head,
    worktree_present_at_last_proof,
    proof_mode,  # zero_delta | accepted_report | generic_candidate
)
```

It would need an owner-supplied `reobserve_before_worktree()` and
`reobserve_before_ref_stage()` that either return an exact current binding or
raise `WorkflowBlockedError`. Its output could only be immutable resource
facts: `worktree_absent`, `local_ref_absent`, `remote_ref_absent`, or a
blocked reason. It must not return permission to retry a remote deletion.

That contract is insufficient as a standalone reusable executor today. The
fresh proofs cannot be captured at admission:

- The worktree registration, canonical path, clean/dirty state, symlink
  checks, submodules, and local expected SHA must be read again immediately
  before `retire` (`execution_workspace.py:383-417`). A changed/moved or
  symlinked registration refuses removal.
- Local ref occupancy and expected SHA must be observed immediately before
  `delete_branch_if_unoccupied`; the helper independently prevents a race
  with a foreign worktree (`runtime.py:6713-6754`).
- Remote ref must still equal `expected_head` immediately before the
  `--force-with-lease` deletion; after a failed push the code observes remote
  state and only accepts an already-absent ref (`6761-6818`). A present,
  changed remote ref is a refusal, not a retry.
- Generic candidate proof must freshly fetch and prove the checkpoint is in
  published product (`7147-7174`). Accepted cleanup instead freshly proves
  the exact accepted report/integration commit and published checkpoint
  (`7068-7091`). Zero-delta instead freshly proves its active retry's product
  drift before starting (`5481-5487`). These are not interchangeable proof
  callbacks.

`ServiceEngine` must retain the owner lock/serialized runner, cohort scope,
ticket-store reads, `_state` and WAL transitions, and all dependency policy.
In particular, `_assert_cleanup_has_no_execution_dependency` remains the
engine authority for active worker, reconciliation, automatic-retry, and
accepted-integration bindings (`6838-7060`). Passing it as a generic callback
would obscure that zero-delta intentionally cannot use it while its own retry
is live. A shutdown between effects has the same consequence as another
exception: stop the sequence, leave observed resources intact, and require
fresh re-entry proof. No path should cache a remote/ref/registration proof
across an effect.

### Extraction decision

| Common part | Decision | Evidence and consequence |
| --- | --- | --- |
| Validated worktree removal | **KEEP SEPARATE** at orchestration level; retain existing `ExecutionWorkspaceManager.retire`. | The lower-level worktree transaction is already shared. Moving its callers into a new executor would delete only the two calls at `runtime.py:5453-5454` and `6683-6684` plus the guarded generic call at `7262-7267`, while introducing proof-mode adapters that conceal materially different authority. |
| Local/remote ref deletion | **LOCAL-DEDUP ONLY**. | `_retire_execution_refs` is already the shared executor and supplies occupancy, CAS, lease, and post-effect observations. Its `report` and `candidate` branches show that proof is caller-specific (`6690-6818`); extracting a second wrapper would be cosmetic. No new Protocol is justified. |
| Combined post-admission choreography and partial-result handling | **KEEP SEPARATE**. | Generic cleanup owns a user-visible per-resource partial result; integrated cleanup owns optional best-effort logging/event state; zero-delta failure blocks an active lifecycle and must replay before finalization. A single result adapter would need state mutation and three policy modes, increasing WAL/coupling rather than reducing it. |

Therefore no new adapter or Protocol is recommended. The only concrete code
that a composite executor could remove is the short repeated call ordering
listed above; it would add proof callbacks, result translation, and a new
cross-module import edge. That is net coupling, not a defensible ownership
boundary. The existing two primitives are the appropriate local deduplication
level.

### Provisional negative regression matrix

These are required invariants for any future narrow extraction ticket. They
are negative tests: refusal retains resources and evidence rather than
continuing with a weaker proof.

| Condition | Required outcome | Existing evidence / future focused coverage |
| --- | --- | --- |
| Local deletion blocked or CAS ref move | Keep local ref; do not attempt an unproven remote deletion. | Covered by `tests/test_control_workspace.py:192-213,237-313`; retain for every caller proof mode if code moves. |
| Worktree occupied by foreign registration | Keep ref and foreign worktree; no forced deletion. | Covered before and during CAS by `tests/test_control_workspace.py:215-235,316-353`. |
| Dirty, moved, or symlinked worktree | Refuse worktree effect and retain refs; never traverse the substituted path. | Generic clean/binding proof is `runtime.py:7117-7137`; root symlink refusal is tested at `tests/test_control_workspace.py:655-679`. Add focused accepted and zero-delta tests if their orchestration is touched. |
| Remote drift or lease race | Leave changed remote ref; no blind second delete. Local deletion may already be complete, and later re-entry must re-prove the remote ref. | Covered by `tests/test_control_workspace.py:356-485` and restart changed-ref refusal at `861-924`. |
| Interrupted deletion after worktree, local ref, or remote ref | Treat already-absent resources as partial completion only after fresh ownership/provenance proof; converge without expanding cohort. | Covered for generic cleanup at `tests/test_control_workspace.py:774-859`. Keep accepted process-loss recovery at `tests/test_control_cross_layer.py:132-257`; add direct zero-delta boundary tests only if this sequence changes. |
| Already-completed partial cleanup | Do not infer success from absence alone when a conflicting registration/ref or dependency remains. Re-observe ticket state, topology, SHA, publication, and mode-specific durable evidence. | Generic candidate re-entry proof is `runtime.py:7119-7148`; accepted revalidation is `7062-7097`. A future test should exercise the exact proposed executor input with an absent worktree and changed remote ref. |

### Risks and remaining questions

The Version 1 direct `runner=_git` anomalies remain observable technical debt,
not scope for this audit: accepted product synchronization uses `_git` at
`runtime.py:6566-6573`, and other cleanup/publication direct-runner findings
remain recorded at Version 1:213-216. A cleanup adapter must not become a
route around the owner/shutdown runner.

The material unknown is whether an interruption request can be observed
between each cleanup effect under every host mode and, if so, which existing
owner boundary records or translates it. This pass did not map that broader
shutdown propagation because the requested question was post-proof effect
sharing. Also unmeasured are the exact command/effect ledger for each path
under a hypothetical extraction; any implementation ticket must demonstrate
unchanged `retire`, local CAS delete, remote lease delete, and re-observation
order. No further cleanup composite-executor study is recommended unless a
reviewer identifies a concrete new common durable contract.

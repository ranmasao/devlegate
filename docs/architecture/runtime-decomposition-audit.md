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

## Version 3: checkpoint, publication, and lifecycle boundary

**Scope and snapshot.** This reviewer-directed pass investigates only the
execution checkpoint through execution-branch publication and lifecycle
transition, including recovery of `checkpointing`, `post-checkpoint`,
`publishing`, and `post-publication`. No production code changed. Observations
are from workspace/product checkpoint
`b3380f12924f7656f4d94c3a7ad249c8bf11bb4b` (`TASK-105: Iteratively audit
runtime decomposition and DRY boundaries before final Git story acceptance`).
The Version 1 and Version 2 snapshots remain historical evidence. Source line
references in this version are measured at this checkpoint.

### Flow ownership and durable boundary

`ServiceEngine._run_iteration_body` owns the normal execution tail. After the
worker group's retirement and submodule proof, it creates an in-memory report,
then makes the first durable boundary before the checkpoint effect
(`runtime.py:4961-5009`). The normal path writes each subsequent WAL stage
itself at `5004-5031` and `5151-5170`. It alone has the live `worker_run`, the
service lifecycle drain decision, and the normal return/error mapping
(`5038-5046,5153-5175,5179-5192`).

`recovery_workflow.recover_checkpoint_publication` owns replay sequencing,
not the state store or Git runner (`recovery_workflow.py:4-9,72-208`).
`ServiceEngine` supplies observations, state writes, workspace manager, and
effects through recovery capabilities (`runtime.py:10339-10461`). Startup
dispatch chooses that workflow only for the `checkpoint-publication` stage
class, then separately proves the lifecycle report and calls the engine's
lifecycle coordinator (`runtime.py:3949-3957`). The small engine wrappers only
map `WorkflowRecoveryError` to `WorkflowBlockedError`
(`runtime.py:8616-8626`). This is a meaningful existing domain boundary, not a
second implementation of normal scheduling.

`ExecutionWorkspaceManager.checkpoint` owns the local worktree checkpoint
effect: validate binding, verify its initial HEAD, stage, detect a delta,
optionally commit, and return the final HEAD
(`execution_workspace.py:584-621`). `_publish_execution_branch` owns one
execution-ref publication attempt: re-observe workspace HEAD and remote tip,
prove predecessor ancestry, lease-push, then resolve an ambiguous failed push
(`runtime.py:6243-6289`). `_apply_execution_lifecycle` remains an engine-owned
control-worktree transaction: it proves the published execution report,
fetches/reproves control state, commits the lifecycle transition, and retries a
racing control push at most three times (`runtime.py:8811-8960`).

### Normal and replay state/effect matrix

"Evidence" below is the smallest durable or re-observable fact that permits
the next action. It is not a claim that a state record alone proves a Git
effect. Every replay begins under the serialized owner path; the source does
not give `recovery_workflow` an independent lock or runner.

| Stage | Normal flow: WAL before effect and effect | Recovery/re-entry proof and action | Shared and deliberately different parts | Minimum completion evidence / new observation after ambiguity |
| --- | --- | --- | --- |
| Post-worker admission | `agent_running/checkpointing` stores the report with no workspace head before checkpoint (`5004-5008`). The worker group must already be retired and submodules verified (`4947-4989`). | Startup dispatch never resumes a live/unknown worker through this workflow; stage classification reaches it only after stranded-execution handling (`3949-3988`; `recovery_transactions.py:116-162`). | Both require report-to-state identity later. Only normal has a live worker return, its interruption kind, and a shutdown drain decision. | Durable execution binding plus pending report; recovery freshly validates the workspace and report binding (`recovery_workflow.py:74-88`). |
| Checkpoint | `checkpointing` precedes `manager.checkpoint`; on success `post-checkpoint` persists report `workspace_head` (`5011-5031`). Manager may run `add -A` and `commit` after checking original HEAD. | If HEAD is still `execution_start_head`, recovery first proves the admitted product generation, then may checkpoint. Otherwise it requires exactly one clean, single-parent checkpoint directly on start head (`100-138`; `runtime.py:8594-8614`). | The manager effect and resulting report field can be shared. Recovery's product proof and exact-history gate are necessarily stricter because a crash makes the commit outcome ambiguous. | `post-checkpoint` report with exact checkpoint; if the stage write crashed, re-observe clean workspace HEAD and prove either no commit or exactly the one allowed checkpoint. |
| Product generation decision | Normal observes product after checkpoint. Unobservable/unsafe-local returns blocked without publication; changed history either records zero-delta lifecycle retry, accepts a safe descendant, or pins reconciliation evidence and saves `idle/reconciliation` (`5047-5150`). | Replay observes product at `post-checkpoint` and at the continuation point. It turns zero delta into lifecycle retry, persists reconciliation through the engine capability for changed history, and raises a blocked recovery error (`140-164`). | Observation and reconciliation persistence should be shared through `_observe_product_generation` and `_persist_product_reconciliation`. Normal's safe-running-descendant exception is a worker-time policy branch, not a replay shortcut. | Reconciliation requires the checkpoint evidence ref to resolve to the report checkpoint before `idle` state is saved (`runtime.py:8402-8460`). After interruption, fetch/re-observe local and remote product identity; no cached pre-checkpoint observation authorizes publication. |
| Execution-branch publication | `agent_running/publishing` is durable before `_publish_execution_branch`; success records `post-publication` (`5151-5164`). | From `post-checkpoint` or `publishing`, recovery requires clean workspace HEAD equal to report checkpoint, re-observes the execution remote, pushes only when it still equals the persisted predecessor, then re-observes exact remote checkpoint before recording `post-publication` (`165-190`). | The same publish helper performs both lease push and ambiguous-success handling. Recovery must additionally prove the report/workspace identity before deciding whether to push. | The remote execution ref exactly equals `report.workspace_head`. A failed push is accepted only if fresh remote observation already equals that checkpoint; predecessor equality means the effect did not complete and is an error/retry, while any third value is drift (`6249-6289`). |
| Lifecycle admission | Normal writes `agent_running/lifecycle`, including report and worker interruption kind, only after the execution ref is published (`5165-5170`). It then applies lifecycle and finalizes state (`5173-5177`). | `post-publication` replay rechecks workspace and remote exactness, writes `lifecycle`, then `_recover_lifecycle_report` validates report/state before `_complete_execution_lifecycle` (`191-207`; `runtime.py:3955-3957`). | Both use the same engine lifecycle operation and finalizer. Recovery must rebuild/validate report evidence if the pending report is absent and reprove publication before allowing control effects. | Durable lifecycle stage plus report binding and exact remote checkpoint. An ambiguous control push is handled inside `_apply_execution_lifecycle` by fresh control fetch, existing lifecycle-commit proof, and bounded retry (`8824-8959`). |
| Final lifecycle state | `_finalize_execution_lifecycle` computes failure/interruption metadata, todo fingerprint, retry completion, and performs the final `idle` save (`5543-5644`). | Recovery calls the same engine finalizer through `recovery_finalize_lifecycle` after report proof and lifecycle completion (`recovery_workflow.py:210-251`; `runtime.py:10417-10423`). | This is shared engine policy, not a candidate recovery-owned state machine. It maps `operator_abort` and `service_shutdown` to durable interrupted failure only when the report conclusion is failed (`5568-5604`). | The control lifecycle commit must be exact or recovered from the report store; re-entry freshly observes product/control before accepting report state (`recovery_workflow.py:226-251`). |

### Lease, runner, shutdown, and effect accounting

The normal execution publication order is: persist `publishing`; resolve local
execution HEAD; observe remote execution ref; optionally prove ancestry;
`push --force-with-lease`; and, only on a nonzero push, re-observe the remote
ref (`runtime.py:5151-5163,6249-6289`). The helper's result cases are precise:

- Remote equals the expected predecessor before push and equals checkpoint
  after an unsuccessful push: ambiguous success is accepted.
- Remote still equals predecessor after an unsuccessful push: publication is
  not inferred; the helper raises its transport error.
- Remote differs from both: lease/drift is a refusal, not a retry.

Recovery adds an exact remote observation before deciding to invoke that
helper and another after it (`recovery_workflow.py:165-190`). Thus the durable
`publishing` stage is intent, never proof that the push happened. The
`post-publication` record is written only after the exact remote observation.
This preserves WAL-before-effect and gives a fresh observation for every
ambiguous push outcome.

Known command/effect accounting is bounded by existing source inventory, not
a new instrumented trace. `checkpoint` executes `rev-parse`, `add -A`,
`diff --cached --quiet`, and final `rev-parse`; a nonempty delta additionally
executes `commit` (`GIT_COMMAND_INVENTORY.md:183`). Publication's documented
operation subset is `R, R, R, P, R` and remains unchanged
(`GIT_COMMAND_INVENTORY.md:71`): helper-internal observations include local
HEAD, remote ref, and optional ancestry; `P` is the leased remote effect; the
last read occurs only on a nonzero push. Product observation includes a
state-changing `fetch`, rather than a read-only proof
(`runtime.py:8170-8232`; `GIT_COMMAND_INVENTORY.md:3-7,25`). Lifecycle adds a
control fetch, possibly ancestry/reset/fast-forward/commit effects, and an
unleased control push with its bounded reconciliation loop
(`runtime.py:8824-8959`). Any extraction must retain this order and these
conditional calls, including the helper-internal re-observations.

There is an actual runner-authority anomaly, separate from a hypothetical
extraction risk. `_publish_execution_branch` passes `runner=_git` to both
`is_ancestor` and `push_refs` (`6259-6279`), even though its caller constructs
the workspace manager with `_git_runtime` (`4961-4967`) and the documented
runner seam is meant to enforce owner thread and shutdown policy
(`GIT_COMMAND_INVENTORY.md:33-36`). Recovery calls the same helper through
`recovery_publish_execution` (`runtime.py:10389-10392`), so it inherits the
same bypass. Direct `_git` also remains in normal/replay reconciliation
evidence pinning (`5097-5105,8423-8428`) and lifecycle effects
(`8829-8835,8947-8952`). These are current-baseline audit findings; this
docs-only ticket neither changes them nor asserts that every one is unsafe.
An implementation ticket must classify each call by the exact owner-thread and
shutdown behavior before replacement.

Normal shutdown is explicitly observed after checkpoint and before product
observation (`5038-5046`), so a drain leaves `post-checkpoint` durable for
re-entry. It is not checked by the direct publication helper itself. Recovery
dispatch returns early if stop is already requested (`3900-3901,3950-3951`),
but a stop arriving between direct helper subprocesses is not separately
persisted there. This is a test gap and a reason not to move this sequence
behind another callback layer.

### Architecture decision: KEEP SEPARATE

**Decision: KEEP SEPARATE** for the normal checkpoint-to-lifecycle orchestration
and crash replay. Retain `recover_checkpoint_publication` as the existing
meaningful replay transaction. It already removes recovery-only stage dispatch
and sequencing from the engine while keeping owner mutation authority in
`ServiceEngine`.

Extracting a common new transaction would not remove one coherent normal
block. The proposed caller would have to provide, at minimum, immutable
`ExecutionCheckpointPublicationInput` containing execution/report binding,
start/checkpoint heads, branch/path, expected remote predecessor, product
generation, and interruption state; mutable callbacks for state saves,
workspace validation/checkpoint, product/reconciliation decisions, publication,
lifecycle, status refresh, shutdown, and exception translation; and a result
that represents `blocked`, `reconciliation`, `zero_delta_retry`, `published`,
or `lifecycle_complete`. That is a parallel state machine rather than a
transaction boundary. It would remove normal blocks around
`runtime.py:5004-5177` but introduce a wide protocol beyond the existing
recovery capabilities (`recovery_workflow.py:27-69`) and obscure which caller
owns every `_state` mutation.

The limited local deduplication already justified by evidence is unchanged:
normal reconciliation persistence at `runtime.py:5094-5137` should eventually
call `_persist_product_reconciliation` (`8402-8460`) in a narrow separate
ticket. That preserves the evidence-pin-before-`_save_state` invariant without
turning normal scheduling into a recovery callback. It does not justify moving
checkpoint/publication sequencing.

The current protocol also demonstrates the growth risk: the workflow invokes
`recovery_checkpoint_exact`, `recovery_persist_reconciliation`, and
`recovery_report_matches_state` (`recovery_workflow.py:122,159,250`), but
they are absent from `WorkflowRecoveryCapabilities` (`27-69`) despite being
implemented by the engine (`runtime.py:10359-10406`). Adding a shared normal
transaction before repairing/validating this declared boundary would compound
unchecked capability drift and potential circular imports.

### Tests, gaps, and finite implementation shortlist

Existing focused regressions cover checkpoint recreation without rerunning a
worker (`tests/test_control_recovery.py:955-994`), exact-versus-dirty checkpoint
re-entry (`996-1058`), publication recovery without a worker (`1060-1100`),
stale product refusal before recovery side effects (`1153-1201`), and recovery
from a crash after the remote publication but before the WAL transition
(`1203-1243`). The normal/recovery product-drift and retained reconciliation
paths are also covered at `tests/test_control_recovery.py:1422-1564` and
`tests/test_control_cross_layer.py:564-649`. Reconciliation lease drift is
covered separately at `tests/test_control_cross_layer.py:702-747`; it is not
direct coverage of ordinary checkpoint publication.

Future narrow tickets need only targeted cases, not another end-to-end suite:

- Crash immediately after checkpoint effect and before `post-checkpoint` save,
  with a changed product remote, must prove the exact checkpoint or refuse.
- Product drift after `post-checkpoint` must pin the evidence ref before saving
  reconciliation; add a direct assertion through the shared persistence helper
  if the Version 1 local dedup is implemented.
- Evidence/ref publication must not occur after an unproven changed execution
  remote. Add ordinary `_publish_execution_branch` lease-drift and ambiguous
  successful-push tests; the current explicit lease test is only for rewrite.
- A changed execution remote between recovery's observation and push must
  remain a failed-closed `publishing` replay, with no lifecycle transition.
- A shutdown requested immediately before and during publication must leave a
  replayable stage and must not cause an unowned subprocess; this requires a
  defined expectation for the direct-runner calls before testing it.
- Protocol conformance/static coverage must detect recovery methods called but
  omitted from `WorkflowRecoveryCapabilities`.

Ranked finite opportunities after three passes are:

| Rank | Narrow scope | Benefit / risk / dependency | Acceptance accounting |
| --- | --- | --- | --- |
| 1 | Make normal changed-product handling call `_persist_product_reconciliation`. | Removes demonstrated same-owner duplicated state/evidence choreography. Low risk, independent of checkpoint replay. | Same evidence ref, `update-ref`, reconciliation payload, and one final `_save_state`; targeted evidence-before-state and drift tests. |
| 2 | Reconcile `WorkflowRecoveryCapabilities` with its actual calls and add static/conformance coverage. | Makes the existing replay ownership contract reviewable. Moderate protocol-growth risk; do not add normal-flow callbacks. | No Git/state ordering change; type/static check proves every invoked capability is declared. |
| 3 | Audit and, only where justified, route checkpoint-publication direct `_git` calls through the owner/shutdown runner. | Addresses an observed authorization-bypass candidate without changing transaction ownership. Higher risk because interruption timing changes behavior; depends on an explicit per-call shutdown contract. | Instrument normal and replay command order, including failed push re-observation and lease outcomes; preserve all WAL stages and exact remote proofs. |

No composite checkpoint/publication extraction is recommended. Keep the normal
scheduler, state-save authority, lifecycle finalization, and exception/shutdown
translation in `ServiceEngine`; keep crash replay sequencing in
`recovery_workflow`; keep local checkpoint and execution-ref effect semantics
in their present domain owners. The remaining uncertainty is the intended
shutdown behavior for every direct `_git` call and whether an ordinary
execution-branch push needs more explicit ambiguous-success coverage. Those
questions should gate only the ranked narrow tickets, not another broad
decomposition study.

## Version 4: internal scheduler and state-proof structure

**Scope and snapshot.** This reviewer-directed pass answers only whether the
internal contiguous regions of `_run_iteration_body` and
`_validate_state_invariant` have cohesive extraction boundaries. No production
code changed. Observations are from the current workspace/product checkpoint
`4ff27df00224bc1495451552322fbf76537212c6` (`TASK-105: Iteratively audit
runtime decomposition and DRY boundaries before final Git story acceptance`).
Line references and physical LOC below are measured at this checkpoint.
Versions 1-3, including the **KEEP SEPARATE** decision for normal versus replay
checkpoint/publication state machines, remain valid.

### `_run_iteration_body`: actual contiguous regions

The method is 1,305 physical LOC (`src/devlegate/runtime.py:3888-5192`). It is
one scheduler authority, but not one indivisible operation. The table divides
it at actual returns, durable transitions, and capability boundaries rather
than by superficial local variables.

| Range (physical LOC) | Entry and exit behavior | State/lock/effect dependencies | Ownership conclusion |
| --- | --- | --- | --- |
| `3889-3918` (30) | Publishes `processing`, restores an admitted forced retry, refuses a stop, or resolves a persisted unsafe workspace and returns `0`. | Reads `force_retry_authorization`, `phase`, `operator_recovery`; publishes status; invokes recovery effect path. Owner serialization is supplied by the caller of `run_iteration`, not this function. | Essential scheduler pre-dispatch. Keep in place. A result callback would hide the distinction between stop and unsafe-workspace recovery. |
| `3919-3993` (75) | Proves product checkout branch, dispatches accepted-integration/reconciliation/checkpoint-publication/lifecycle recovery, or converts a stranded execution into automatic authorization. Every recovery branch returns before ordinary planning. | Reads `phase`, accepted/reconciliation/WAL stage and worker identity; invokes engine recovery/lifecycle methods and backlog cleanup. | Essential lifecycle dispatch. Keep in place: it is the point that chooses normal work versus the stricter replay state machine. |
| `3994-4206` (213) | Refuses dirty product state, observes/synchronizes control and product, resumes a pending merge, or returns `0/1` on stop, fetch, merge, or topology failure. | Product/control Git observations and effects through `_git_runtime`; `_save_state` writes `merge_pending`, `agent_pending`, or `idle`; stop gates occur before effects. | Owner synchronization policy. The repeated pending/non-pending fast-forward branches share primitives but have different persisted lineage checks (`4035-4151` versus `4152-4204`). Do not fold them into a generic merge helper. |
| `4207-4320` (114) | Loads tickets/context, creates a stable execution plan, repairs a recoverable bound workspace once, publishes projections, and applies retry authorization only after fresh observations. | Ticket store, project-context observation, plan/worker ownership policy, status publication; no new worker or workspace effect. | Plan/admission coordination. Keep in place; `_make_execution_plan` is already the read-only policy capability. |
| `4321-4411` (91) | Rejects blocked plans; completes a pending accepted integration or begins and completes a newly selected accepted integration, then returns `0`. | `accepted_integration` WAL, `_integrate_accepted` multi-worktree effect, ticket/control/product proofs, cleanup event and status effects. | Owner-only accepted-publication dispatch. Its two paths are intentionally different: the first validates persisted recovery evidence (`4331-4359`), the second first writes durable intent (`4375-4388`). |
| `4412-4792` (381) | Selects a ticket, refuses stale/unauthorized retries, binds rework feedback, allocates execution identity, then saves `agent_pending`; returns on no work or stop. | Ticket store, report store, review provenance, retry/failed-execution fields, all admission identities, `_save_state`; Git proof only while resolving prior rework lineage. | Essential policy and WAL admission. It is too coupled for a narrow extraction: a proposed input would contain plan, snapshots, authorization, report/review history, and mutable state, recreating an admission state machine. |
| `4793-4905` (113) | Prepares the execution worktree, decides fresh/rework/resume directive, restores review content, constructs/persists the prompt artifact, and stops before `agent_running`. | `ExecutionWorkspaceManager` through `_prepare_execution_workspace`, persisted execution/rework fields, prompt filesystem artifact, stop gate. | A cohesive pre-worker setup region, but leave in the coordinator initially. Splitting it would require a large mutable context merely to return workspace, prompt, directive, artifact, and execution identity. |
| `4906-5037` (132) | Durably enters `worker-launch`, runs one worker, always publishes post-worker status, proves worker-group retirement and submodules, builds a report, writes `checkpointing`, checkpoints, then writes `post-checkpoint`. `WorkerAdmissionClosed` returns `0`; post-worker integrity errors record failure then raise. | `_state` stages/worker identity/interruption field; worker-supervisor effect; prompt-delivery artifact callback; workspace Git checkpoint effect via manager; status publication. No new lock is acquired. | Cohesive inner operation. A small engine-private method is viable because it has one caller and its effects remain under engine methods and the injected manager runner. |
| `5038-5150` (113) | Honors lifecycle drain after checkpoint; observes product and either returns blocked, records a zero-delta lifecycle retry, permits the safe descendant, or pins reconciliation evidence and returns blocked. | `post-checkpoint` WAL/report, product fetch observation, evidence-ref mutation, reconciliation state write, lifecycle/status publication. | Mixed but deliberately ordered product-decision policy. The inlined evidence/state sequence duplicates `_persist_product_reconciliation` (`5094-5138` and `8402-8460`); only that local deduplication is justified. |
| `5151-5192` (42) | Writes publishing intent, publishes execution ref, writes post-publication/lifecycle, applies control lifecycle, finalizes, logs, and maps result to `0/1`. | WAL stages, execution-ref Git effect, control lifecycle effects, final state and snapshot. | Essential ordered tail. Keep in place with the Version 3 normal/replay separation; this is not a standalone transaction without a parallel state machine. |

The visibly repeated operations inside the method are not automatically
interchangeable. The two accepted-integration tails at `4357-4374` and
`4390-4411` both call integration/save/cleanup, but only the latter has to
make accepted intent durable first. The two plan calculations at `4214-4239`
and `4248-4273` are a retry after a specific repair, not duplicated planning
policy. The normal reconciliation sequence at `5094-5138` and
`_persist_product_reconciliation` at `8402-8460` is the demonstrated same
proof/evidence/state choreography: both reject a mismatched evidence ref, pin
the checkpoint with `update-ref`, construct pending reconciliation, then save
idle state. That remains the only direct internal duplication conclusion.

#### Private worker-through-checkpoint seam

`4906-5037` can become a private engine method such as
`_run_worker_through_checkpoint(...) -> WorkerCheckpointOutcome`. Its minimum
explicit inputs are the prepared workspace, selected ticket, execution ID,
prompt, prompt artifact, initial product/remote heads, and observed execution
remote head. Its typed result needs `report`, `worker_run`, `manager`, and
`checkpoint`; it must return the `WorkerAdmissionClosed` outcome distinctly so
the caller preserves its `0` result. It must re-raise the current
`DevlegateError` wrapping for checkpoint errors and must retain the
post-worker-failure recording behavior for submodule/integrity errors.

This would add one engine-private call edge and no protocol/import edge. It
would contain state mutation and worker/Git effects, but only through existing
engine authority and `ExecutionWorkspaceManager(..., runner=self._git_runtime)`
(`4961-4968`). It must not write later product/reconciliation/publication
state, fetch product, or choose lifecycle policy. The caller must keep the
ordering `agent_running/worker-launch` -> worker group retired ->
`post-worker` -> submodule proof -> `checkpointing` -> checkpoint ->
`post-checkpoint`, and retain its snapshot refreshes. This is a structural
seam, not a new transaction owner.

### `_validate_state_invariant`: proof groups and dependencies

The validator is 448 physical LOC (`runtime.py:3267-3714`). It has no Git,
workspace, lock, runner, `_save_state`, or store effect. It is called before a
loaded state is accepted (`3263`), before `_save_state` replaces the store
(`3777-3782`), and for a fresh status-store observation (`9334-9343`). Thus its
only authority is rejection; transition and persistence authority already stay
in `ServiceEngine`.

| Range | Proof group and dependencies | Pure-decomposition assessment |
| --- | --- | --- |
| `3268-3306` | Phase enum, cleanup snapshot/event shape. | Independent shallow schema proof. Its meaning is not used to authorize another group. |
| `3307-3379` | Reconciliation status/identity/optional fields plus retained `ExecutionReport` exact binding. | Cohesive pure proof. It depends only on the raw reconciliation mapping and `ExecutionReport.from_dict`; it does not depend on current phase or execution fields. |
| `3380-3484` | Failed execution metadata, nested automatic-retry status/field-set/identity checks, worker identity, interruption kind, manual retry marker. | Mostly independent payload proofs. Automatic-retry validation is already a local pure helper (`3399-3454`), demonstrating the useful granularity. |
| `3485-3562` | Idle has no live worker; accepted integration is idle-only and its optional product transition has exact allowed shapes. | Cross-field phase proof. Keep adjacent to the phase discriminator; separating it creates an unnecessary phase-policy interface. |
| `3563-3647` | Early return for idle/merge-pending; active sync coordinates, selected-ticket binding, execution-stage enum, worker identity/stage relation, and pending report checkpoint relation. | This is the central state-machine proof and should remain one local control-flow region. Its ordering intentionally prevents later active-execution checks from accepting idle/merge states. |
| `3648-3714` | Active execution binding and remote-head shape; optional rework provenance plus `restore_review_binding` exact predecessor/report/control binding. | The execution binding is phase-dependent; rework proof depends on it. Keep with the active-state region. |

The only current candidate that reduces complexity rather than displacing a
large block unchanged is a private static
`_validate_reconciliation_state(reconciliation: object) -> None` for
`3307-3379`. It has one new call edge from `_validate_state_invariant`, no
state mutation or effects, and preserves `DevlegateError` translation of
`ExecutionReportError`. Its immutable input is raw untrusted state and its
only output is normal return or `DevlegateError`; introducing a mutable typed
state object would add conversion semantics without evidence of a consumer.
It would permit direct tests of reconciliation shape/report binding without
constructing unrelated phase state. A broader state-schema module is not
justified: all remaining groups are used only here, and moving them would add
an import edge to `ExecutionReport` and review provenance while retaining the
same 448-line state machine.

### Three concrete comparisons

| Candidate | Existing block -> proposed owner | Contract, edges, effects, and test seam | Decision |
| --- | --- | --- | --- |
| Reconciliation validator | `runtime.py:3307-3379` -> private static method on `ServiceEngine` (or a small dependency-free validation module only if a second consumer appears). | Input `object`; normal return or `DevlegateError`; one new call edge. No `_state` mutation, store action, lock, Git, or worker effect. Unit seam is malformed identity and retained-report exact binding. Predicted displacement is about 73 physical LOC, which is not the success criterion; it isolates one independently meaningful proof. | **Independent, low-risk candidate.** |
| Worker through checkpoint | `4906-5037` -> small engine-private method. | Inputs/outcome as above; one new call edge; no import/protocol edge. It owns existing worker and checkpoint effects plus current stage saves, but cannot decide product/retry/publication. Unit seam is outcome mapping and stage/effect order with injected worker/manager. Predicted displacement is about 132 LOC. | **Independent only after the reconciliation local-dedup ticket, medium risk.** It must preserve ordering, shutdown visibility, and exception behavior exactly. |
| Pre-worker admission/plan | `4207-4792` -> hypothetical planner/admission object. | Would require snapshots, ticket store, authorization, mutable state, report store, review provenance, stop checks, and a result covering no-work/reject/admit. It adds multiple calls and a new state-machine contract while still requiring engine `_save_state`. | **KEEP in place.** Its 586 LOC spans real scheduler policy, not a coherent domain operation. |

The validation candidate and worker/checkpoint candidate do not depend on one
another. The worker/checkpoint effect ordering remains coupled to the existing
normal/replay boundary: recovery must continue to use
`recovery_workflow.recover_checkpoint_publication`, not the new private normal
helper. Neither candidate permits a new Git subprocess, a direct `_git`
bypass, a durable state write outside `_save_state`, or a wider Protocol.

### Invariants and focused regression set

For a reconciliation validator extraction, retain failure on malformed
reconciliation identity, malformed retained report, and every mismatched
report binding. Existing direct evidence is
`tests/test_control_recovery.py:1853-1880`; automatic-retry shape has focused
coverage at `1416-1420`, and active workspace identity at
`tests/test_cli_cross_layer.py:673-702`. Add only a direct unit test for an
otherwise valid reconciliation with each optional invalid enum/type, if the
new function makes those cases separately addressable.

For worker-through-checkpoint, retain: no post-worker action while the worker
group is unretired; `WorkerAdmissionClosed` returns before a live worker stage
survives; checkpoint intent is durable before the Git checkpoint; exactly one
clean/replayable checkpoint is recorded afterwards; and a lifecycle drain
after checkpoint returns without product observation/publication. Existing
focused coverage includes worker non-retirement in
`tests/test_daemon_recovery.py:615-619`, checkpoint/replay proof in
`tests/test_control_recovery.py:955-1058`, and drain-at-checkpoint boundaries
in `tests/test_daemon_lifecycle.py:670-694`. An extraction ticket should add
one injected-worker call-order test that asserts the stage sequence and one
submodule-failure test that verifies `_record_post_worker_failure` remains
before the raised error. It should not duplicate broad end-to-end recovery
tests.

Owner/shutdown invariants remain: the outer scheduler decides all pre-admission
stops (`4000-4006`, `4788-4792`); the worker helper may only observe the
already supplied stop event; the normal post-checkpoint drain check stays in
the caller (`5038-5046`); replay dispatch remains earlier in the caller
(`3949-3957`). This avoids a helper that accidentally treats a drain as either
an ordinary worker failure or permission to skip a replay proof.

### Finite prioritized implementation shortlist

1. Make normal changed-product handling call the existing
   `_persist_product_reconciliation` helper. This is the previously validated
   same-owner duplication; preserve evidence-pin-before-idle-save and add the
   targeted drift/evidence regression identified in Version 3.
2. Extract the pure reconciliation-state validator as one private static
   helper. Acceptance: unchanged accepted/rejected payload matrix and no new
   imports/effects/state writes.
3. Reconcile `WorkflowRecoveryCapabilities` with calls already made by the
   recovery workflow and add a declaration/conformance check. This remains an
   existing-contract repair, not a normal-flow abstraction.
4. Extract the engine-private worker-through-checkpoint operation only with
   an explicit stage/effect ledger and the focused worker, integrity, replay,
   and drain tests above. Do not use it from recovery.
5. Audit direct checkpoint/publication `_git` calls for owner/shutdown routing
   only after their interruption contract is specified, as established in
   Version 3.

No ticket should extract plan/admission, accepted integration dispatch, normal
publication/lifecycle, or the complete state validator wholesale. The
remaining uncertainty is whether the worker/checkpoint helper's snapshot
publication belongs inside its outcome contract or should remain visibly at
the scheduler call site; resolve that with a before/after effect ledger in a
narrow implementation ticket, not another broad runtime decomposition pass.

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

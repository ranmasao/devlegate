# Git Command Inventory

This is the before/after accounting for TASK-072. Counts are executed Git
subprocesses, not Python helper calls. A command is classified by its highest
effect tier: Tier 1 is local-only read, Tier 2 is remote read, and Tier 3 is a
state-changing command. Ordinary `fetch` is Tier 3 because it writes local
objects and tracking refs.

`ServiceEngine` remains the owner of admission, locks, durable state, WAL
stages, re-observation, authorization, retry policy, and user-facing errors.
`devlegate.git` constructs individual commands. `control_git_workflow` owns the
ordered control synchronization transaction. `recovery_workflow`,
`recovery_transactions`, and `reconciliation_transactions` own their already
factored replay and rewrite transactions. Every effectful operation receives
the service runner, so owner and shutdown enforcement remain in force.

## Explicit Operations

| Operation | Executed command and actual effect | Preconditions and error boundary |
| --- | --- | --- |
| `ref_exists` | `show-ref --verify --quiet ref`; Tier 1 exact local-ref observation | Status 0 is present, status 1 is absent, and every other status is an observation error; localized diagnostics are not parsed. |
| `create_ref_if_absent` | `update-ref ref new ""`; Tier 3 local ref write | Caller observed absence; nonzero is a race and is re-observed. |
| `compare_and_swap_ref` | `update-ref ref new expected-old`; Tier 3 local ref write | Caller observed the expected old object; nonzero is never treated as success. |
| `update_ref` | `update-ref ref new`; Tier 3 local ref write | Only idempotent recovery/disposition paths use this intentionally unguarded operation. |
| `fetch_ref` | `fetch [--prune] remote ref`; Tier 3 remote read plus local object/tracking-ref writes | Failure is transport failure, never ref absence. |
| `push_refs` | `push options remote refspecs`; Tier 3 remote write | Caller supplies the exact lease and re-observes the remote tip. Fresh control publication leases against absence so a ref created after observation is not overwritten. |
| `merge_fast_forward` | `merge --ff-only ref`; Tier 3 local worktree/ref write | Caller has validated ancestry and, where needed, a durable pending stage. |
| `add_all` / `commit` | `add option` / `commit -m message`; Tier 3 local index/history writes | Control lifecycle state is validated before this transaction. |
| `hash_object` | `hash-object -w --stdin`; Tier 3 local object-store write | Payload is complete before its disposition/evidence ref is published. |
| `add_worktree` / `remove_worktree` | `worktree add` / `worktree remove`; Tier 3 local topology/filesystem writes | Workspace topology and ownership are validated by the workspace manager. |
| `delete_branch` / `reset_hard` | local branch deletion / checkout reset; Tier 3 local ref/worktree writes | Exact checkpoint identity and branch/worktree ownership are proven first. |

No read helper performs fetch, push, ref update, branch change, cleanup, or
worktree mutation. The sole subprocess seam is `_git_runtime`, which adds only
owner-thread and shutdown interruption policy; command construction is not
performed there.

## Runtime Mutation Ownership

| Original runtime sequence | Final owner | Why it remains or moved |
| --- | --- | --- |
| Control fetch and optional fast-forward | `control_git_workflow.sync_control` | Cohesive fetch, ancestry, accepted-integration exception, merge, and post-merge observation. Service supplies policy callbacks and owner runner. |
| Existing control attach | `control_git_workflow.attach_existing_control` | ServiceEngine owns remote-existence admission and result presentation; the workflow owns fetch, topology/ancestry validation, and conditional worktree creation. |
| Fresh control bootstrap | `control_git_workflow.bootstrap_control` | ServiceEngine owns control-init authorization; the workflow owns orphan worktree/add/commit/push/fetch ordering. |
| Execution checkpoint publication | `recovery_workflow.recover_checkpoint_publication` and `ServiceEngine._publish_execution_branch` | WAL and checkpoint policy stay in service/recovery; lease push and exact remote re-observation remain owner-bound. |
| Accepted integration replay | `recovery_workflow.recover_accepted_integration` | Durable accepted-integration ordering and replay are transaction-owned; state mutation remains service capability. |
| Control lineage reconciliation | `recovery_transactions.reconcile_control_lineage` | Repeated admission, evidence create-if-absent, reset, and post-reset validation are one recovery transaction. |
| Product update-base rewrite | `reconciliation_transactions` | Local/remote pending stages, rebase abort, evidence pin, leased publication, and replay remain together. |
| Drop disposition/evidence and retirement | `ServiceEngine._drop_owned` and its evidence/retirement helpers | Service must retain lock, report binding, WAL disposition, and workspace ownership decisions. Each Git call uses named typed operations. |
| Interrupted lifecycle recovery | `ServiceEngine._normalize_stranded_execution` / `_recover_interrupted_execution` | Fresh product/control observations and ticket binding are policy gates; recovery cannot be hidden in a low-level helper. |

## Representative Executed Traces

The control-sync rows below are full executed orderings captured at the
`ServiceEngine._sync_control()` runner seam. They include worktree validation
and the observation callback, not only commands issued by
`control_git_workflow.sync_control`. `R` means a Tier 1 local observation, `L`
a Tier 2 remote observation via `ls-remote`, `F` a Tier 3 fetch, `A` a Tier 1
ancestry check, `M` a Tier 3 local merge, `P` a Tier 3 remote push, `U` a Tier
3 local ref write, and `W` a Tier 3 worktree/index/history mutation.
Re-observations are included. The attach and bootstrap rows below are also full
runner traces. The remaining rows are operation-level parity records unless
explicitly marked as a full trace.

| Workflow | Before subprocess trace | After subprocess trace | Tier counts before -> after |
| --- | --- | --- | --- |
| Control sync, already current (full runner trace) | `R, R, R, R, R, R, R, F, R, R` | `R, R, R, R, R, R, R, F, R, R` | T1 9 -> 9; T2 0 -> 0; T3 1 -> 1 |
| Control sync, fast-forward (full runner trace) | `R, R, R, R, R, R, R, F, R, R, A, M, R` | `R, R, R, R, R, R, R, F, R, R, A, M, R` | T1 11 -> 11; T2 0 -> 0; T3 2 -> 2 |
| Existing control attach, new worktree (full runner trace) | `L, F, R, R, W` | `L, F, R, R, W` | T1 2 -> 2; T2 1 -> 1; T3 2 -> 2 (local worktree write; fetch remote read plus local object/ref writes) |
| Control bootstrap (full runner trace) | `L, R, W, W, W, P, F` | `L, R, W, W, W, P, F` | T1 1 -> 1; T2 1 -> 1; T3 5 -> 5 (local worktree/index/history writes; remote push; fetch remote read plus local object/ref writes) |
| Checkpoint publication (operation subset) | `R, R, R, P, R` | `R, R, R, P, R` | subset only; no new invocation |
| Evidence pin (operation subset) | `R, U, R` (CAS race adds `R`) | `R, U, R` (CAS race adds `R`) | subset only; no new invocation |
| Accepted integration replay (operation subset) | `F, F, R, R, P, F` plus lifecycle commit sequence | unchanged; owned by `recovery_workflow` | subset only; no new invocation |
| Interrupted recovery (operation subset) | `F, R, F, R, R, M` | unchanged; service retains fresh observations | subset only; no new invocation |

The full control-sync and control-init traces are asserted through the real
service runner in `tests/test_daemon_cross_layer.py`; these tests record nested
observations, remote reads, mutations, publication, and refresh. The focused
workflow tests in `tests/test_control_git_workflow.py` assert domain ordering,
while the typed primitive argument traces are asserted in `tests/test_git.py`.
The other rows are intentionally labeled subsets rather than presented as
whole-process traces. Extraction changes ownership and module edges, not
subprocess behavior. There are no new remote reads, no new state-changing
commands, and no per-object or hidden N+1 loop introduced by this checkpoint.

## Interrupted `merge_pending` Traces

The transport seam no longer repeats an arbitrary command. Before this
checkpoint, any nonzero command in `merge_pending` followed this trace when the
return code matched the shutdown signal: `X(command), X(command)`; this also
applied to a read observation or `fetch`. After the change the transport trace
is only `X(command)`, followed by the original interruption. No nested
invocation is added for read, fetch, push, ref-update, or other mutation calls.

The owning product fast-forward transaction has an explicit, evidence-backed
branch. For a normal product merge with durable `merge_pending` state, the
trace remains `F, R, A, M, R`. If `M` is interrupted, the recovery trace is
`F, R, A, M(interrupted), R(HEAD), [M(retry), R(HEAD)]`: the retry is present
only when fresh HEAD is exactly the persisted old revision and the tracking
ref still resolves to the admitted target; if HEAD is exactly the target, the
transaction continues without a second merge; any other HEAD fails closed.
The bracketed `M(retry)` is the only newly retained invocation. This proves
the prior effect or its absence and does not infer absence from the interrupted
subprocess alone. A control merge has no corresponding retry branch: its trace
ends at `M(interrupted)`, and a later fresh control sync re-observes state
before performing one normal merge.

## Source Delta

The new `control_git_workflow.py` contains the extracted control
synchronization, attach, and bootstrap transactions and their traceable command
effects. `runtime.py` now delegates those sequences and retains the policy
callbacks, owner runner, control-init admission, state decisions, and error
translation. Existing recovery/reconciliation modules account for the replay
and rewrite sequences moved by the preceding TASK-070/071 work.

At this checkpoint the extracted module is 279 lines including its module
documentation, while `runtime.py` is 8,573 lines. The sync method itself fell
from 63 lines to 22 lines, and the control-init attach/bootstrap sequences no
longer have duplicate runtime implementations. The executed Git count is
unchanged because the workflows pass the same owner-bound runner and preserve
the same ordering. No direct subprocess call sites were added; the new module
calls the existing named Git operations only.

## Execution Workspace Boundary

TASK-080 inventories the direct commands in `execution_workspace.py`. The
manager remains the domain owner for execution branch/worktree lifecycle. Its
optional runner is a capability: standalone inspection uses `run_git`, while
every `ServiceEngine` and recovery-workflow construction supplies
`_git_runtime`. That runner asserts the hosted owner thread and converts an
intentional shutdown signal into the runtime interruption path. The injection
does not add a subprocess or grant mutation authority to standalone callers.

| Group and manager operation | Commands | Authority and failure semantics | Executed subprocesses/effects |
| --- | --- | --- | --- |
| Branch/ref observation: `_validate_base`, `_ensure_branch`, `_validate_branch`, rework predecessor/replay proof | `rev-parse --verify`, `show-ref --verify`, `merge-base --is-ancestor`, `for-each-ref`, `rev-list`, `cherry` | `prepare`/`prepare_rework` are admitted lifecycle calls; `inspect` is read-only. Exact absence is only `show-ref --verify --quiet` in shared helpers; Git errors and malformed output fail closed. | One per listed observation; branch-create race adds one re-observation. No effects. |
| Worktree registration and identity: `_registrations`, `_validate_existing`, path checks | `worktree list --porcelain`, `rev-parse --git-common-dir`, `rev-parse --show-toplevel`, `symbolic-ref --quiet --short HEAD`, `rev-parse HEAD`, `status --porcelain` | Registration parsing rejects duplicate/malformed/detached/wrong-repository state. `inspect` reports unsafe rather than absence for observation errors. `prepare`, `retire`, and `checkpoint` revalidate exact path, branch, HEAD, and cleanliness before effects. | Registration is one command. Existing-checkout proof is six commands. No effects. |
| Lifecycle mutation: `_ensure_branch`, `prepare`, `_prune_expected_stale_registration`, `retire`, rework transplant | `branch`, `worktree add`, `worktree remove [--force]`, `update-ref` CAS, `cherry-pick [--abort]` | Only admitted service/recovery paths receive the owner runner. Every mutation has prior topology proof; add failure re-observes registration, CAS refusal is a conflict, and removal is followed by registration/path confirmation. | One mutation each. Branch creation failure adds one observation; failed add adds one registration observation; failed cherry-pick adds one abort. No uniformity-only calls. |
| Checkpoint: `checkpoint` | `rev-parse HEAD`, `add -A`, `diff --cached --quiet`, `commit -m` | The caller must supply a currently validated workspace. A non-0/1 diff or failed commit rejects publication; the post-commit HEAD is returned only after the effect. | Three commands for a clean checkpoint, five when committing. `add` and `commit` are local Tier 3 effects. |
| Submodule validation/materialization: `_gitlink_paths`, `_observe_initialized_submodules`, `_prepare_submodules`, `_verify_submodules` | `ls-tree -r -z HEAD`, `submodule status --recursive`, `rev-parse`, `status --porcelain`, `submodule update --init --recursive` | Gitlink paths, symlink components, checkout roots, expected SHAs, and dirtiness are proven before update and before removal. Missing `.gitmodules` is the sole empty-submodule case; unreadable or malformed metadata is an error. | Per repository: one gitlink listing; initialized submodule observation adds up to five commands per nested checkout; verification adds four plus its gitlink listing. Materialization adds exactly one update. The recursion follows submodule topology, not an added loop. |

The before/after trace for every row is identical: the same commands, order,
and local/remote effect tiers run through a different runner reference. This
change adds zero Git subprocesses and zero mutations. `execution_workspace.py`
retains specialized worktree, identity, submodule, checkpoint, and rework
commands because their proofs depend on that domain's ordered state; only
shared ancestry, commit resolution, and patch-equivalence semantics use typed
`git.py` helpers.

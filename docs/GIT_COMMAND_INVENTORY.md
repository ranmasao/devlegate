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
| `create_ref_if_absent` | `update-ref ref new ""`; Tier 3 local ref write | Caller observed absence; nonzero is a race and is re-observed. |
| `compare_and_swap_ref` | `update-ref ref new expected-old`; Tier 3 local ref write | Caller observed the expected old object; nonzero is never treated as success. |
| `update_ref` | `update-ref ref new`; Tier 3 local ref write | Only idempotent recovery/disposition paths use this intentionally unguarded operation. |
| `fetch_ref` | `fetch [--prune] remote ref`; Tier 3 remote read plus local object/tracking-ref writes | Failure is transport failure, never ref absence. |
| `push_refs` | `push options remote refspecs`; Tier 3 remote write | Caller supplies the exact lease and re-observes the remote tip. |
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

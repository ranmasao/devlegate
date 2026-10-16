# Git Command Inventory

`ServiceEngine` remains the owner of admission, locks, durable state, write-ahead
stages, re-observation, and error policy. `devlegate.git` owns construction of
effectful Git commands. Every operation below executes through its supplied
runner, so hosted owner and shutdown enforcement are preserved.

## Explicit Operations

| Operation | Git command and effect | Preconditions and boundary |
| --- | --- | --- |
| `create_ref_if_absent` | `update-ref ref new ""`; tier 3 local ref write | Evidence identity was observed absent. Caller handles conflicting identity and re-observation. |
| `compare_and_swap_ref` | `update-ref ref new expected-old`; tier 3 local ref write | Caller observed the expected old object and treats nonzero status as a race. |
| `update_ref` | `update-ref ref new`; tier 3 local ref write | Caller has already established idempotence or recovery policy. This is intentionally not a CAS. |
| `fetch_ref` | `fetch [--prune] remote ref`; tier 3 remote read plus local tracking-ref/object writes | Caller has owner admission or is in a read-only observation path. Failure is never treated as absence. |
| `push_refs` | `push options remote refspecs`; tier 3 remote write | Caller supplies the exact lease option where needed, then re-observes the remote state. |
| `merge_fast_forward` | `merge --ff-only ref`; tier 3 local worktree/ref write | Caller has a durable pending stage when recovery requires replay. Failure preserves that stage. |
| `add_all` / `commit` | `add option` / `commit -m message`; tier 3 local index/history writes | Lifecycle code has validated workflow state and owns the serialized control mutation. |
| `hash_object` | `hash-object -w --stdin`; tier 3 local object-store write | Evidence/disposition payload is complete before its ref is published. |
| `add_worktree` / `remove_worktree` | `worktree add` / `worktree remove`; tier 3 local worktree metadata/filesystem writes | Workspace topology has been validated by `ExecutionWorkspaceManager`; failures remain workflow errors. |
| `delete_branch` / `reset_hard` | local branch deletion / checkout reset; tier 3 local ref/worktree writes | Caller has exact checkpoint identity and has validated branch/worktree ownership. |

## Runtime Sequences

The following sequences preserve their existing lock and WAL boundaries. The
listed observations are intentional checks, not helper side effects.

| Sequence | Preconditions, WAL stage, and safety checks | Executed mutations |
| --- | --- | --- |
| Control sync | Owner admission; control checkout clean; lifecycle or sync state remains durable on failure | fetch, optional ff-only merge |
| Product sync | Owner admission; `merge_pending` is written before a merge | fetch, optional ff-only merge |
| Control bootstrap/attach | Control init command owns admission; remote branch or absent-branch precondition is validated first | worktree add, add, commit, push, refresh fetch |
| Accepted integration | Accepted report and product/control observations are proven; lifecycle state is serialized before control publication | optional leased product push, fetch/ff-only sync, add, commit, control push |
| Execution publication | Checkpoint HEAD and remote predecessor are re-observed; lease is explicit | leased push |
| Evidence pinning | Evidence ref is checked before publication; create-if-absent or CAS failure is re-read | hash-object, ref create/CAS/update |
| Drop/recovery retirement | Evidence is pinned before branch/worktree retirement; remote lease is re-observed | leased delete push, worktree removal, local branch deletion |
| Lifecycle replay | Control fetch and lineage proof precede reset/merge; lifecycle commit is created before push and retried from durable state | fetch, optional reset/ff-only merge, add, commit, push |
| Interrupted recovery | Product/control generations and workspace binding are freshly observed before normalization | fetch, optional ff-only merge |

No helper performs a fetch, push, ref update, branch change, worktree cleanup,
or other mutation unless its name and operation contract state that effect.

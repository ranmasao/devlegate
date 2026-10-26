# Git Semantic API Audit

## Scope and method

This TASK-084 inventory covers direct Git runners and their result interpretation in
`git.py`, `runtime.py`, `execution_workspace.py`, `recovery_workspace.py`,
`reconciliation_transactions.py`, `recovery_transactions.py`,
`reconciliation.py`, and `control_git_workflow.py`. It records command traces and
semantic ownership; it is not a claim that every low-level command is incorrect.
Worker-process return codes are excluded because they are not Git semantics.

| Owner/caller | Command family | Valid negative/absence | Failure or ambiguity | Effect and replay rule |
| --- | --- | --- | --- | --- |
| `git.py` observations | `show-ref`, `rev-parse`, `ls-remote`, `cat-file`, `log`, `status`, `worktree list` | Exact ref absence is `show-ref --quiet` status 1; detached HEAD is `symbolic-ref` status 1; empty `ls-remote` is absent remote ref | Other status, malformed porcelain, unreadable object/ref, and runner OS errors raise `GitObservationError` | Read-only; retry only by fresh observation |
| `git.is_ancestor` consumers | `merge-base --is-ancestor ancestor descendant` | Status 1 is a proven false relation | Status >=2 and OS failure raise `GitAncestryError` | Read-only; replay with fresh graph observation |
| `git.patch_equivalence` / rework | `cherry original-tip replay-tip base` | `+` is an inequivalent replay commit, `-` is equivalent | Nonzero or a non-`+/- <oid>` record raises `GitObservationError` | Read-only; replay with exact three identities |
| `execution_workspace` | worktree/branch/rev-list/cherry-pick/add/commit | Clean staged diff has status 0; status 1 creates a checkpoint | Other diff status, malformed topology, or command failure is unsafe/error | Worktree and commit effects require topology and HEAD re-observation; cherry-pick is not blindly replayed |
| `recovery_workspace` / `recovery_transactions` | evidence `update-ref`, worktree remove, branch CAS | Missing evidence ref is distinct from unreadable evidence | A failed create/CAS is not accepted until exact evidence/ref identity is re-observed | Evidence create is replay-safe only when post-observation proves its exact object; CAS must re-observe before retry |
| `reconciliation_transactions` | temporary refs, rebase, reset, cherry-pick | Domain-specific empty history cases are handled in the transaction | Any command/output mismatch preserves evidence and aborts the transaction | Effectful rewrite steps are not replayed after interruption; recover from durable transaction evidence |
| `control_git_workflow` | fetch, ff merge, initial control push | No absence is inferred from transport failure | A nonzero fetch/push is transport/process failure, not a proven remote rejection | Fetch is safely repeatable; merge/push require post-failure observation |
| `runtime` publication and cleanup | fetch, explicit leased push, evidence ref CAS, remote delete | Remote ref absence comes only from successful observation | Nonzero push is an unknown remote outcome unless a successful re-observation proves predecessor/rejection/success | Never blindly replay remote mutation; fetch/observe exact remote ref first |

## Mutation and transport contract

`create_ref_if_absent` uses Git's empty expected-old lease. `compare_and_swap_ref`
uses an explicit expected old object, including compare-and-delete. Callers retain
the expected identity and must classify a nonzero result by re-observing the exact
ref: it can be a proven conflict, local process error, or an interrupted/unknown
effect. `update_ref` is deliberately unconditional and is limited to callers whose
transaction authority already supplies that guarantee.

`push_refs` deliberately preserves the raw process result. Git transport can lose
the response after the remote accepts a mutation, so a nonzero push is not a proven
rejection. The publishing owner must fetch or use a read-only remote observation
before it retries. Explicit `--force-with-lease=<ref>:<expected>` values preserve
the required remote predecessor identity.

## Bounded changes

The high-risk callers changed in this milestone are rework preparation and workspace
recovery. They now use typed ancestry and patch-equivalence helpers, so status 1 is
a valid false answer only where Git defines it, while status >=2, OS errors, and
malformed `cherry` output fail closed. Transaction authority, service locking, WAL,
and cleanup scheduling are unchanged.

## Publication proof measurement

`runtime._prove_published_workspace_head()` calls `fetch_ref()` for the configured
product remote/ref, then resolves the local tracking ref and proves ancestry. Its
command trace is one network `git fetch <remote> <product-ref>` per invocation plus
local ref/ancestry observations. Full cleanup invokes candidate proof per worktree,
so N candidates can cause N fetches (an N+1-style repeated-fetch cost when an
earlier product fetch already occurred). This is measured from the nested call trace;
no scheduler, caching, or TOCTOU check changed here.

## Follow-up boundaries

Potential broader work is intentionally deferred: typed remote-publication outcomes
at every runtime call site, reconciliation transaction normalization, and cleanup
fetch coalescing. Each needs transaction-specific proof and cannot be safely folded
into this first bounded semantic boundary change.

# Git Semantic API Audit

## Scope and method

This TASK-084 inventory covers direct Git runners and their result interpretation in
`git.py`, `runtime.py`, `execution_workspace.py`, `recovery_workspace.py`,
`reconciliation_transactions.py`, `recovery_transactions.py`, `reconciliation.py`,
and `control_git_workflow.py`. Worker-process return codes are excluded because they
are not Git semantics. Locations are source locations in this checkpoint. "Observed"
means the command path was read or covered by an existing test; "static" means the
call path was classified from source only. Grouped rows name the exact function only
where its commands and authority are the same.

| Location and trace | Runner | Valid negative or absence | Error or ambiguity | Effect, owner, shutdown authority, replay | Proof |
| --- | --- | --- | --- | --- |
| `git.py:316-390`, `resolve_commit`, `resolve_ref_commit`: `show-ref`, `rev-parse` | injected `runner` | quiet `show-ref` 1 is absent; absent exact ref returns `None` | present-but-unreadable ref, non-absence diagnostic, malformed identity, OS error -> `GitObservationError` | Read only; caller owns admission. Re-observe, never infer absence from failure. | Observed: `test_git.py`; static callers. |
| `git.py:417-451`, `is_ancestor`: `merge-base --is-ancestor A D` | injected `runner` | 0 true; 1 proven false | >=2 or OS error -> `GitAncestryError` | Read only; fresh graph observation is replay-safe. | Observed: unit and real-Git tests. |
| `git.py:454-491`, `patch_equivalence`: `cherry original replay base` | injected `runner` | `- <oid>` equivalent; `+ <oid>` inequivalent | any nonzero, malformed marker or object id -> `GitObservationError` | Read only; replay only with the same three identities. | Observed: unit and real-Git tests. |
| `git.py:494-1003`, observation helpers: `merge-base`, `rev-list`, `status`, `worktree list`, `ls-remote`, `cat-file`, `show` | injected `runner` | documented empty history/path/object/ref results only | nonzero outside each documented case and malformed porcelain/output -> `GitObservationError` | Read only; owner chooses recovery after a fresh observation. | Static inventory; helper tests cover representative absence/malformed cases. |
| `git.py:43-313`, mutation wrappers: `update-ref`, `fetch`, `push`, `merge`, worktree/branch/index/commit/reset | injected `runner` | none, except a caller may prove a post-state | raw result deliberately retained; failed push is unknown remote outcome, not rejection | Mutating wrapper only. Transaction owner retains expected old SHA and must re-observe. Fetch may repeat; local and remote mutations require post-failure observation. | Observed CAS real-Git test; static callers. |
| `execution_workspace.py:80-142`, `prepare` | module `_git` seam | existing registered worktree is reusable only after exact topology/head checks | failed worktree add is rechecked once; missing registration or malformed output is error | Local worktree create. Manager owns workspace mutation; service owner supplies lock/admission. Re-observe registrations before retry. | Observed: workspace tests. |
| `execution_workspace.py:144-304`, `prepare_rework` | `_git`, `is_ancestor`, `patch_equivalence` | ancestry status 1 is conflict; exact `-` patch series is an already replayed port | ancestry error, `cherry` error/malformed output, non-linear delta, CAS move failure -> error/conflict | Retire, `update-ref` lease, and cherry-pick are local mutations. Do not replay a failed pick; inspect exact branch/worktree state. | Observed: rework and consumer regression tests. |
| `execution_workspace.py:306-414`, `inspect`, `retire` | `_git` | absent branch/worktree can be recoverable only under exact topology rules | unreadable/malformed registration and failed removal are unsafe/error | Inspection is read-only. Removal is manager-owned under service lock; post-check registration before retry. | Static inventory; workspace tests. |
| `execution_workspace.py:416-605`, submodules and `checkpoint` | `_git` | `diff --cached --quiet` 0 means no commit, 1 means changes | other diff status and all materialization/commit errors fail closed | Local index/commit effects. Re-observe HEAD/status; do not replay commit blindly. | Static inventory. |
| `recovery_workspace.py:47-187`, `inspect_bound_workspace` | `run_git`, manager methods | missing branch is recoverable only when no attached material remains; false ancestry is unsafe | observation/ancestry errors are unsafe, never descendant | Read-only classifier; runtime recovery owner controls admission and shutdown. Fresh observation required. | Observed: boundary and consumer regression tests. |
| `recovery_workspace.py:190-311`, `repair_bound_workspace` | `run_git`, `create_ref_if_absent` | absent evidence may be created only after pre-worker absence proof | failed/contended evidence creation, unreadable evidence, changed branch, failed removal -> error | Evidence pin, worktree removal, branch recreation. Owner must re-observe exact evidence and branch; do not replay remove/CAS without it. | Static inventory. |
| `recovery_transactions.py:45-99`, `reconcile_control_lineage` | `run_git`, `create_ref_if_absent`, `reset_hard` | absent evidence is acceptable only if post-create observation pins `from_head` | evidence mismatch and reset failure abort recovery | Recovery owner lock and repeated admission authorize effects. Evidence creation is replay-safe after identity proof; reset needs re-admission/re-observation. | Static inventory. |
| `reconciliation_transactions.py:169-205`, `prove_workspace_lineage` | local `_git` escape hatch | no commits is a domain invalid lineage unless proof says otherwise | failed `rev-list` becomes invalid lineage; parent read failure cannot prove linearity | Read-only transaction evidence, retained intentionally to keep domain proof cohesive. Re-observe for retry. | Static inventory. |
| `reconciliation_transactions.py:230-570`, rewrite/recovery stages | local `_git` escape hatch and owner publication methods | durable WAL stage, not a Git status, selects recovery | rebase/reset/cherry-pick failure, malformed parent data, or remote drift preserves evidence and aborts | Transaction owner owns WAL, lock, and publication lease. Effectful rewrite is not blindly replayed; resume from durable stage and re-observe. | Static inventory. |
| `control_git_workflow.py:58-115`, `sync_control` | injected `runner`, injected ancestry | local==remote or proven ancestor permits fast-forward | fetch/merge error; failed ancestry is raised by typed helper | Fetch is repeatable. Control workflow owns local merge under service authority; re-observe HEAD after failure. | Static inventory. |
| `control_git_workflow.py:118-224`, `attach_existing_control` | injected `runner` and typed observations | remote branch absence only after successful fetch plus ref observation | fetch/worktree failure or unreadable topology -> workflow error | Fetch/worktree add are workflow effects. Re-observe registrations before retry. | Static inventory. |
| `control_git_workflow.py:227-359`, `bootstrap_control` | injected `runner`, typed observations | no remote absence is inferred from push failure | failed leased initial push is unknown remote outcome until fetch/`ls-remote` proves it | Workflow owns bootstrap under service authority. Fetch is repeatable; merge/push require read-only re-observation. | Static inventory. |
| `runtime.py:1471-1742`, publication/evidence helpers | `self._git_runtime`, Git wrappers | evidence absence only through typed local observation | create/CAS/push nonzero is conflict or unknown until exact local/remote re-observation | Runtime owns WAL and service lock. Explicit old SHAs and `--force-with-lease` are retained. | Static inventory. |
| `runtime.py:4006-6775`, lifecycle and remote cleanup | runtime runner and Git wrappers | remote absence only from successful `remote_ref`/fetch observation | transport/process error and nonzero push are not remote rejection | Runtime owns remote publication/deletion. Do not replay push/delete; re-fetch exact ref and predecessor first. | Static inventory. |
| `runtime.py:7134-7158`, `_prove_published_workspace_head` | `self._git_runtime`, typed remote/ancestry observations | equality or proven ancestry proves publication | fetch, remote ref, or ancestry error blocks cleanup | Read-only proof, but it is an authoritative immediate reproof before a cleanup effect. Retry only as a fresh proof. | Observed: source trace; no scheduler change. |
| `runtime.py:7160-7300`, `cleanup_workspaces` | manager plus `_prove_published_workspace_head` | unregistered candidate is skipped, not deleted | any proof failure blocks that candidate | Runtime/service lock owns deletion admission. Candidate proof is re-run immediately before cleanup; preserve TOCTOU boundary. | Static inventory. |
| `reconciliation.py:24-117` | none | domain proof predicates return valid false | no subprocess or runner result is interpreted here | Pure domain evidence; transaction caller owns Git observation and WAL. | Observed: boundary tests. |

## Mutation and transport contract

`create_ref_if_absent` uses Git's empty expected-old lease. `compare_and_swap_ref`
uses an explicit expected old object, including compare-and-delete. Callers retain
the expected identity and must classify a nonzero result by re-observing the exact
ref: it can be a proven conflict, local process error, or an interrupted/unknown
effect. `update_ref` is deliberately unconditional and is limited to callers whose
transaction authority already supplies that guarantee.

`push_refs` deliberately preserves the raw process result. Git transport can lose the
response after the remote accepts a mutation, so a nonzero push is not a proven
rejection. The publishing owner must fetch or use a read-only remote observation
before it retries. Explicit `--force-with-lease=<ref>:<expected>` values preserve the
required remote predecessor identity.

## Bounded changes

The high-risk callers changed in this milestone are rework preparation and workspace
recovery. They now use typed ancestry and patch-equivalence helpers, so status 1 is
a valid false answer only where Git defines it, while status >=2, OS errors, and
malformed `cherry` output fail closed. Transaction authority, service locking, WAL,
and cleanup scheduling are unchanged.

## Publication proof measurement

The following is a static call-path count, not an executed measurement. For a
`cleanup_workspaces()` call with a specified cohort of N fully registered candidates,
each candidate that reaches `_validate_workspace_cleanup_target()` invokes
`_prove_published_workspace_head()` once. That proof issues one network
`git fetch <remote> <product-ref>`, then `ls-remote` and, when unequal, local
`merge-base --is-ancestor`. Thus the full-inventory path is N product fetches. This
is separate from any initial product fetch used while building the cohort and from the
both has one initial fetch plus N candidate proofs, not a demonstrated universal
"N+1" trace. No scheduler, caching, CAS/lease behavior, or TOCTOU check changed.

## Follow-up boundaries

Potential broader work is intentionally deferred: typed remote-publication outcomes
at every runtime call site, reconciliation transaction normalization, and cleanup
fetch coalescing. Each needs transaction-specific proof and cannot be safely folded
into this first bounded semantic boundary change. Focused downstream work should add
executed command tracing for cleanup cohorts, then remediate one runtime publication
transaction at a time without changing scheduler ownership.

## Locale-independent observation update

TASK-101 removed the remaining diagnostic-text classifiers from `git.py`.
`resolve_commit` and `resolve_object` use `show-ref --verify --quiet` before
resolving exact `refs/*` names; arbitrary revision failures are observation
errors because `rev-parse` status 128 cannot prove absence. `commit_parents`
and `read_object` use `cat-file -e <full-object-id>`, where status 1 is the
documented absent-object result and every other nonzero result is an error.
`path_object` and `read_path` use `ls-tree -z --format=%(objectname)`, for
which empty structured output is the valid missing-path result. `remote_exists`
uses the successful structured output of `git remote`, not `get-url` errors.

| Function | Previous language assumption | Replacement observation | Failure handling |
| --- | --- | --- | --- |
| `resolve_commit`, `resolve_object` | `rev-parse` diagnostics such as unknown revision | `show-ref --verify --quiet` for exact `refs/*` | Only status 1 from quiet `show-ref` is absent; arbitrary revision failure is an observation error. |
| `commit_parents`, `read_object` | `rev-list`/`cat-file` bad-object text | `cat-file -e <full-object-id>` | Only status 1 for a full object id is absent; status 128 and malformed output fail closed. |
| `path_object`, `read_path` | `rev-parse`/`show` path-does-not-exist text | `ls-tree -z --format=%(objectname) <commit> -- <path>` | Empty NUL-delimited structured output is absent; nonzero and malformed output fail closed. |
| `remote_exists` | `remote get-url` no-such-remote text | successful `git remote` line protocol | Membership is a valid negative; config/read failure is an observation error. |
| `run_git` | none; diagnostics were forwarded to operators | child-only `LC_ALL=C` environment | Diagnostics remain human-facing only. No global environment is changed. |

Representative local-read trace delta: an exact-ref commit resolution changes from
`rev-parse --verify <ref>^{commit}` to quiet `show-ref`, verbose `show-ref`, then
`rev-parse`; object reads add `cat-file -e <oid>` before `cat-file -t/-p`; path
reads change from `show <commit>:<path>` to `ls-tree -z` followed by
`cat-file -p <oid>`. Remote existence changes from `remote get-url <name>` to
`remote`. All added commands are local reads, with no fetch, network, ref, index,
or worktree mutation.

The added local reads are one `cat-file -e` before exact object parent/content
reads and one `ls-tree` before path content reads. They do not fetch, contact a
remote, or mutate Git state. `run_git` copies the caller environment and sets
`LC_ALL=C` only for Git subprocesses so forwarded diagnostics are reproducible;
this is defense in depth and is never a semantic parser dependency. True
Bulgarian Git diagnostic validation requires a host with `bg_BG.UTF-8` generated
and Git translations installed. Deterministic tests inject Bulgarian and opaque
diagnostics independently of host locale.

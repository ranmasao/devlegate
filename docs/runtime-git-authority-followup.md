# Runtime Git authority: partial audit and follow-up

This is a partial TASK-108 audit, not an assertion that all runtime subprocesses
are owner/stop protected. The existing `GIT_COMMAND_INVENTORY.md` describes the
typed commands, but its assertion that every effect uses the service runner is
not true for the current runtime. A complete function-by-function inventory,
including callback and helper internals, remains necessary.

`_git_runtime` checks hosted owner before spawning and translates a matching
shutdown signal after a failed command; it does not reject a pending stop before
spawn. `reconciliation_git` rejects a pending stop and then calls `_git_runtime`.
These are different contracts. A status observation at
`_remote_exists_for_status` deliberately uses raw `_git` for a status view;
its callers and standalone preflight permissions still require a complete audit.

## Audited transaction ledger

Counts below are per indicated path, excluding conditional error rechecks.
No command was added or removed by this iteration. Tier 1 is a local read,
Tier 2 a remote read, and Tier 3 a write including fetch.

| Workflow and call site | Before and after command/effect sequence | Admission, replay and stop |
| --- | --- | --- |
| `_persist_product_reconciliation` | Exact ref resolution: `show-ref --verify --quiet` and, if present, `show-ref --verify`, `rev-parse`; then `update-ref` (tier 3); then save pending state. The carried-forward implementation uses `_git_runtime` for admitted drain. | Exact evidence identity is checked before pinning. Stop after pin but before state leaves the same ref to be re-observed on entry. |
| `_publish_execution_branch` | `rev-parse HEAD`, remote `ls-remote`, optional `merge-base --is-ancestor`, leased `push`; on nonzero push re-observe remote. The carried-forward implementation uses `_git_runtime` for admitted drain. | Admission distinguishes new work from an execution already draining. A successful ambiguous push must be recovered via durable publishing intent and exact remote observation, not blind push. |
| `_pin_rework_evidence` | Exact ref resolution as above; if absent `update-ref ref checkpoint ""` (create-only CAS); then exact ref resolution again. All three calls now use `reconciliation_git`. | Conflicting identity fails closed. A stop after successful CAS leaves the pin; safe re-entry observes and verifies it before advancing. Stop before a command refuses spawn. |
| `_prepare_rework_remote_evidence` | Local pin as above; `ls-remote` predecessor and evidence; if absent leased `push` of immutable evidence, followed by `ls-remote`; save preparation stage only after proof. Push now uses `reconciliation_git`. | Empty lease prevents overwriting an existing remote ref. Interruption after push leaves prior stage and replay must re-observe exact evidence. Remote observations currently use `_git_runtime` and need their own stop audit. |
| `_prepare_rework_remote_predecessor` | Local pin; `ls-remote` predecessor and, if appropriate, evidence; leased `push` of workspace head; `ls-remote`; save remote head and preparation stage. Push now uses `reconciliation_git`. | Exact predecessor lease and evidence proof precede replacement. Interruption after push must preserve earlier durable stage and re-observe before deciding whether predecessor is already installed. |

The exact-ref helpers can execute more than one subprocess: absence costs the
quiet `show-ref`; a present ref adds an identity read and commit resolution;
races can trigger an additional quiet check. Treat each runner invocation as a
separate shutdown boundary, not a single atomic observation.

## Drop transaction: current iteration

Before and after, command order and conditional counts are unchanged. The
changed calls use `_git_runtime` instead of raw `_git`; it now also forwards
`input_text` for `hash-object`. Tier 1 means local read, tier 2 remote read,
and tier 3 state change. The drop admission refuses lifecycle drain before
new work, while already admitted work and restart recovery must be able to
finish; no stop-strict gate was inserted within the transaction.

| Call site | Executed command sequence and replay boundary |
| --- | --- |
| `_read_drop_disposition` | Exact `show-ref --verify --quiet`, then if present `rev-parse --verify`, `cat-file -e`, `cat-file -t` and `cat-file -p` (tier 1). Used by admission and replay; now enforces hosted ownership. |
| `_persist_drop_intent` | Read disposition; if absent `hash-object -w --stdin` and `update-ref` (tier 3), with disposition reread on failure. The dropping disposition is durable before evidence and cleanup. |
| `_pin_drop_evidence` | Exact ref `show-ref --verify --quiet`; if present `show-ref --verify` and `rev-parse --verify`; if absent `update-ref`, reread on failure; disposition read, `hash-object -w --stdin`, `update-ref`, reread on failure. Exact checkpoint is checked before replacement; tier 3 writes retain existing unguarded update semantics. A crash after pin leaves evidence for replay. |
| `_drop_already_completed`, `_validate_drop_evidence` | Disposition and checkpoint resolution (tier 1), plus workspace proof and remote `ls-remote` (tier 2); replay allows a missing remote only after disposition exists. Workspace manager commands already use the service runner. |
| `_retire_drop_workspace` | `ls-remote`; if exact, leased deletion `push`; on failure `ls-remote`; manager proof and worktree removal; local branch resolution and optional `branch -D` (tier 3). The disposition and evidence precede deletion. Restart observes the remote and worktree again rather than blindly deleting. A test now interrupts after an actual successful delete push and restarts. |

This is not a complete runtime inventory. In particular, raw recovery,
accepted integration, lifecycle publication, cleanup and status/preflight
call sites still need individual admission and nested-command review. Some
workspace proof helpers reached from drop also remain raw and are not claimed
as hardened by this iteration. `_git_runtime` checks owner on every spawn but
does not by itself prevent a pending stop from spawning new work.

## Remaining grouped work

1. Inventory every `runner=_git`, direct `_git`, default runner and indirect
   callback in `runtime.py`, including standalone status/preflight paths. Record
   function, every nested subprocess and tier, owner/stop admission, WAL/lease or
   CAS, and whether a direct transport exception is actually justified. Inspect
   all semantic helper internals and call sites, not just textual matches.
2. Audit and harden accepted integration and product/control publication as
   coherent WAL transactions. Include the raw ancestry, push, fetch, control
   merge, commit and lifecycle paths. Prove interruption after remote success
   using real restart and exact remote identities.
3. Audit unsafe workspace recovery, lifecycle, drop and cleanup together with
   their worktree proof, evidence pin, CAS and stage transitions. Test crashes
   after each materially changed destructive boundary.
4. Finish checkpoint and rework interruption coverage, including a stop between
   observations and real ambiguous remote success followed by restart; assess
   all uses of `_execution_remote_head` before changing its runner. Preserve
   proof before advancing state and do not insert a stop gate that loses an
   already completed irreversible effect.
5. Run focused regressions and Ruff, then require exact-checkpoint CI before
   considering the ticket complete.

## Additional source-reviewed boundaries

This register extends the partial audit above; it is not yet an exhaustive
register. Line numbers refer to `src/devlegate/runtime.py` at the carried-forward
checkpoint. The tiers are those defined above. Raw calls are defects unless an
exception is explicitly established; an owner-thread entry point alone does not
protect a call invoked through another path. No command sequence in this table
has been changed by this documentation update.

| Function and raw call sites | Actual Git operations and authority/replay assessment |
| --- | --- |
| `_validate_force_retry_admission` (1381) | `merge-base --is-ancestor` (tier 1), owner admission, not a standalone view. Must use the runtime capability. |
| `_force_retry_transition` (1470-1475) | Exact evidence resolution (tier 1), conditional create-only `update-ref ref checkpoint ""` (tier 3), then save retry state. An interrupted pin must be re-observed by exact identity before state is advanced. The checkpoint precedes the pin. |
| `_validate_recover_admission` (1608-1610) | Two exact-ref resolutions (tier 1) establish authorized branch and evidence identities; fresh operator admission must refuse shutdown before it begins. |
| `_recover_unsafe_bound_execution_workspace` (1635-1746) | Exact branch/evidence resolution; create-only local evidence; conditional remote evidence resolution and create-only pin; conditional evidence push; remote re-observation and leased branch push; registered-worktree proof and forced removal; branch re-observation and expected-old `update-ref`; manager preparation (tiers 1-3). This is one recovery family, not independent substitutions: interruption after evidence push, branch push, worktree removal or CAS requires exact re-observation on re-entry. In particular, remote evidence publication is conditional on local evidence being absent; whether a crash between local pin and remote evidence push can safely resume is an explicit unresolved question. |
| `_canonical_reconcile_assertion`, `_automatic_zero_delta_authorization` (1845, 1939) | `rev-parse --verify` of a user revision and `merge-base --is-ancestor` (tier 1), respectively. Both authorize work rather than being standalone status; fresh admission and already-admitted drain must be distinguished. |
| `_validate_control_reconcile_admission` (2014-2044) | Resolve local HEAD; `fetch --prune` control branch (tier 3, updates tracking refs); exact remote tracking ref resolution; one or two ancestry tests (tier 1). Operator admission is not a read-only exception even though it is named validation. |
| `_workspace_cleanup_cohort` (2398, 2429) | `for-each-ref` (tier 1) and `ls-remote` wildcard (tier 2), after manager worktree registration observation. It serves cleanup inventory; whether a standalone dry-run caller may bypass hosted owner must be proven at all callers before retaining raw transport. Apply decisions cannot rely on unowned observations. |
| `_git_runtime` (2887-2889) | Raw transport is the intended single implementation behind owner checking. It forwards `input_text`, and maps a matching signal after a failed command; it does not gate a stop before spawn. Retain this transport, not a second default runtime runner. |
| `_remote_exists_for_status` (2993) | `remote` (tier 1) for status/preflight diagnostics, no fetch or mutation. This is a candidate standalone exception, but caller audit and proof that the result is never admission authority remain open. |
| `_accepted_execution_report` (6293-6312) | `log -1`; `ls-tree` and conditional `cat-file -p`; conditional parent lookup and another parent lookup (tier 1). These establish exact accepted execution evidence and are runtime authority, not status. |
| `_published_accepted_execution_report`, `_retire_integrated_workspace` (6381, 6607) | Parent lookup (tier 1), followed by provenance and cleanup proof; missing disposable refs are permitted only for proven published integration. Must retain exact parent identity during replay. |
| `_integrate_accepted` (6476-6540) | Conditional ancestry tests (tier 1); leased product `push` (tier 3); remote re-observation (tier 2); optional product fetch and `merge --ff-only` (tier 3). A push interrupted after remote success requires exact remote re-observation, not blind retry; control completion and cleanup follow only after publication proof. |
| `_prove_published_product_checkpoint`, `_find_accepted_integration_commit`, `_accepted_integration_commit_matches_report`, `_trailerless_integration_matches_report` (7327-7476) | Raw `show -s --format=%B`, `log --format=%H`, and parent lookups (tier 1), mixed with runtime-backed proof helpers. These observations authorize integration or retirement and require owner authority. Legacy trailerless proof must remain exact. |
| `_complete_accepted` (7528-7613) | Leased control push of an already exact local transition, or ticket rename followed by `add -A`, `commit -m`, exact proof and leased push (tier 3). On failed/ambiguous push the existing classification re-observes exact published control identity. Preserve predecessor lease, local commit proof and already-admitted drain. |
| `_safe_running_product_descendant`, `_merge_accepted_product_descendant` (8233-8353) | Ancestry tests and parent lookup (tier 1); WAL `merging` before checkout changes; owner-aware fast-forward and merge/abort; `merged` WAL before leased product push (tier 3), followed by remote observation on failure and `published` WAL. Recovery must prove the exact two-parent merge and predecessor; do not add a stop gate after merge or push. |
| `_prove_product_generation` (8508-8511) | Product `fetch --prune` (tier 3) then tracking ref resolution (tier 1), used to authorize recovery. Fetch is not a standalone observation and updates local Git state. |
| `_execution_workspace_for_rewrite_recovery` (8561-8576) | `rev-parse --git-common-dir`, `rev-parse --show-toplevel`, another common-dir, `symbolic-ref`, `rev-parse --verify HEAD`, `status --porcelain` (tier 1). These bind the staged rewrite to the registered worktree and must use the owner-aware runner. |
| `_prove_unpublished_lifecycle_lineage`, `_prove_bound_ticket_descendant` (8649-8710) | Parent lookup and ancestry (tier 1) mixed with runtime-backed range and path proofs. These are authority for replay and cannot be treated as standalone status. |
| `_apply_lifecycle_once`, `_apply_execution_lifecycle` (8785-8947) | Report write and possible ticket rename precede `add -A`, `commit`; replay loop fetches control, resolves heads, proves parent/ancestry, conditionally fast-forwards or resets, creates lifecycle commit and pushes (tiers 1-3). Exact remote lifecycle observation precedes deciding whether a previous push succeeded. Must preserve replay after commit, reset, and ambiguous push, including the existing bounded race loop. |
| `_reconcile_resume_owned` (10650-10698) | Control HEAD and exact evidence ref resolutions, predecessor ancestry (tier 1), then durable resolving state and publication/re-observation. Evidence identity must remain exact. |
| `_normalize_stranded_execution` (10815-10855) | Product and control `fetch --prune` (tier 3), tracking/HEAD resolution, ancestry, conditional control `merge --ff-only` (tier 3); worktree proof and final state save follow. Re-entry must re-observe a completed fetch or merge and retain admitted generation proof. |
| `_recover_interrupted_execution` (10993-11031) | Product and control `fetch --prune` (tier 3), tracking refs and control HEAD resolution (tier 1), then worktree proof and state save. These are recovery authority, not preflight. |

### Helper-internal command expansion

All of these helpers default to `run_git` in `src/devlegate/git.py`; a caller
that omits `runner` therefore bypasses the engine seam. Explicit propagation
must include every nested call. This list expands the helpers used by the
reviewed raw boundaries, rather than counting each source expression as one
subprocess:

* `resolve_commit`: non-exact revision uses `rev-parse --verify`; exact ref
  delegates to `resolve_ref_commit`, which uses `show-ref --verify --quiet`,
  then, if present, `show-ref --verify` and `rev-parse --verify`. On a race it
  can repeat the quiet probe. `resolve_object` uses the quiet probe for exact
  refs and then `rev-parse --verify`.
* `parent_commit` delegates to `commit_parents`: an object ID can trigger
  `cat-file -e` followed by `rev-list --parents -n 1`. `read_path` calls
  `ls-tree -z` and, if present, `cat-file -p`. `read_object` can run
  `cat-file -e`, `cat-file -t`, and `cat-file -p`.
* `delete_branch_if_unoccupied` runs `worktree list --porcelain`, a branch
  `rev-parse --verify`, compare-and-delete `update-ref -d`, and another
  worktree listing; failure or occupancy after deletion can cause create-only
  ref restoration. A strict stop between delete and restoration can strand an
  occupied branch and needs transaction-specific handling.
* `fetch_ref`, `push_refs`, `create_ref_if_absent`, `compare_and_swap_ref`,
  `update_ref`, `merge_fast_forward`, `reset_hard`, `add_all`, `commit`,
  `hash_object`, and `remove_worktree` each invoke the corresponding single Git
  command. Fetch, index writes and `hash-object -w` are effects even if the
  caller uses them in a proof. `is_ancestor`, `remote_ref`, `file_history_commit`,
  `common_dir`, `worktree_root`, `branch_name`, and `working_tree_status` each
  make one observation command.

### Remaining coherent implementation families

1. Accepted product and control integration, including proof helpers and both
   leased publication paths. Exercise wrong-owner/pre-admission stop and real
   ambiguous-success re-entry after each product and control push and merge.
2. Control lifecycle, including staged report/ticket changes, commit, reset,
   fetch and push. Exercise re-entry after local commit, reset and remote success,
   and wrong-owner/fresh-stop refusal. Preserve admitted drain.
3. Operator and stranded recovery, including local/remote evidence publication,
   worktree removal and ref CAS. First resolve the local-pin/remote-push replay
   question above, then prove each ambiguous effect and exact identity on re-entry.
4. Cleanup, including cohort discovery, worktree proof, remote leases and the
   multi-command branch deletion/restoration helper. Test interruption after
   deletion and restoration, wrong owner and fresh-stop admission.
5. Complete the raw/default/indirect candidate register for all remaining
   functions and prove or reject status/preflight exceptions. Then audit checkpoint
   and rework observations with the same fresh-admission versus drain distinction.

The remaining source sites outside this table and any default or indirect runner
not yet traced are unverified, not implicitly approved exceptions.

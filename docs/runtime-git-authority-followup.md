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

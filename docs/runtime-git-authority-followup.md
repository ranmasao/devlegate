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
| `_persist_product_reconciliation` | Exact ref resolution: `show-ref --verify --quiet` and, if present, `show-ref --verify`, `rev-parse`; then `update-ref` (tier 3); then save pending state. Already changed in carried-forward work to `reconciliation_git`. | Exact evidence identity is checked before pinning. Stop after pin but before state leaves the same ref to be re-observed on entry. Verify conflicting identity and no overwrite in all crash windows. |
| `_publish_execution_branch` | `rev-parse HEAD`, remote `ls-remote`, optional `merge-base --is-ancestor`, leased `push`; on nonzero push re-observe remote. The carried-forward change uses `reconciliation_git` for ancestry and push. This iteration adds no Git commands. | Stop before initial observation is now refused; the initial resolve and remote observation still use `_git_runtime`, which does not reject a stop arriving between commands. A successful ambiguous push must be recovered via durable publishing intent and exact remote observation, not blind push. The existing mocked test is not restart proof. |
| `_pin_rework_evidence` | Exact ref resolution as above; if absent `update-ref ref checkpoint ""` (create-only CAS); then exact ref resolution again. All three calls now use `reconciliation_git`. | Conflicting identity fails closed. A stop after successful CAS leaves the pin; safe re-entry observes and verifies it before advancing. Stop before a command refuses spawn. |
| `_prepare_rework_remote_evidence` | Local pin as above; `ls-remote` predecessor and evidence; if absent leased `push` of immutable evidence, followed by `ls-remote`; save preparation stage only after proof. Push now uses `reconciliation_git`. | Empty lease prevents overwriting an existing remote ref. Interruption after push leaves prior stage and replay must re-observe exact evidence. Remote observations currently use `_git_runtime` and need their own stop audit. |
| `_prepare_rework_remote_predecessor` | Local pin; `ls-remote` predecessor and, if appropriate, evidence; leased `push` of workspace head; `ls-remote`; save remote head and preparation stage. Push now uses `reconciliation_git`. | Exact predecessor lease and evidence proof precede replacement. Interruption after push must preserve earlier durable stage and re-observe before deciding whether predecessor is already installed. |

The exact-ref helpers can execute more than one subprocess: absence costs the
quiet `show-ref`; a present ref adds an identity read and commit resolution;
races can trigger an additional quiet check. Treat each runner invocation as a
separate shutdown boundary, not a single atomic observation.

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

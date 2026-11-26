# Owner-held implementation transfer

`implementation_transfer.ImplementationTransfer` is the common preparation
boundary for REWORK and active reconciliation. It has no lifecycle or CLI entry
point. Call it inside the serialized repository owner region with the existing
owner assertion and shutdown-aware Git runner. Store the admitted attempt in the
caller's existing durable state before invoking it. `TransferIdentity` binds that
attempt token and stable owner/ticket/source generation to the full commit IDs
of original base **A**, original checkpoint **H**, and target product **B**.
Attempt tokens are unique and never reused with different inputs.

The caller supplies `require_source(identity)`, which must independently prove
that H belongs to the admitted generation and that A and B are the admitted
bases. The existence of H, its commit message, or a branch at the same SHA is
not that proof. The transfer rechecks admission before opening the local object
transport. Once materialized, replay depends on the private pins and receipts,
not the continuing existence of a source checkout or ref. The caller ascertains
target freshness, worker retirement, and publication authority at its own
boundaries. The caller also excludes concurrent editors while owner operations
run. As with resource binding, these receipts operate on a trusted filesystem;
they cannot authenticate edits by someone who can rewrite the owner receipts.

## Result contract

- `TransferReady` provides the private repository path, exact candidate
  commit **C**, its tree, its net-effect classification, and whether any step
  was externally resolved. C is B or a proved descendant of B. This result
  proves the mechanical transfer or the exact authorized resolution, not
  semantic correctness, approval, generation creation, or remote publication.
- `TransferConflict` names the exact attempt, step, parent and original commit.
  It is returned only for an attributed Git conflict with preserved material.
  It remains a conflict after staging files. `resolution_tree(conflict)`
  observes a fully staged tree; unresolved index entries, hidden index entries,
  and unstaged, untracked or ignored files are refused. The caller explicitly
  authorizes that tree with `ResolutionAuthorization`, including justification
  for the chosen result (including paths beyond the original contribution).
  The resolver edits and stages files; the owner creates the checkpoint and
  resumes the series. A later conflict needs its own authorization. Marker
  absence or an otherwise clean status is never approval.
- `TransferUnavailable` means a required proof/object is unavailable, an
  identity differs, or a requested operation is unsafe (for example, a stale
  resolution authorization). Consult its reason; do not fall back to another
  checkpoint or adopt a replacement repository.
- `TransferInterrupted` identifies an unsettled effect or changed authorized
  tree that cannot be replayed safely. Keep the candidate and receipts and
  surface the reason. A Git interruption can also propagate through the owner's
  existing shutdown policy; reopen the same admitted attempt to classify it.

## Git transformation and retention

The transfer imports exact objects over local Git transport into an independent
repository with private A/H/B refs. It does not share an object store or register
a worktree with the product repository. It proves the ordered single-parent
lineage from A through H using `commit_range`, `commit_parents`, and
`prove_linear_lineage`. The native `ort` cherry-pick applies one commit at a time
on detached HEAD, retaining author/message and order, including picks that
become empty. This preserves binary content, modes, rename/delete effects and
gitlinks without relying on a textual patch ID or commit count. It does not
initialize or fetch submodules: clean gitlink changes transfer as tree entries;
a divergent gitlink that Git cannot merge is a conflict. Downstream workspace
materialization still verifies and prepares the recursive submodule pins.

If A=B, keep H exactly. If the A/H trees already match, the result is
`source-empty` (C=B unless A=B); intermediate canceled changes remain available
on H. Otherwise, if H is an ancestor of B, return B as `already-ancestor`.
After a full replay, compare the actual B and C trees: `target-tree-unchanged`
can have nonzero commit count, for example when the changes were incorporated
under different IDs. `changed` says only that the trees differ. No classification
skips a nonempty, unproved part of a series.

Receipts live under the owner's `transfers` directory, outside ordinary
execution workspace and ref discovery. The directory and repository receipts
identify the actual owned filesystem objects; config and exact private ref
inventory are checked. An intent with no effect can retry from the proved
original boundary. A copy with no private refs is safe to repeat; a partially
materialized checkout or a ref/checkout effect lacking its receipt is retained
as uncertain. Each cherry-pick has an immutable parent/source/reflog intent.
Only the matching clean original state can retry the pick. A normal return and
attributable Git observation produce its outcome receipt; an unreceipted HEAD
advance or conflict is **not** adopted after restart. The owner can replay an
authorized resolution by recomputing the exact commit from its tree, parent,
attempt and justification and performing a private HEAD CAS. The checkout,
index, operation marker and matching reflog effect are rechecked; unknown or
subsequently edited material is retained. Ready replay requires the entire
receipt chain and the exact clean C.

Ordinary prune cannot see these candidates. Call `release` only after the
lifecycle has durably taken custody of C and any still-needed A/H evidence.
Release requires a matching ready result and the caller's custody proof; its
tombstone lets the owner finish removing the private repository on restart.
The receipts stay. Conflicted and uncertain work is retained, with no implicit
cleanup or adoption path. No candidate is an execution checkpoint, worker
workspace or execution report.

## Upcoming lifecycle callers

| Ticket | Reuse at the lifecycle boundary | Replace with this contract |
| --- | --- | --- |
| TASK-199 (REWORK) | Keep predecessor review/report and generation admission, `ResourceBinding.require_worker_retirement` and live predecessor/evidence-pin checks. Retain creation and publication receipts/CAS for the next execution generation, after a ready result. Inject `ServiceEngine._assert_repository_owner` and `_git_runtime`. | Replace the port and patch-equivalence replay in `ExecutionWorkspaceManager.prepare_rework`. Split the transformation out of `ResourceBinding.prepare_rework`: do not call `rework_source_absent` or record `rework-started` for retirement just to prepare a candidate. Record the attempt in the existing handoff state; a conflict or uncertain result leaves the predecessor generation in place. |
| TASK-200 (active reconciliation) | Keep `admit_update_base` product/topology/evidence and publication authorization, `reconciliation_workspace_binding` and the `ResourceBinding` source proof. Use the existing owner lock/assertion and Git runner. Keep remote CAS, rewritten evidence pinning, and `_resolve` as downstream effects after a ready candidate and fresh lifecycle proofs. | Replace the in-place rebase in `start_fresh_rewrite` and the local transform inference in `recover_local_pending`. `recover_remote_pending` should replay publication from the proved candidate and downstream receipts without another transform or undo. Use one caller-held attempt with the same result contract as REWORK. |

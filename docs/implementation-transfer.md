# Owner-held implementation transfer

`ImplementationTransfer` runs inside the existing serialized repository owner
region using the owner's Git runner. The caller durably admits a unique attempt
token before calling it. `TransferIdentity` binds the attempt to the owner,
ticket, source generation, and exact commits **A** (original base), **H**
(retained checkpoint), and **B** (target product). There is no CLI entry point.

The caller's `require_source(identity)` proves that H belongs to the admitted
generation and that A/B are the admitted bases. It also ensures these objects
remain reachable in the canonical object store until downstream custody. Use
the original generation/ref proof or independently owned retention evidence
appropriate to the origin; an object, matching SHA on an arbitrary ref, or
commit trailer is not evidence of the generation. Preparation and resolution
recheck this proof on reopen. The caller excludes editors during owner
operations, keeps them away from Git administration and owner receipts, and
handles worker retirement, target freshness, and publication authorization.

## Result contract

- `TransferReady` names **C**, its tree, net effect, and whether any step was
  externally resolved. C is B or a proved descendant of B. `path=None` means C
  is an exact source/target commit already in the canonical store. Otherwise
  `path` is an isolated repository whose detached HEAD is exactly C. After
  fresh owner and source checks the caller can fetch the exact C from this
  local path without updating an execution ref (and verify the resulting object
  before its own CAS and receipt boundaries). Neither kind of ready result is
  semantic approval, an execution checkpoint, or permission to start a worker.
- `TransferConflict` identifies the attempt, workspace, series ordinal,
  source commit, and parent at the stop. The editor changes files; the owner can
  stage specified paths with `stage_resolution` and observe the exact fully
  staged tree with `resolution_tree`. Staging alone never constitutes approval.
  `checkpoint_resolution` requires the active conflict and an authorization
  naming that exact tree and the reason for accepting it, including justified
  additional paths. Hidden or unresolved index entries, unstaged/untracked/
  ignored files, changed Git identity, and stale authorizations are refused.
  Checkpointing writes a commit object from the authorized tree and parent,
  without relying on the editor to make commits or change refs. The next
  segment starts from that commit in a fresh checkout. A subsequent conflict
  has its own identity and needs its own authorization.
- `TransferUnavailable` identifies a missing proof/object, unsafe request, or
  mismatched identity. Do not substitute a different source or candidate.
- `TransferInterrupted` identifies an unsettled effect or changed authorized
  material. Retain the files. The owner's existing shutdown exception may also
  propagate. Reopen the same admitted attempt: unexposed work can be recomputed;
  a changed exposed or authorized checkout cannot be silently replaced.

## Representation and progress

`commit_range`, `commit_parents`, and `prove_linear_lineage` verify the ordered
single-parent A..H contribution. Same base returns H exactly. If A/H have the
same tree, classify `source-empty` and return B (or H for same base). If H is
already in B's ancestry, return B as `already-ancestor`. These cases need Git
observations and one immutable intent, with no checkout, object copy or ref.

For a real transfer use an isolated Git checkout, index and administration
directory with a private per-attempt object pool. Git's object alternates read
the bound canonical store without copying history. The private checkouts all
write new objects only into their own pool. The indirection avoids a worktree
registration in the canonical repository and a chain of alternates for each
resolution. A linked worktree would share canonical configuration, refs and
maintenance; an independent fetch of A/H/B would copy reachable history just
to isolate Git state. Neither is needed here. Source retention still belongs
to the admitted caller; an alternate is not a canonical GC pin.

Cherry-pick each source commit with Git's `ort` strategy on detached HEAD,
preserving order, author/message and empty picks. This handles binary content,
mode changes, renames/deletions and gitlinks using Git trees. No submodules are
initialized or fetched in a candidate. Divergent gitlinks Git cannot merge
remain conflicts; downstream materialization still verifies recursive pins.
Compare the actual B and C trees for `changed` or `target-tree-unchanged`, even
when different commit IDs already incorporated the change. Mechanical replay
recomputes the same contribution; unpublished rewritten commit IDs need not
survive recomputation (Git can assign a new committer time). A published C is
exact and is never selected by ancestry alone.

The previous transfer implementation used directory/repository/import/
materialization receipts and private A/H/B refs, then intent/done records per
pick plus conflict/approval records. A lost checkout, ref or pick acknowledgment
could wedge every replay. The replacement's durable inventory is:

| Record | Evidence and replay boundary |
| --- | --- |
| Attempt intent | Binds exact A/B/H and caller identity to the source object store and, only for a real transfer, the filesystem identity of the private volume and pool. Reservation publishes atomically; an unpublished random directory is never opened by replay. |
| One outcome per segment | Binds an owned private checkout, start, exact HEAD/reflog and ready/conflict position to the attempt. In a clean N-commit transfer this is one checkout, N native picks and one outcome, with no per-pick writes, fetches or pins. In-memory Git effects and cleanliness are checked before publication. If publication was lost, replay creates a fresh scratch from the admitted start and never adopts, resets or overwrites the previous scratch. |
| One authorization per exposed conflict | Records the exact resolution tree and justification before writing its deterministic commit object. Repeating that write is safe; HEAD and conflict files stay in place. Revalidate the staged tree before continuing. The next outcome seals progress beyond that conflict. |
| Release tombstone | Records downstream custody of exact C before removing fully attributed, unchanged storage. An inode-bound rename allows interrupted removal to resume. If there is unpublished scratch, retain the whole volume, including its object pool; the scratch may contain foreign edits and is never an adoption or automatic deletion target. |

Candidate directories are outside ordinary execution checkout/ref discovery;
ordinary Git worktree prune cannot remove them. Unsettled and exposed conflict
material has no automatic release. The owner releases a ready result only after
the lifecycle has taken durable custody of C and any still-needed source
evidence. Abandoned scratch is retained for explicit disposition; it does not
prevent a fresh private computation or the downstream handoff of a proved C.

## Next lifecycle integrations

- **TASK-199 (REWORK):** Keep predecessor review/report and generation
  admission, `ResourceBinding.require_worker_retirement` and the live
  predecessor or evidence-ref proof. Use `_assert_repository_owner` and
  `_git_runtime`. Replace `ExecutionWorkspaceManager.prepare_rework`'s
  ancestry/delta port, `patch_equivalence`/count-based replay and its
  cherry-pick/abort after retirement. Split `ResourceBinding.prepare_rework`
  so `rework_source_absent` and the `rework-started` retirement effect are no
  longer used to prepare a candidate. Retain retirement, generation creation
  and publication receipts/CAS as effects of downstream handoff after ready.
- **TASK-200 (active reconciliation):** Keep `admit_update_base`,
  `reconciliation_workspace_binding`, `reconciliation_evidence_matches`, the
  `ResourceBinding` proof, and product/topology/publication authority. Replace
  the in-place rebase/abort in `start_fresh_rewrite` and the transform inference,
  abort and restoration in `recover_local_pending`. Narrow
  `recover_remote_pending` to downstream local/remote publication receipt
  replay from exact C, rather than proving an in-place transform from mutable
  workspace state. Keep displaced/rewritten evidence pins, remote lease/CAS,
  and `_resolve` downstream. Both origins persist the same transfer attempt in
  their existing WAL and hand off only from the proved result.

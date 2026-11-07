---
"type": "devlegate.ticket"
"title": "Durably bind fresh execution resources to a verifiable generation"
"depends_on": ["TASK-125"]
---

## Context and authority

Fresh implementation of the original objective of archived TASK-134, following
the operator-approved 2026-11-05 retirement decision. Begin from the currently
integrated product, not from historical TASK-134 worker checkpoints. The
TASK-145/147/149 deferred-continuation mechanism has been reverted.

The archived ticket, historical Git refs/worktree, execution reports, reviews,
and recovery record are evidence to preserve, **not** execution input, current
ownership authority, or code to cherry-pick. This ticket receives a new
execution ID and ordinary fresh-ticket admission.

## Goal

Give newly admitted executions a durable, generation-specific resource binding
that distinguishes owner-created resources from merely identical-looking
worktrees and local/upstream Git refs. Expose a read-only proof of the binding
for the future operator reset preview in TASK-129. Do not implement reset,
generic historical continuation, or destructive non-done cleanup here.

## Required contract

1. **Evidence and limits.** Specify the observable trust boundary before
   implementing it. Persisted SQLite intent, Git path, branch name, and SHA
   alone do not prove uninterrupted resource identity. In particular, plain
   Git remote observation cannot distinguish deleting and recreating a ref at
   the same name and SHA. Do not claim this is provable without additional
   independent evidence or an explicit, enforceable non-reuse assumption.
   Report unsupported or uncertain proofs as unproven, with a precise reason.
   Distinguish owner intent, independently observed resource identity, and
   authority to mutate; none implies the others.

2. **Durable admission and creation.** Under the existing serialized service
   owner, record a fresh binding/creation intent before its first resource
   effect. Bind exact ticket ID, execution ID, product base, control admission
   commit and ticket blob, expected worktree path/Git common directory,
   branch/ref names, expected absence or exact prior state, and a
   generation-specific non-reusable token or equivalently strong identity.
   Define WAL/state ordering and retry rules for each operation. No worker
   launch before the newly created resources satisfy the proof.

3. **Independently checkable identity.** Persist sufficient owner-controlled
   evidence outside the single mutable runtime state record to detect
   lost/replaced/recreated local worktrees and mismatched generations, even
   when branch/path/HEAD SHA are identical. Validate actual Git registrations,
   paths, symlinks, locks, cleanliness, submodules, local refs, and exactly
   observed upstream refs under the existing Git capability boundary. For
   upstream identity, either establish a genuinely verifiable generation
   anchor with bounded scope, or explicitly classify continuity as unknown;
   never infer identity from matching SHA. Avoid a new generic VCS layer or
   broad isolated-generation continuation implementation.

4. **Lifecycle and crash recovery.** Preserve binding across preparation,
   checkpoint/publication, worker handoff, restart, retry, and ordinary
   accepted integration. Reobserve at use boundaries. A crash after an
   external effect but before a receipt must recover only from exact proof;
   ambiguous creation, conflicting/foreign resources, lost markers, changed
   refs, missing remote observation and a second owner must fail closed.
   Never fabricate an old binding, adopt an unowned generation, or overwrite
   existing resources because they happen to match a historical SHA.

5. **Read-only internal proof API.** Provide TASK-129 with a small structured
   result identifying the exact execution/resource generation, proof state
   (proved, unproven, conflict), and actionable reason. Its read path must
   not create/fetch/reset refs, write SQLite, or alter a worktree. A proof
   snapshot is information, never independent authorization for later
   destructive actions; revalidation remains mandatory at apply time.

6. **Non-interference.** Preserve existing automatic cleanup policy, report
   and review provenance, TASK-073 human acceptance, and other currently
   hosted projects. In particular, never alter or remove the archived
   TASK-134 source branch, worktree, reports, reviews or evidence refs.

## Acceptance and tests

- Real-Git disposable fixtures for fresh binding, clean restart, stable
  reobservation, same-SHA resource recreation, missing/changed markers,
  mismatched branch/path/common dir, replaced registration, dirty/locked/
  symlinked or foreign worktree, submodule changes, remote ref drift,
  unavailable remote, and competing owner.
- Fault injection at each admission, local creation, ref publication,
  checkpoint, worker-launch and receipt boundary; prove idempotence or an
  explicit safe block without unintended effects.
- Test a new unrelated execution after a prior generation with the same
  ticket-scoped path/branch and commit SHA: the prior identity must never be
  silently transferred.
- Test the read-only proof API makes no mutations and agrees with owner
  admission and recovery decisions. Assert no changes to archived TASK-134
  resources or immutable reports.
- Document the identity/evidence model, its remote-Git limitation, recovery
  and operator implications. Run focused tests, full configured CI and Ruff.

## Boundaries

Stdlib-only runtime as practical. No compatibility or historical state
migration, no one-off TASK-134 conditional in production, no reset CLI,
no new deletion authority, no generic deferred failed-execution replay,
no automatic non-done retirement, and no unrelated refactoring. Prefer
a small, reviewable change to existing owner, workspace and recovery
boundaries over a new subsystem. Do not mark complete on a purely
state-recorded binding without independently checkable resource evidence.

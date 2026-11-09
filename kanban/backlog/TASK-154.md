---
"type": "devlegate.ticket"
"title": "Expose bounded virtual revision and diff exploration to workers"
---

## Scheduling
**Deferred architectural follow-up. Keep in backlog until explicitly scheduled.**
This is not a prerequisite for TASK-153 and must not expand that regression fix.
Related to TASK-152's future capability isolation, but this ticket does not itself
claim to implement OS-enforced agent sandboxing.

## Problem
Devlegate owns Git/checkpoint/integration while workers operate in ephemeral
task worktrees. A REWORK worker may see current files and review feedback but
cannot be assumed to know its cumulative delta from the integrated base or the
changes in earlier attempts. Requiring the worker to use raw Git commands or
commit/execution hashes would violate the intended agent-facing abstraction.

## Goal
Expose a narrowly scoped, **read-only Change Explorer** to implementation
workers. It should describe available task revisions and return factual,
bounded comparisons without granting Git lifecycle/ref authority or assuming
Git will always be the sole VCS.

## Worker-facing revision vocabulary
- `BASE`: current task branch's base on the managed target branch.
- `HEAD`: latest completed, Devlegate-recorded checkpoint of the task line.
- `HEAD-N`: N preceding **task checkpoints**, not raw VCS parent offsets;
  stop at the task boundary. A session with no code checkpoint adds no revision.
- `WORKTREE`: current editable workspace contents, including uncommitted and
  newly created files (a mutable comparison endpoint, not a stored commit).
- `TARGET`, if useful: latest observable state of the managed integration
  branch; never silently substitute it for `BASE`.

No `PREVIOUS` alias, execution ID, commit hash, Git ref, or raw Git argument
is required in the **worker-facing** interface. Devlegate may retain such
identities internally for provenance, authorization and consistency. Describe
rebase/checkpoint alias behavior explicitly: a prior worker checkpoint can be
historically retained even when Git parentage was rewritten; do not silently
traverse into unrelated target commits. Squash integration is acceptable and
must not force the entire worker commit history onto the target branch.

## Minimal operations
1. `revisions()`: cheap, deterministic inventory of accessible virtual
   revisions and task-checkpoint counts. Provide factual kind/availability/
   size metadata, not invented semantic descriptions or internal IDs.
2. `changes(from, to)`: pageable inventory of added/modified/deleted/renamed
   paths, text line counts where meaningful, byte sizes, binary/oversized
   indicators, and exact before/after change kinds. No project-specific
   production/tests classification by default.
3. `diff(from, to, path?, cursor?)`: exact, bounded unified text diff for a
   selected path or page/hunk, including explicit unavailable/truncated
   statuses. Avoid reconstructing a huge whole-branch patch before limiting
   output.

## Constraints
- Strictly read-only; tool resolves virtual names through owner-held metadata
  and a backend that initially uses Git. No arbitrary shell/Git commands,
  arbitrary refs, Git mutations or exposure of managed control repositories,
  secrets or other task workspaces.
- Protect against oversized or binary content: list changes and sizes without
  emitting binary patches. Bounded output and work, pagination with opaque
  cursors, stable comparison observations or explicit stale-state rejection
  when `WORKTREE` changes between pages.
- Expose deterministic facts, not LLM-generated interpretation. Git commit
  messages and report prose, if ever exposed, must be marked as unverified
  author-supplied descriptions.
- Work with a single currently managed target branch, whichever branch name
  Devlegate configured, and with a task line rebased or squashed by the owner.
  Do not assume `main`/ `master`, Python, `src/`, `tests/` or one
  commit per arbitrary VCS history event.
- Prefer extending existing stdlib-oriented owner/provider/tool contracts;
  no new VCS framework, migration path, hardcoded toolchain, or pre-generated
  multi-megabyte Change Context.

## Acceptance
Use disposable repositories/workspaces to prove first execution, multiple
checkpoints and a zero-change session, REWORK cumulative BASE→WORKTREE diff,
target advancement/rebase, squash, non-ancestor retained checkpoint, unrelated
commits, untracked files, large text changes, changed binary files, stale
paged WORKTREE observations and tool-access limits. Validate default concise
responses and focused/full project checks.

## Boundaries
Backlog only. Do not modify TASK-153, existing execution records, historical
worker checkpoints or real operator assets while implementing this later.

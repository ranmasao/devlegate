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
- `HEAD`: latest durable Devlegate-recorded task code checkpoint, including one
  created after a failed or transport-disconnected execution; before the first
  code checkpoint, `HEAD` aliases `BASE`.
- `HEAD-N`: N preceding **actual task checkpoints**, not raw Git parents,
  executions or model sessions. Zero-change sessions add no revision; stop at
  the task boundary without inventing an extra `HEAD-N` alias for `BASE`.
- `WORKTREE`: current editable files, including staged, unstaged and eligible
  non-ignored untracked files (a mutable endpoint, not a stored commit).
  Do not expose the Git staging index as a separate worker-facing state.
- `TARGET`, if useful: latest observable state of the managed integration
  branch; never silently substitute it for `BASE`.

No `PREVIOUS` alias, execution ID, commit hash, Git ref, or raw Git argument
may be exposed in the **worker-facing** interface. Devlegate may retain such
identities internally for provenance, authorization and consistency. Describe
rebase/checkpoint alias behavior explicitly: a prior worker checkpoint can be
historically retained even when Git parentage was rewritten; do not silently
traverse into unrelated target commits. Squash integration is acceptable and
must not force the entire worker commit history onto the target branch.
Devlegate's technical rebase/replay commits are not additional worker-session
checkpoints. Cross-base comparisons must not silently attribute target-branch
changes to the worker.

## Minimal operations
1. `revisions()`: cheap, deterministic inventory of accessible virtual
   revisions and task-checkpoint counts. Provide factual kind/availability/
   metadata, not invented semantic descriptions or internal IDs.
2. `changes(from, to)`: pageable inventory of added/modified/deleted/renamed
   paths, file type/mode changes, available before/after byte sizes, and
   binary/oversized indicators. Supply text added/deleted counts only when
   actually computed; unknown is not zero. No project-specific
   production/tests classification by default.
3. `diff(from, to, path?, cursor?)`: exact, bounded unified text diff for a
   selected path or page/hunk, including explicit unavailable/truncated
   statuses. Broad unscoped requests must not cause an unbounded response.
   Avoid reconstructing a huge whole-branch patch before limiting output.

## Checkpoint, restart, and working-directory semantics
- Losing model connectivity need not end a Devlegate execution; the harness
  may reconnect. Once Devlegate has **proven worker retirement**, a failed
  transport/egress/process execution may still produce a durable checkpoint
  containing surviving edits, without implying semantic completion or review.
  Sessions without changed code do not create extra revisions.
- `diff(HEAD, WORKTREE)` means all current content changes since the latest
  checkpoint (analogous to `git diff HEAD`, **plus eligible untracked files**).
  `diff(BASE, WORKTREE)` is the cumulative task delta. Hide the staging index.
- A read-only diff tool does not authorize retry, resume, worker relaunch,
  recovery, staging or commit. If old-worker ownership is unresolved, an
  unsafe mutable worktree must not become an execution authorization just
  because the tool can list some of its files.

## Oversized, binary, and nonregular files
- **File size**, **size of the resulting patch**, and **computational cost**
  are independent. Enforce service-owned bounded computation (including
  time, memory, I/O and subprocess output) separately from bounded response
  size. A large text file may have a cheap small diff; try to serve it when
  economically feasible, otherwise return an explicit `TOO_LARGE` or
  `TOO_EXPENSIVE` outcome without hiding the file from `changes()`.
  Output pagination does not itself bound the cost of diff construction.
  Beware giant add/delete and very long text lines.
- First-version binary policy: inventory a changed binary's factual
  added/modified/deleted/renamed state, before/after sizes where cheap,
  type and mode changes. Do **not** emit binary Git patches, raw byte
  fragments or hex dumps; `diff()` reports `BINARY_DIFF_UNAVAILABLE`.
  Equal file size never proves identical content. Binary files unchanged
  between the compared revisions must not be scanned just because they
  exist elsewhere in repository history. Domain-aware archive/image/
  database/model inspection belongs to separate specialized tools.
- Distinguish regular text, binary or unknown, symlinks and submodules.
  Inspect symlink targets as link text only (never follow the symlink);
  expose submodule changes without traversing their history. Do not
  classify solely by extension. A tracked Git LFS pointer is not proof
  that the referenced external asset has been read.
- If a text comparison cannot be completed within the work budget, report
  unavailable or incomplete explicitly; do not claim a truncated patch
  is complete, silently skip changed files or replace unknown line counts
  with zero. Do not promise arbitrary hunk/page navigation over giant
  files without a bounded algorithm to find those hunks.

## Constraints
- Strictly read-only; tool resolves virtual names through owner-held metadata
  and a backend that initially uses Git. No arbitrary shell/Git commands,
  arbitrary refs, Git mutations or exposure of managed control repositories,
  secrets or other task workspaces. Disable external diff/textconv execution;
  enforce path containment for untracked files and symlinks.
- Protect oversized/binary content as specified above. Use opaque page
  cursors and stable, bounded comparison observations, or explicit stale-state
  rejection when `WORKTREE` changes. A stat-only check is not proof that
  mutable content is byte-identical; if stable observation is unaffordable,
  reject cross-page consistency claims.
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
commits, staged/unstaged/untracked and ignored files, failed and zero-delta
checkpoint sessions, oversized text/very long lines, large files with small
diffs, changed equal-size binaries, unchanged historical binaries, symlinks,
submodules, unknown (not zero) statistics, stale paged WORKTREE observations,
computation/output limits and tool-access limits. Assert no Git/execution
identities leak and tool reads cannot stage, retry or authorize a worker. Validate default concise
responses and focused/full project checks.

## Boundaries
Backlog only. Do not modify TASK-153, existing execution records, historical
worker checkpoints or real operator assets while implementing this later.

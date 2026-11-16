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
2. `changes(from, to, cursor?)`: pageable inventory of added/modified/deleted/
   renamed paths, file type/mode changes, available before/after byte sizes,
   and binary/oversized indicators. This operation NEVER returns file bodies,
   patches, hunks or snippets. Supply added/deleted line counts only when
   actually computed; unknown is not zero. No project-specific production/tests
   classification by default. Pagination must be deterministic for unchanged
   comparison inputs, without hiding changed paths on later pages.
3. `diff(from, to, path, cursor?)`: exact, bounded unified text diff for
   **exactly one required file path** per call. `path` must be an explicit
   repository-relative file path (not a directory, glob, wildcard, path list,
   absolute path, or arbitrary Git pathspec). For symlinks, compare link text
   without dereferencing; for submodules, expose a factual inventory change
   without traversing history or presenting a text patch. No unscoped or
   multi-file diff API exists. Split a large file diff into honest, bounded
   response pages/hunk segments with unambiguous progress and continuation;
   a segment need not be an independently applicable patch. Never build an
   unbounded whole-branch patch merely to return one file/page.

## Checkpoint, restart, and working-directory semantics
- Losing model connectivity need not end a Devlegate execution; the harness
  may reconnect. Once Devlegate has **proven worker retirement**, a failed
  transport/egress/process execution may still produce a durable checkpoint
  containing surviving edits, without implying semantic completion or review.
  Sessions without changed code do not create extra revisions.
- `changes(HEAD, WORKTREE)` lists paths changed since the latest checkpoint;
  `diff(HEAD, WORKTREE, path)` provides that ONE file's content delta (including
  eligible untracked files). `changes(BASE, WORKTREE)` and
  `diff(BASE, WORKTREE, path)` expose cumulative task changes. Hide the staging
  index; callers need not know whether edits are staged or unstaged.
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

## Transport, ownership and role boundary

- Expose three separate agent tools `revisions`, `changes`, and `diff`
  through a thin OpenCode tool adapter or MCP adapter. The worker-facing schema
  is transport-neutral, and the backend is a Devlegate-owned Python component
  using its existing read-only Git observations. An OpenCode adapter is a
  minimal first delivery; introducing a mandatory MCP server, a speculative
  generic VCS provider or a new runtime dependency is unnecessary.
- The privileged backend must remain **outside the worker sandbox**. A child
  launched by the Landlock-restricted worker inherits those restrictions and
  cannot act as a privileged Git reader. Bind every request to a current
  authorized worker execution, its exact managed workspace and permitted
  virtual revision namespace; do not take repository paths, control identities,
  Git refs, arbitrary commands or worker-supplied execution identifiers as
  authority. Permit only the three named, read-only operations on that endpoint.
- TASK-152 separately owns actual kernel/OS capability isolation, including
  Landlock. TASK-154 does NOT claim that a custom tool or MCP protocol makes
  direct `git`/shell access impossible. Tool availability may be built and
  tested independently of full isolation, but neither privileged IPC access
  nor OS isolation can be inferred merely from tool descriptions. Reject
  requests outside the active authorization boundary without side effects.

## Response size, compute budget and pagination

**Provisional first-version defaults**, evaluated with focused real-world traces;
they are bounded service-owned policy, not agent-controlled ways to request more:

| Scope | Default limit |
| --- | --- |
| `changes()` | At most 30 file records per page, with an opaque cursor |
| `diff()` | At most 8 KiB of serialized diff payload AND 120 text lines per page; whichever is reached first |
| Total Change Explorer response payload across an execution | At most 64 KiB, counting pages of all three tools, not a fresh quota for every call or reconnect |
| Individual line | A separately enforced bounded line size; reject with `LINE_TOO_LONG` rather than return a huge line or misleading partial text |
| Backend effort | Independent configured limits on elapsed work, CPU/memory, bytes read, and subprocess/output buffering; no unlimited diff construction prior to pagination |

- Response budgets measure **serialized UTF-8 bytes including metadata** and
  exclude no-cost assumptions about tokenizer internals. They are conservative
  context-budget proxies, not promises of an exact LLM token count. A useful
  small file diff may fit even when the full file is large. Avoid rejecting
  solely on whole-file size when a bounded algorithm can produce a cheap
  small diff.
- Enforce the aggregate budget per **execution**, including repeated pages,
  tool retries and reconnect/restart. Do not let a fresh adapter process or
  a changed tool name replenish it. Account only actual delivered response
  bytes, consistently across delivery failures; specify the durable metering
  boundary without granting the worker state-mutation authority. Budget
  accounting does not authorize Git/resource changes.
- Every pageable reply explicitly identifies `complete` / `has_more`,
  supplies `next_cursor` only if a continuation is valid, and distinguishes
  omitted information from a complete empty diff. Return stable,
  deterministic pages for immutable revisions. For mutable `WORKTREE`, bind
  opaque cursors to a verified stable observation or fail with `STALE_CURSOR`;
  timestamps and sizes alone do not prove equal content. Cursors may not be
  forged to skip authorization or bypass budgets.
- Ordinary pagination is not an error: `has_more=true` means a partial
  answer. If limits prevent a meaningful page, return an explicit bounded
  failure. Use clear machine statuses such as `TOO_LARGE`,
  `TOO_EXPENSIVE`, `BINARY_DIFF_UNAVAILABLE`, `LINE_TOO_LONG`,
  `STALE_CURSOR` and `BUDGET_EXHAUSTED`; if any partial payload is returned
  under an error/truncation status, mark it as incomplete, not authoritative
  full history. Unknown file statistics must remain unknown, never zero.
- All output paths, revision labels, cursors and diff headers must be
  worker-safe: no leaked Git object IDs, refs, execution IDs, external paths,
  control state, privileged endpoint identities or credential material. The
  change inventory remains accessible even when a file's content diff is
  unavailable or too expensive.

## Agent-facing contract and selective reading

The actual tool schemas/descriptions must carry concise operational guidance
in addition to ordinary safety constraints, so the worker sees it when tools
are presented. Normative descriptions, subject to exact adapter syntax:

- **`revisions()`:** List available virtual task checkpoints and endpoints.
  These are task states, not raw Git commits; use the inventory to select only
  useful comparisons.
- **`changes(from, to, cursor?)`:** List *only paths and metadata* of changed
  files. Prefer this before requesting individual diffs. Page only as far as
  necessary for the task. This tool never returns code or patch text.
- **`diff(from, to, path, cursor?)`:** Return a bounded text diff of *one
  explicit file*. Identify the file using `changes` first, then request
  further pages only if needed for the current unresolved requirement.
  A page with `has_more=true` is not the full diff. Binary, oversized and
  computationally expensive diffs may be unavailable; do not infer absence
  of changes from an unavailable page.
- Add at most one brief general Worker reminder if needed: prefer selective
  inspection that advances the primary ticket blocker; avoid exhausting every
  diff page by habit. Do not duplicate the complete tool-use instructions in
  the core contract or introduce a new planning subsystem (TASK-146 handles
  general worker prioritization).

## Constraints
- Strictly read-only; tool resolves virtual names through owner-held metadata
  and a backend that initially uses Git. No arbitrary shell/Git commands,
  arbitrary refs, Git mutations or exposure of managed control repositories,
  secrets or other task workspaces. Disable external diff/textconv execution;
  enforce path containment for untracked files and symlinks.
- Protect oversized/binary content as specified above. Never treat an
  output-size limit as a sufficient computational bound. Use opaque page
  cursors and stable comparison observations, or explicit stale-state
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
computation/output limits and tool-access limits. Assert no Git/execution identities leak and tool reads cannot stage, retry or
  authorize a worker. Additionally test that `diff` rejects absent path,
  multi-file requests, wildcards, directories, absolute/traversal paths,
  symlink escape and other workspaces; `changes` never returns patch bodies;
  each page meets both byte and line caps; continuation does not repeat/skip
  content for stable revisions; `WORKTREE` mutation yields `STALE_CURSOR`;
  aggregate budget survives retries/reconnect/restart and fails closed;
  oversized lines, large low-delta files, forged cursors, expensive diff
  construction and binary/unavailable patches report precise statuses.
  Exercise the three actual tool descriptions through the adapter and confirm
  their selective-reading/pagination guidance is present. Validate default
  concise responses and focused/full project checks.

## Boundaries
Backlog only. Do not modify TASK-153, existing execution records, historical
worker checkpoints or real operator assets while implementing this later.

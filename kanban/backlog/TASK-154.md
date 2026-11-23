---
"type": "devlegate.ticket"
"title": "Expose bounded virtual revision and diff exploration to workers"
---

## Milestone

Devlegate 0.5.8 — bounded read-only Change Explorer for implementation workers.

**Scheduling: keep in backlog until explicitly scheduled.** This ticket is
independent of TASK-153 and does not implement TASK-152's kernel/OS worker
isolation. Its interface may be delivered before that isolation is complete.

## Goal

Give FRESH/REWORK/RESUME workers a selective way to inspect their own task's
current and historical code changes without learning Git object IDs, refs,
execution identifiers or repository lifecycle commands. Deliver four focused
read-only tools, including an on-demand Git-compatible diff hygiene check, not
a second Git audit, snapshot or integrity subsystem.

## Context

Devlegate owns checkpoint publication, Git refs and integration. Workers may
need to compare their cumulative changes against the admitted product base,
their last checkpoint, or an earlier attempt. The worker-facing interface must
be independent of Git terminology beyond virtual task revisions, while its
first backend may use Git directly.

`WORKTREE` is a **live, mutable view**, not an immutable revision or a
security attestation. Its contents are already governed by the existing
execution/workspace ownership boundaries. Change Explorer reports what the
authorized workspace contains when inspected; it is not responsible for
independently proving that the worker never touched the Git index, establishing
an immutable snapshot, or authorizing later checkpoint/integration operations.

## Required behavior

### Virtual revision vocabulary

- `BASE` — the admitted task's effective base on the configured target branch.
- `HEAD` — latest durable Devlegate-recorded task **code checkpoint**, including
  one retained after a failed/disconnected execution. Before the first such
  checkpoint, `HEAD` aliases `BASE`.
- `HEAD-N` — N preceding actual task code checkpoints, not Git parents,
  sessions or arbitrary commits. Zero-change sessions create no new revision;
  enumeration stops at the task boundary without inventing a `HEAD-N` for `BASE`.
- `WORKTREE` — current editable task files, including staged, unstaged and
  eligible non-ignored untracked files. The Git index is **not** an exposed
  revision or separate agent-facing state.
- `TARGET` is optional; if supported, it is the latest observed integration
  branch state and must not silently replace `BASE`.

Only these virtual labels and bounded factual metadata are visible to workers.
No raw Git ref, SHA, execution ID, control identity, arbitrary revision string
or path supplied by the worker may become Git authority.

Devlegate resolves historical checkpoint identities from existing owner-held
evidence. Rebase/replay commits do not become additional worker checkpoints;
retained checkpoints may cease to be ancestors after a rebase. Cross-base
comparisons must not silently present unrelated target changes as worker edits.
Squash integration remains valid.

### Four tools

1. `revisions()`: return the available virtual endpoint/checkpoint inventory
   and factual availability/count metadata; no invented semantic summaries.
2. `changes(from, to, cursor?)`: return only a bounded, pageable inventory of
   changed repository-relative paths: add/modify/delete, rename when Git can
   establish it cheaply, file type/mode, before/after sizes when available and
   binary/unavailable flags. No file bodies, code snippets, hunks or patches.
   Unknown statistics remain unknown, not zero.
3. `diff(from, to, path, cursor?)`: return a bounded unified **text** diff for
   exactly one explicitly named repository-relative file. Reject absent,
   multi-file, directory, glob, wildcard, absolute, traversal and arbitrary
   Git-pathspec arguments. No unscoped branch patch API. A page must clearly
   distinguish a complete diff from a partial diff; when a useful bounded page
   cannot be produced economically, return a clear unavailability status.
4. `check(from, to, cursor?)`: perform an **on-demand, non-mutating, diagnostic**
   check of introduced changes for Git-compatible whitespace errors and
   unresolved conflict markers, corresponding to `git diff --check`. Reuse
   Git's own diff/check semantics where practical, including the repository's
   applicable `core.whitespace` policy; do not approximate them with a generic
   linter or flag all whitespace-only/style changes as errors. Return bounded
   repository-relative paths, line numbers, diagnostic kinds, and short
   offending-line excerpts when safe and available. Distinguish `clean`
   (fully checked and no findings), `findings`, `partial`, and
   `unavailable`; a truncated or failed scan must **never** claim clean.
   The result is advice for the worker, **not** an acceptance veto or automatic
   source-code correction. A legitimate project/style convention may intentionally
   use whitespace that Git flags; the worker must be able to inspect and decide.

`check(HEAD, WORKTREE)` examines changes since the last task checkpoint,
whereas `check(BASE, WORKTREE)` covers cumulative work. Like the virtual
`changes` and `diff` operations, a WORKTREE comparison includes eligible
tracked staged/unstaged and non-ignored untracked files; this is an explicit
scope extension compared with bare `git diff --check`, which normally checks
only unstaged tracked changes. Preserve Git's *diagnostic rules*, not its
implicit index-only scope. Untracked files must be checked safely without
modifying/staging the index; an uncheckable/expensive input must be reported as
unavailable rather than silently omitted. Do not expose arbitrary Git options,
external path selectors, command execution, or a fifth independent validation
subsystem.

`changes(HEAD, WORKTREE) and `diff(HEAD, WORKTREE, path)` show changes since
the latest checkpoint. `BASE` to `WORKTREE` shows cumulative task changes,
including eligible untracked files. Use ordinary Git/worktree observations to
combine staged and unstaged changes without exposing index internals. Do **not**
implement independent index-tamper detection as a Change Explorer obligation.

The tools never fetch, stage, commit, reconcile, launch, retry, auto-fix or otherwise
modify Git or workflow state. Their output cannot grant worker admission,
retirement, checkpoint validity or integration permission.

### WORKTREE observation and paging

- `WORKTREE` is observed on demand, using ordinary bounded Git status/diff and
  safe file reads where needed. Do not independently attest it by rehashing
  every file, scanning it twice or comparing an additional synthesized
  snapshot against the original. No separate WORKTREE integrity record.
- Content can change between calls or pages while a worker edits files.
  Return a clearly marked **live/best-effort observation**; pages involving
  `WORKTREE` are not promised to form an atomic snapshot or an exact,
  cross-call consistent history. A cheap detected invalid continuation may
  return `STALE_CURSOR`, but proving that all mutable bytes remain identical
  is explicitly **not required**. Do not use stat-only equality to claim such
  proof either.
- Cursor continuation must remain tied to the authorized query and bounded
  output, and cannot be forged to expand access or quotas. Immutable revision
  comparisons should paginate deterministically without missing or repeating
  records. For changing `WORKTREE` inputs, document that re-querying may be
  necessary; never label an incomplete page as a complete snapshot.
- These inspection rules are separate from existing strict worker ownership,
  checkpoint, Git publication and reconciliation proofs. If the service cannot
  safely identify the calling worker and its workspace, fail closed rather
  than allowing arbitrary filesystem reads.

### Special files and submodules

- Symlinks are represented by link text, never dereferenced. Reject paths that
  escape the authorized repository or enter another workspace.
- Submodules are **single gitlink entries in the parent repository**.
  Report parent-level add/delete/pointer/type changes and optional rename
  evidence. The file `.gitmodules` is an independent ordinary text file.
  Never enter submodule Git history, follow its remotes or return nested
  submodule file patches, refs, URLs or object IDs.
- For `WORKTREE`, ordinary shallow parent Git status may indicate submodule
  checkout drift, dirtiness or unavailable/uninitialized state. Report only
  what can be observed cheaply; unknown is not clean. No independent
  three-way pointer/index/checkout attestation or recursive nested scan is
  required. Staged parent gitlink changes still count as parent-level changes.
  Uncommitted nested edits are **not** saved by a superproject checkpoint;
  make this limitation clear in the tool guidance.
- `diff` on a submodule path yields a bounded structural summary and
  `SUBMODULE_CONTENT_UNAVAILABLE`. Reject a path inside a tracked submodule.
- Binary changes remain visible in `changes`; `diff` reports
  `BINARY_DIFF_UNAVAILABLE` rather than raw bytes/hex patches. Do not classify
  solely by file extension or assume equal sizes mean equal contents. No
  scanning of unrelated unchanged historical binaries. A Git LFS pointer
  does not imply external LFS content was read.

### Bounded output and reasonable computation

Retain first-version response limits as **service-owned defaults**:

| Scope | Limit |
| --- | --- |
| `changes()` | 30 path records per page |
| `diff()` | 8 KiB serialized payload and 120 text lines per page, whichever is reached first |
| `check()` | 8 KiB serialized output and 30 diagnostic records per page, whichever is reached first |
| All four tools combined | 64 KiB serialized output budget per execution |
| Single line | Bounded; overlong lines report `LINE_TOO_LONG` |

Count serialized UTF-8 bytes including metadata. Apply the aggregate execution
budget across ordinary calls/reconnects through existing owner-held accounting;
a tool process restart must not grant a fresh full budget. Prefer straightforward
accounting at the trusted response boundary rather than exactly-once delivery
receipts or a new metering subsystem.

The backend must avoid unlimited buffering, unbounded whole-branch diff
construction and recursive workspace traversal. Use safe command options,
bounded reads/output and the existing execution infrastructure. Expensive
inputs may return `TOO_EXPENSIVE` or `TOO_LARGE` rather than forcing a giant
diff into memory just to paginate it. Keep timeout/effort policy pragmatic and
configurable if needed; **do not require a separate fixed 3-second/128-MiB
sandbox or an independently metered CPU/memory/I/O attestation pipeline**.

Every response distinguishes `complete`, `has_more` and valid continuation.
Unknown file/line statistics are not fabricated. Normal pagination is not
an error; bounded failures such as `BUDGET_EXHAUSTED`, `TOO_EXPENSIVE`,
`LINE_TOO_LONG` or `BINARY_DIFF_UNAVAILABLE` are explicit and must not
masquerade as an empty or complete diff. Immutable comparisons should provide
stable pages when inexpensive to do so.

### Tool authority and delivery

- Implement a small Devlegate-owned Python read-only backend plus a thin
  OpenCode adapter exposing `revisions`, `changes`, `diff` and `check`; a mandatory MCP
  server, generic VCS framework and new runtime dependency are out of scope.
- Keep privileged Git observation **outside** the worker's Landlock-restricted
  process. Bind tool calls to a currently authorized execution, its exact
  managed task workspace and the permitted virtual revision namespace using
  Devlegate-held identity, not caller-supplied IDs, paths, Git refs or commands.
  The agent must not be able to turn a read request into privileged Git access
  to other tickets, control, product secrets or external paths.
- TASK-152 owns OS-enforced prevention of direct worker Git/shell access. A
  custom tool does not itself constitute that isolation; test it independently
  without claiming more protection than implemented.
- Tool descriptions should succinctly direct workers to list revisions,
  inspect changed paths first, request only specific file diffs/pages, and
  invoke `check` when they need the equivalent of `git diff --check`.
  Explain the role-to-tool mapping in concise, task-oriented terms rather than
  asking the worker to call raw Git: `changes` for status/path inventory,
  `diff` for a file patch, `check` for introduced whitespace/conflict
  diagnostics. Do not treat check findings as an automatic failure, and do not
  force an exhaustive check on every query. No automatic exhaustive page
  reading or pre-generated multi-megabyte Change Context.

## Acceptance criteria

1. A worker can inspect available `BASE`, `HEAD`, older real checkpoints
   and its current `WORKTREE` through exactly four read-only tools, with no
   raw Git/execution identities or additional index-facing revision.
2. `changes` lists factual paths/metadata without code; `diff` requires one
   valid file path and returns bounded text, explicit non-text status or
   explicit cost/unavailability. Both work for first execution and cumulative
   REWORK changes.
   `check` returns Git-compatible, configurable-whitespace/conflict-marker
   diagnostics with exact relative location and explicit completeness, without
   modifying source or Git state. A finding is not automatically disqualifying;
   benign style/whitespace changes need not be treated as errors.
3. Staged, unstaged and eligible untracked worker edits appear without extra
   hashing, double scanning, independent WORKTREE attestation or index-tamper
   detection. A mutable observation is labeled accordingly and does not claim
   cross-page snapshot consistency.
4. Historical checkpoint enumeration respects actual durable task checkpoints,
   zero-change sessions, rebase/replay, squash and rewritten ancestry without
   folding in unrelated product history.
5. Tool authorization prevents access to other workspaces and privileged
   control identity; reads do not modify Git or authorize workflow transitions.
6. Response limits, bounded expensive-input handling, clear pagination and
   per-execution output quotas are enforced without a new generic audit,
   resource accounting or VCS subsystem.

## Required regressions

Use disposable repositories/workspaces and targeted adapter/owner tests:

- First execution (`HEAD` aliases `BASE`), multiple checkpoints, zero-code-delta
  session, failed/disconnected checkpoint retained after proven retirement,
  REWORK cumulative changes and a non-ancestor checkpoint after rebase.
- Staged + unstaged + untracked edits, ignored-file exclusion, `HEAD` versus
  `WORKTREE` and `BASE` versus `WORKTREE`; verify no separate index revision,
  double file hash, full-tree verification or mutation is required.
- Immutable page continuation is stable. A file changing between `WORKTREE`
  pages produces a documented best-effort view or a cheap `STALE_CURSOR`
  rejection; do not test/require content-hash snapshot consistency.
- Reject missing `diff` path, globs, directories, traversal, absolute paths,
  symlink escape, cross-ticket/workspace access, forged cursor and unauthorized
  worker identity. Verify no source Git IDs, refs or execution IDs leak.
- Parent submodule gitlink add/remove/update and `.gitmodules` change;
  shallow dirty/uninitialized/unknown state when observable, no nested
  traversal, and `SUBMODULE_CONTENT_UNAVAILABLE` for the submodule path.
- Large text with small diff, huge line, binary same-size modification,
  unchanged historical binary and expensive comparison. Results must remain
  bounded and explicit about unknown/unavailable data.
- `check` against clean, whitespace-only but acceptable, trailing-whitespace,
  blank-at-EOF (where configured), space-before-tab, and conflict-marker
  changes; compare results with Git's `--check` on the same tracked revisions
  under `core.whitespace`. Cover Markdown-style intentional trailing spaces,
  staged+unstaged and eligible untracked WORKTREE changes, binary/submodule
  unavailable cases, output truncation, failures and no false `clean` on
  partial observations. Verify `check` does not stage, mutate, fix or block.
- Aggregate response cap across ordinary calls and reconnects, per-page
  byte/line/path/diagnostic caps, and the four actual OpenCode tool descriptions.
  Existing full project CI and lint remain the quality gate.

## Boundaries

Modify only the Change Explorer and necessary narrowly scoped integration,
tests and documentation. No changes to TASK-153, TASK-152, other tickets,
historical execution/review evidence, active worker resources or real operator
assets. No migrations, speculative VCS abstraction, independent Git forensic
audit, mandatory WORKTREE snapshot/hash pipeline, OS sandbox implementation
or generic retry/recovery mechanism.

# Inspect task changes with Change Explorer

Implementation workers can use four read-only tools:

- `revisions()` lists the available virtual revisions. `BASE` is the admitted
  product base. `HEAD` is the last durable task checkpoint (or `BASE` before the
  first checkpoint); `HEAD-1`, `HEAD-2`, and so on are actual older task
  checkpoints. Unchanged runs and replay commits do not add checkpoints.
- `changes(from, to)` is the path inventory: see changed paths, kinds, modes,
  and available sizes before asking for a patch. It does not return file text.
- `diff(from, to, path)` shows the unified text diff for one explicit
  repository-relative file. It cannot return an entire branch patch or nested
  submodule content. Binary and expensive diffs can be unavailable.
- `check(from, to)` is the on-demand equivalent of `git diff --check` for
  introduced whitespace errors and unresolved conflict markers. Findings are
  advice; decide whether the project's conventions warrant a change. Only a
  complete `clean` result means the requested comparison had no findings.

Use `HEAD` → `WORKTREE` for edits since the last checkpoint, or `BASE` →
`WORKTREE` for cumulative changes. `WORKTREE` includes tracked staged and
unstaged edits and eligible non-ignored untracked files; the final editable
contents are the visible endpoint. After a replay, a live `HEAD` comparison
uses the replay of the checkpoint on the admitted base. A historical
comparison across different bases can be unavailable rather than attributing
new product changes to the worker.

If a response has more results, pass its `next_cursor` with the same arguments
and request only the pages you need. `WORKTREE` pages are live, best-effort
observations; re-query if edits between pages make the result unclear. Limits
apply across all four tools for the execution, including reconnects.

Submodules are parent-repository entries. Checkout status may be unknown,
and a superproject checkpoint never saves uncommitted submodule edits.
`.gitmodules` is a separate text file. These observations do not authorize
checkpoint or integration operations.

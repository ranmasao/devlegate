---
"type": "devlegate.ticket"
"title": "Extract typed Git inspection and read-only primitives from runtime"
"depends_on": ["TASK-070"]
---

## Goal

Introduce a coherent Git-specific *read/inspection* layer without inventing a generic VersionControlSystemInterface. Git is the supported first-class backend. `runtime.py` should make typed observations, not construct raw Git read commands or parse their process output.

## Context

After TASK-070, `runtime.py` still contains about 164 direct `_git`/`_git_runtime` call sites, including 52 `rev-parse`, 7 `rev-list`, 9 `status`, 8 `symbolic-ref`, and 3 `cat-file`. Some calls are distinct, but repeated command assembly and result parsing remain. `git.run_git()` delegates to `subprocess.run`; at `check=False` no error normalization occurs. `resolve_commit()` currently maps *all* nonzero statuses to None, potentially conflating missing refs with Git observation failures. The `commit_parents()` contract also needs verification for a valid root commit.

## Work

1. Inventory every remaining raw Git *read* call in `runtime.py` with command shape, response parsing, failure meaning and owner context; explicitly identify duplicated patterns versus nonduplicated domain checks.
2. Implement/reuse a compact set of real semantic Git primitives in `git.py` or one cohesive Git-specific read module, including only what actual callers need: ref/commit/object resolution; parent and range inspection; read blob/type/path; status and branch/worktree identity; ahead/behind comparisons.
3. Make missing/not-ancestor distinct from unreadable repository, malformed results, execution errors and invalid refs where callers need the distinction to fail closed. Do not accidentally convert transient Git errors into permission to initialize, repair or overwrite. Do not replace a direct call with a one-line proxy just to reduce command counts.
4. Replace direct read-command construction/parsing in `runtime.py` with semantic helpers. Keep domain-specific comparison, evidence policy, mutation authorization and retries in their existing owners. Preserve owner-bound `_git_runtime` shutdown/interruption behavior when routing through helpers.
5. Preserve tests' relevant seams with minimal changes. Add focused tests for missing ref vs command failure, ancestry error, malformed output and root commit parents where meaningful. No broad E2E suite, no new external dependencies, no generic VCS interface.

## Transparency of Git executions

- A helper must disclose every Git subprocess it launches and whether it can perform filesystem/network effects. Read-only inspection helpers must not implicitly fetch, update refs, repair worktrees, or spawn background Git commands.
- Avoid hidden multi-command implementations for a formerly single-command observation unless required for correctness. Compare the number and kind of executed Git commands in representative read paths before and after; investigate and explicitly justify any increase. Use a lightweight spy on the existing Git execution seam or focused tests, not a new broad test harness.
- Do not claim success from a reduced number of raw call sites if the new helpers add unexpected Git subprocesses. Record the static-to-semantic mapping and note any additional command executed internally.

## Acceptance

Report categorized before/after raw read-call counts and examples of actual semantic duplication removed. The remaining *read-only* direct Git calls in `runtime.py` must be zero, except specifically justified exceptional owner-bound infrastructure not reasonably factored without worsening correctness. Preserve all admission, evidence, WAL and CAS invariants. No unaccounted extra Git subprocesses, implicit remote operations, or hidden mutations. Exact-checkpoint full pytest and Ruff green.

The next stage, TASK-072, removes raw mutating Git command construction from `runtime.py`.

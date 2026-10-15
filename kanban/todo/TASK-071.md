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

## Acceptance

Report categorized before/after raw read-call counts and examples of actual semantic duplication removed. The remaining *read-only* direct Git calls in `runtime.py` must be zero, except specifically justified exceptional owner-bound infrastructure not reasonably factored without worsening correctness. Preserve all admission, evidence, WAL and CAS invariants. Exact-checkpoint full pytest and Ruff green.

The next stage, TASK-072, removes raw mutating Git command construction from `runtime.py`.

## Curator review — 2026-10-15: REWORK (execution 2de154d82c314bab9e269aaf90db1f1a)

Review of the exact published checkpoint `f754e053b0e7718d1e3ff2d6329125f728128cf5` against product base `507da8f926f92cd3be25ab97b5c382b74bdb78b5`. Preserve this checkpoint and the execution report; continue the **same ticket** and feature-branch lineage. The worker explicitly reported `incomplete`.

### Blocking findings

1. **Constructor-wide regression; fix first.** [CI run #38757880217](https://github.com/ranmasao/devlegate/actions/runs/38757880217) failed with 468 failed tests, 32 errors, 718 passed and 1 skipped; Ruff was skipped after pytest failed. Root cause shown by CI traceback: `ServiceEngine.__init__ → _validate → _branch_runtime → git.branch_name(runner=self._git_runtime) → _assert_repository_owner`. The `_host_owner_thread_id` attribute is initialized **after** `_validate`, and the new helper now enters the owner-bound runner before that initialization. Correct the constructor/transport lifecycle and test initialization from real CLI and hosted service. Do not merely move that one attribute: `_git_runtime` error handling also expects initialized `_state` and shutdown state. Avoid disabling owner checks after startup.
2. **Incomplete extraction.** A static inventory of direct calls with explicit Git subcommand arguments in `runtime.py` found approximately 103 read-only call sites before the change and **53 still remaining** at `f754e053`, with 161 → 111 total direct calls. Examples include `rev-parse`, `rev-list`, `ls-remote`, `log`, `show`, `diff-tree`, and the recovery/evidence paths. Complete the ticket's direct-read extraction target; document any genuinely unavoidable exception instead of reclassifying read calls as mutation infrastructure. Report before/after inventory and semantic deduplication.
3. **Incorrect relative common-dir resolution.** `git.common_dir(cwd)` resolves `git rev-parse --git-common-dir` via `Path(value).resolve()`, which is relative to the Python process directory, **not** the provided repository/worktree `cwd`. Make the relative path resolution repository-relative and add a real-Git regression with process CWD distinct from the observed worktree.
4. **Git failure must not mean absence.** `git.read_object()` and `git.read_path()` currently return `None` for any exit 128; that includes failures other than demonstrably missing objects/paths. Distinguish exact absent cases from corrupt/unreadable repository, malformed inputs/output and execution errors, preserving fail-closed behavior at evidence/recovery boundaries. Add focused negative/error tests. Review `resolve_commit()` absence classification against the same invariant.
5. **Prove exact checkpoint.** After fixes, run focused tests, the full exact-checkpoint pytest suite and Ruff. CI green is mandatory before acceptance. Report counts, semantics, preserved owner/shutdown transport and WAL/CAS regression evidence. The current checkpoint's tests have not passed, and Ruff has not executed.

### Review disposition

Return `review → todo` for focused continuation. Do not accept/integrate `f754e053`. Do not clear the execution evidence, reset the implementation branch, or move work into TASK-072. The existing post-worker QA initiative is deferred and is **not** a new condition of this ticket.

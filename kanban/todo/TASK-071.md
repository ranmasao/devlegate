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

## Curator review — 2026-10-15: SECOND REWORK (execution c2a654c2740740da93f70bd42d1a15da)

Exact reviewed checkpoint: `0688293f13283e3b4816338d4e7c7dec14ebea13`; predecessor `f754e053b0e7718d1e3ff2d6329125f728128cf5`. Preserve **both** immutable execution reports and the continuing feature-branch lineage. Worker again reported `incomplete` with unfinished extraction and tests. The new checkpoint fixes the mass constructor failure and relative common-dir handling, adds stricter missing-object/path handling and focused regressions. **Do not integrate.**

### Authoritative CI failure

[GitHub Actions run #38786925587](https://github.com/ranmasao/devlegate/actions/runs/38786925587), exactly at `0688293f`: **1208 passed, 13 failed, 1 skipped**, 0 setup errors; Ruff skipped because pytest failed. All 13 failures must be accounted for, not merely the two the worker observed locally.

Affected tests (all failed on this exact SHA):

- `tests/test_recovery_boundaries.py::test_workspace_inspection_allows_only_unattached_missing_branch_rebuild`: new Git observation path raises `AttributeError: 'Result' object has no attribute 'stderr'`; preserve the existing injected runner/test seam while keeping real errors fail-closed.
- `tests/test_cli_ipc.py::test_real_service_drop_retire_old_lineage_and_runs_fresh[T-1]` and `[T-2]`: `drop requires a valid pending execution report` during hosted replay.
- `tests/test_daemon_lifecycle.py::test_restart_after_drop_disposition_finishes_drop_without_lifecycle`: 4 failure variants (`after-evidence`, `after-disposition`, `after-remote`, `after-worktree`). First two leave stale `execution_id`; latter two report `published execution checkpoint does not match the execution branch`.
- `tests/test_daemon_lifecycle.py::test_restart_recovers_dropped_submodule_worktree_without_lifecycle`: published execution checkpoint/branch replay failure.
- `tests/test_daemon_cross_layer.py::test_drop_final_durable_boundaries_replay_without_active_state`: 2 variants (`after-clear`, `before-receipt`), refusing already-completed drop replay.
- `tests/test_daemon_cross_layer.py::test_drop_retirement_workspace_error_does_not_kill_owner_loop`: `NoneType` subscript error in operator result flow.
- `tests/test_control_scheduler.py::test_control_init_does_not_treat_remote_observation_failure_as_absence`: unexpected diagnostic contract for failed remote observation; preserve meaning and existing public error contract, not just string-match changes.
- `tests/test_control_workspace.py::test_product_status_observation_failure_stops_lifecycle_before_checkpoint`: `GitObservationError` escapes instead of the expected safe reconciliation classification. Do not let a failed Git observation authorize any unsafe mutation.

### Remaining work and reviewer-specific concerns

1. **Fix the failing functional/recovery tests first**. Treat drop and restart replay as durability-critical, investigate shared root causes rather than weakening the tests or silently changing established state semantics. Test each intermediate crash boundary and the live owner/IPC path.
2. **Finish direct read extraction**. Using the same narrow static call-site pattern as last review, direct explicit read-command calls in `runtime.py` fell from ~53 to ~47 (first checkpoint `f754e053` to current `0688293f`); the worker's broader inventory reports **78** remaining. These are different counting methods, but **both show the acceptance target is unmet**. Reconcile actual categories and owner-bound exceptions with a complete before/after inventory. Explicit remaining Git operations include `rev-parse`, `rev-list`, `show`, `log`, `ls-remote`, `diff-tree`, evidence and recovery checks. No forwarding-only methods or hidden subprocesses.
3. **Check constructor failure path beyond the happy case**. Moving `_host_owner_thread_id` before `_validate()` removed the mass failure, but `_git_runtime()` still accesses `self._state` on unsuccessful Git commands; `_state` is not initialized until **after** `_validate()`. Ensure startup Git failure yields a meaningful typed/user-facing error rather than another `AttributeError`, while keeping owner protection after initialization.
4. **Keep absent vs error fail-closed**. `git._is_missing_revision_error()` currently recognizes the unqualified substring `"missing"`; ensure an unrelated infrastructure error containing that word cannot become `None`/missing ref. Extend focused real/subprocess-compatible negative tests; preserve genuinely absent behavior for repos/refs/paths.
5. Preserve existing shutdown, owner, WAL, CAS/lease, publication, submodule and report-provenance invariants; compare any new executed Git commands and effects explicitly. Run focused regressions and **green full pytest and Ruff at the exact final checkpoint**. Supply a categorized read-call inventory with justified exceptions. Do not move unfinished work to TASK-072.

### Decision

**REWORK: `review → todo` for the same TASK-071**. The branch/checkpoint is not accepted or integrated. QA remains deferred and is not a new condition. Correct regressions and complete existing acceptance criteria before resubmission.

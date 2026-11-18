<!-- devlegate-review-v1
ticket: TASK-154
execution: 70bcb949dbf54c3cbfe8fd2adfa65f1e
checkpoint: acac83e6f9511f1e1611a0ba951861c16fea9a35
control: f6f6bfa4cb5e70586b3f6ac4a6927f74cc2ede67
disposition: rework
-->

# TASK-154 — REWORK: integrate the Change Explorer into the actual worker

## Evidence and primary blocking outcome

Reviewed the canonical TASK-154, immutable execution `70bcb949dbf54c3cbfe8fd2adfa65f1e` (valid `incomplete`, successful worker process/egress/transport, no questions), the exact checkpoint's incremental Git patch and cumulative source, tests and hosted CI. This fourth execution changes only `src/devlegate/change_explorer.py` and `tests/test_change_explorer.py`. The retained immutable explorer now limits `changes` and `diff` response pages by **8 KiB of fully serialized JSON** including metadata and continuation cursor; a real disposable Git fixture asserts that long paths are paged without omission. This is correct incremental work, not the whole ticket.

**Critical unresolved acceptance requirement:** after four executions, there remains **no execution-authorized worker-facing Change Explorer**. `ImmutableExplorer` is explicitly a library rather than an authorization boundary or adapter, `WORKTREE` remains deliberately unavailable, and the required owner-proven checkpoint history, worker-execution binding, OpenCode tool materialization and durable per-execution metering are missing.

The next execution must prioritize a **coherent end-to-end delivery** rather than another standalone page/namespace test. Connect actual Devlegate-owned ticket checkpoint history, active execution identity and authorized managed workspace to a scoped owner-side Python backend, with real materialized OpenCode worker tools `revisions()`, `changes(from,to,cursor?)`, and `diff(from,to,path,cursor?)`. Test the end-to-end path with a disposable actual worker/service or equivalent production IPC and permissions. No worker-controlled Git refs, SHA, repository/worktree paths or execution IDs may serve as authority. Retain this execution's page size change and prior foundation.

## Required dependent completion

1. `WORKTREE` needs verified stable staged/unstaged/untracked observations; mutable cursor invalidation or explicit stale error, symlink containment and shallow dirty/uninitialized submodule status. No nested submodule traversal; `.gitmodules` is independent text.
2. Complete output **and compute** bounds. The current `_FILE_LIMIT = 32 KiB` in `diff()` rejects every larger file before checking whether its actual delta is small, contrary to the specification's large-file/small-diff requirement; provide a bounded approach for cheap cases and honest `TOO_LARGE`/`TOO_EXPENSIVE` for costly ones. Bound intermediate diff work separately from page size and give accurate unavailable/unknown stats. Enforce the 64 KiB aggregate response budget **per execution across retries, adapter reconnections and restart**, not per explorer object; limit all three tool operations.
3. Preserve semantically correct `HEAD[-N]` actual task checkpoints (not raw Git parents or zero-change execution count), retained failed/rebased/squashed lineage, cross-base safety, and correct `TARGET`/base behavior. Verify worktree/checkout ownership before file observations.
4. Expand tests to cover actual authorized worker adapter and owner channel, refusal for foreign/stale worker, token/secret isolation, invalid/multi-file/traversal paths, expensive/long-line/binary cases, cursor tampering and WORKTREE mutation, file and gitlink type changes, parent-level submodule status and Git read-only guarantees. A unit test of the immutable component alone does not prove this tool's contract.
5. [Exact checkpoint hosted CI #44371207710](https://github.com/ranmasao/devlegate/actions/runs/44371207710) completed `success`: full test/coverage and Ruff steps passed on `acac83e6f9511f1e1611a0ba951861c16fea9a35`. Do not rerun old CI to repair a nonexistent red state; repeat on the **next changed implementation checkpoint**, with focused integration tests and `git diff --check`. The report's `remaining` properly flags missing complete validation.

## Reviewer disposition and boundaries

**REWORK** the unchanged TASK-154. Keep the correct incremental changes and tests. Publish this execution-specific immutable review and atomically move `review` to `todo` without changing ticket bytes. Avoid scope drift into TASK-184's durable research blackboard, a general VCS implementation, arbitrary shell/Git capability, migrations or unrelated cleanups. The execution's working context limits are not a substitute for the end-to-end artifact; no acceptance until the actual tool exists.

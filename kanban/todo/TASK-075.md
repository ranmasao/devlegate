---
"type": "devlegate.ticket"
"title": "Contain expected execution workspace preparation failures without terminating service"
"depends_on": ["TASK-071"]
---

## Incident and scope

On 2026-10-15, Devlegate 0.5.7.dev0 at product revision `507da8f926f9` was recovering a pre-worker `TASK-071` execution. The control branch had safely fast-forwarded from `a935c8e` to `491e902` (only `kanban/backlog/TASK-074.md` added). `ServiceEngine._prepare_execution_workspace()` raised `ExecutionWorkspaceError("control revision changed after execution was planned; refusing stale execution")`. The calling site in `_run_iteration_body()` caught only `ExecutionWorkspaceConflict`, so the error escaped the polling loop and crashed the systemd service (exit status 1).

This ticket fixes **exception containment and accurate service state**, not the revision-adoption policy; routine product/control advancement belongs to TASK-076.

## Required work

1. Audit `_prepare_execution_workspace()` and its immediate scheduler/hosted entrypoints for expected `ExecutionWorkspaceError` subclasses, including branch ancestry/ownership, persisted binding mismatch, changed control revision, unsafe worktree and failed preparation. Map expected pre-worker preparation refusals into the existing `WorkspacePreparationBlockedError` / `WorkflowBlockedError` handling so hosted polling remains alive, reports a specific blocked reason and stays operator-responsive. Do not apply a blanket `except Exception` or conceal programming defects.
2. Preserve the exact admitted execution identity, WAL/state and checkpoint/recovery evidence on refusal. No worker launch, silent generation restart, branch reset, remote rewrite, forced retry, state deletion, or implicit authorization.
3. Verify the same condition is represented consistently in `devlegate status`, service logs and one-shot / interactive operator commands. Avoid an unbounded hot retry loop and duplicate log spam while the blocking state remains unchanged. Permit normal resumption after an authorized correction without restarting the service.
4. Audit adjacent pre-worker error translation boundaries for equivalent crash paths, but keep the code change narrowly scoped. Distinguish true infrastructure/owner failures from ordinary recoverable admission refusal; preserve shutdown cancellation semantics and intentional fatal invariants.

## Worker test scope — focused verification, full CI afterward

Use `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`. Prefer the exact preparation/refusal tests and daemon recovery boundary:

```sh
./dev test -q tests/test_daemon_recovery.py -k "workspace_preparation or workspace or recoverable"
./dev test -q tests/test_control_workspace.py -k "prepar or worktree or branch or binding"
./dev test -q tests/test_daemon_lifecycle.py -k "shutdown or stop_before or lifecycle"
./dev lint
```

Add focused hosted-service/IPC tests for the actual pre-worker `ExecutionWorkspaceError` containment and recovery scenario; only run specifically selected process-topology cases if the assertion genuinely requires an independent service process. Preserve status, plan, authority and fail-closed checks. The listed selectors are a starting point and do not waive the task-specific new regressions.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, the whole `test_cli_topology.py` suite or unrelated packaging tests. GitHub Actions provides full exact-checkpoint pytest, coverage and Ruff; reviewer verifies that result. Do not interpret a worker local full-suite timeout as an implementation failure when targeted test evidence is complete.

## Acceptance

- Regression reproduces the 2026-10-15 control-revision mismatch during workspace preparation, and a second non-conflict `ExecutionWorkspaceError`: hosted polling stays alive, publishes `blocked` with an actionable reason, and keeps status/IPC responsive.
- The same failure cannot mark an execution as worker-running or launch a duplicate worker. The exact persisted binding and Git refs remain unchanged on refusal.
- After making the binding admissible through an authorized operation, a subsequent iteration resumes correctly without process restart; blocked diagnostics clear.
- Existing conflict handling, `once`, retry/operator error reporting, cancellation and concurrent-owner safety remain intact. Focused tests plus full exact-checkpoint pytest and Ruff green.
- Do **not** add broad automatic recovery or weaken control/product admission checks here; TASK-076 defines normal revision advancement.

<!-- devlegate: curator-note -->

## Curator review — 2026-10-18: REWORK REQUIRED (two narrow lifecycle proofs)

Reviewed execution: `d17be7a68696469b888132b5abc5cf04`; checkpoint `241c8b46a3fd55695e91356a9bd52996b2f4889d`; admission `127c9582f02e7ff6788d9b9d9b6855fd4fe8e9d0`.
Exact-checkpoint GitHub Actions [run #39140051330](https://github.com/ranmasao/devlegate/actions/runs/39140051330) passed tests with coverage and Ruff. The focused code change (`ExecutionWorkspaceConflict` -> `ExecutionWorkspaceError` caught at `_prepare_execution_workspace` callsite) is appropriate and should be preserved.

**Do not yet accept.** Two explicit ticket acceptance criteria have not been proven through the complete hosted polling/status boundary:

1. **Blocked state survives a full owner iteration and status/IPC refresh.** Reproduce with a genuine persisted `agent_pending` execution, a valid/reusable execution workspace, and a deliberately stale `execution_control_head` relative to the current control HEAD (the original 2026-10-15 failure). Let the real `ServiceEngine.serve()` owner loop catch `WorkspacePreparationBlockedError`, finish its iteration, and execute its normal `status_view()` / `_publish_status_snapshot()` refresh. Assert that both `service_snapshot()` and operator-visible `status`/IPC retain `blocked` and the actionable **control revision changed** reason, `worker_running=False`, while the service remains alive. Recheck after another unchanged poll and verify the same refusal logs only once. In current code `_run_polling` publishes `blocked` at ~7707 then refreshes `status_view` at ~7798, while `_make_execution_plan` may return `run-worker` for a reusable workspace without checking the stale control binding: investigate whether the refresh erases the actual blocker. Prove or disprove with a deterministic test before changing runtime.
2. **Resume after an authorized correction, without restart or duplicate worker.** Exercise a genuinely recoverable/transient preparation refusal (e.g. controlled submodule preparation failure), keep the same hosted owner and durable `agent_pending` execution, correct the cause by a permitted change, then wake/allow the next scheduler iteration. Assert the blocker clears, the *same* execution ID and base/control binding are reused, exactly one worker launches, and the service remains responsive throughout. Do **not** manufacture successful recovery by silently editing persisted execution identity, resetting branches, bypassing authorization, or force-retrying. Keep normal `once`, operator error, fatal-invariant, shutdown and ownership behavior intact.

Use deterministic synchronization (events/barriers or a bounded owner-loop harness), not sleeps or repeated timing-dependent CI reruns. Prefer `tests/test_daemon_recovery.py`, with a narrowly selected `tests/test_cli_ipc.py` status case if needed; only add a full process-topology test if a real independent process is essential. First search for existing proof and reuse it if exact; add only the missing assertions/tests. Fix any confirmed status-publication bug narrowly in the established owner/status seam, without broad exception catching, schema changes, new state, extra polling, or changes to TASK-076's normal revision-adoption policy.

**Next execution:** report PASS/FAIL for each of the two cases, concrete tests and runtime changes, before/after behavior, persisted-identity invariants, and scoped pytest/Ruff results. After pushing the exact checkpoint, curator verifies green full GitHub Actions CI. Remain in review until both lifecycle proofs are convincing. Original admitted specification above is retained byte-for-byte; this is an appended curator review note only.

<!-- devlegate: curator-note -->

## Curator follow-up — 2026-10-18: CI FAILURE; REWORK REQUIRED

Second execution `5c2a110c078442a5a9d344124cc583f2` produced product checkpoint `97b4819ee80b159c5485fe9f493bccb5ae6cc50b`, admitted to review by control commit `9894feff809663c3386d011e30cbf9295f5a7803`. Exact-SHA [GitHub Actions run #39147576161](https://github.com/ranmasao/devlegate/actions/runs/39147576161) **FAILED**: 1280 passed, 1 skipped, 1 failed. Failing pre-existing regression:

`tests/test_control_recovery.py::test_live_service_keeps_unsafe_recovery_authority_reachable`

After successful owner-authorized `recover T-1 current --observed-head <head>`, the test observes removal of `operator_recovery` and preservation of execution ID, but `service.cli("plan", "--json")` reports `"action": "blocked"` where the recovered workspace must be `"run-worker"` (assertion at test line ~2894).

**Root-cause hypothesis grounded in the diff:** `src/devlegate/runtime.py::_publish_status_snapshot` now copies any previous `service_snapshot.blocked_reason` into an otherwise `run-worker` plan whenever lifecycle was `blocked` and phase is `agent_pending` (new code ~2684-2699). This cache does **not** prove that the old reason is still applicable or even originated from workspace preparation. In the failing test, a successful operator recovery updates the workspace and durable recovery state, but the old generic blocked presentation can remain sticky after the owner refreshes status.

### Required focused follow-up

1. Reproduce the *exact* previously green `test_live_service_keeps_unsafe_recovery_authority_reachable` on the failing checkpoint and inspect the stale `plan.reason` after `recover`. Determine definitively whether the new sticky-status fallback is responsible; do not assume any unrelated failure is flaky.
2. Correct the status/blocker lifetime: retain a genuine, still-unresolved pre-worker preparation refusal across status/IPC refreshes, **but clear it after successful owner-authorized recovery or a subsequent successful preparation attempt**. An independently valid read-only workspace plan must not be overwritten with an obsolete unrelated `blocked` reason. Prefer provenance-aware/owner-scoped diagnostic handling over copying arbitrary service state; do not suppress or rewrite legitimate safety blockers to force `run-worker`. Preserve the correct fail-closed behavior of stale control revision binding.
3. Add an explicit deterministic regression asserting `blocked -> recover accepted and completed -> run-worker` in the *same live owner service*, with unchanged execution identity, durable binding, worker-safety and IPC responsiveness. Retain and pass both regressions introduced in the second execution: stale control binding stays visibly blocked, and a transient preparation refusal can resume with exactly one worker.
4. Verify the existing test with a focused invocation (e.g. `./dev test -q tests/test_control_recovery.py -k live_service_keeps_unsafe_recovery_authority_reachable`), plus the relevant `tests/test_daemon_recovery.py` tests and Ruff. Use existing Python tooling if this workspace lacks `.venv`, and document the environment accurately. Do not replace the real-service test with a mock, disable it, broaden retries/timeouts, change the worker protocol, or implement TASK-076's revision adoption policy as a workaround. GitHub Actions must pass on the exact new checkpoint before acceptance.

**Disposition:** keep TASK-075 in review until a narrower fix proves both sides of the blocker lifetime and achieves green exact-SHA CI. The complete second-admission ticket specification remains unchanged above; this new curator note is an appended annotation, not a rewrite of worker-admitted requirements.

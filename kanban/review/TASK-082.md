---
"type": "devlegate.ticket"
"title": "Eliminate nondeterministic fast-worker identity races in service tests"
---

## Milestone

Devlegate 0.5.7 — focused CI reliability bugfix, independent of the QA workflow pipeline.

## Incident and goal

The exact TASK-063 checkpoint `6eee5894b83578c0fd9967c0be1d981feaa05bba`
had a red full GitHub Actions run (1274 passed, 4 failed, 1 skipped)
followed by a green rerun without code changes. The four failures were:

- `test_real_service_process_executes_retry_from_real_cli`;
- `test_real_service_auto_resume_survives_review_barrier_and_reschedules`;
- `test_real_service_transaction_stage_restart_preserves_execution[post-publication]`;
- `test_real_service_transaction_stage_restart_preserves_execution[lifecycle_after_effect]`.

The failed runs reported `worker identity persistence failed: [Errno 3] No such
process`, after which the expected ticket publication into `review` did not
happen and `LiveService.wait_for()` timed out. Green rerun is not proof that
this failure mode has been eliminated.

## Root-cause hypothesis to verify

`tests/_cli_support.py::_worker_script()` emits a report and exits almost
immediately. `src/devlegate/execution_containment.py` implements test-only
`DurableDeterministicContainmentProvider`: it publishes a worker PID before
the test monitor records completion in its separate `.exit` file.
`_MonitoredProcess.poll()` can consequently report an uncompleted execution
while `/proc/<pid>` is already gone; concurrent
`worker_supervisor._capture_worker_identity()` may receive ESRCH.

This is a concrete, code-based race hypothesis. Demonstrate the precise
ordering with a deterministic regression before treating all such errors as
harmless test flakiness. Also inspect the test worker's prompt-stdin behavior
for early close/BrokenPipe races. Do not assume an unrelated production
process-identity failure is safe to ignore.

## Required work

1. Reproduce the mismatch between test monitor completion publication and
   kernel process identity without relying on a busy CI runner or repeated
   random test attempts. Specify whether the failure originates in the
   deterministic test containment, test worker startup contract, production
   supervisor handling, or more than one layer.
2. Make the successful short-worker service-test path deterministic by
   synchronizing worker completion with the minimum identity/durable
   registration needed by that test. Prefer an explicit test-only
   acknowledgement/barrier or an equally rigorous lifecycle protocol;
   increasing sleeps, polling deadlines, CI retries, or pytest timeouts is
   not a fix.
3. Preserve real service processes, real worker process supervision, genuine
   Git transitions and restart/crash behavior in the affected topology tests.
   Do not replace their essential assertions with mocks or bypass the
   independent-service boundary.
4. If the supervisor needs a production fix, distinguish a normally completed
   short worker from a still-live execution with unverifiable identity.
   Preserve fail-closed ownership, cgroup/descendant retirement, worker
   outcome integrity and restart semantics. Never ignore ESRCH generally,
   invent worker identity, or publish a checkpoint without authoritative proof.
5. Keep `LiveService.wait_for()` bounded and diagnostic-rich. Improve error
   wording if needed to distinguish *identity capture* from *identity
   persistence*, but do not hide runtime transport failures or reclassify a
   real failed worker as success.
6. Keep the patch narrow: no QA workflow changes, adapter framework,
   broader containment redesign, or production polling policy changes.

## Acceptance criteria

- The previously failing four service-test scenarios run reliably with their
  original IPC/worker/restart/review assertions intact.
- Deterministic regression proves the fast-exit / delayed-monitor-publication
  boundary and the safe disposition implemented for it.
- A genuine live-worker identity persistence failure remains an error and
  cannot authorize speculative worker completion or duplicate execution.
- Worker prompt/stdio shutdown paths cannot nondeterministically invalidate
  a fully successful test worker handoff.
- No fixed sleep was added as a synchronization substitute. No global CI
  retry or larger timeout masks the cause.
- Full authoritative GitHub Actions pytest/coverage and Ruff succeed on the
  exact new checkpoint after focused local tests pass.

## Focused verification and execution budget

Use current `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`.
Begin with named selections of the four impacted tests and relevant
`tests/test_worker_protocol.py` and containment tests. Add a narrowly
scoped deterministic process-ordering regression; run it repeatedly in
isolation and with the impacted tests to check for flakiness, reporting the
commands and outcomes. `./dev lint` must pass.

Do not routinely run unfiltered local pytest, coverage or the complete
topology suite. Let full CI validate the exact published checkpoint. Do not
modify the already accepted TASK-063 implementation or its execution report.

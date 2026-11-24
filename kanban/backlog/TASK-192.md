---
"type": "devlegate.ticket"
"title": "Retire remaining execution descendants after normal worker exit"
---

## Milestone

Devlegate 0.5.8 — dogfooding reliability. Backlog until explicitly scheduled by the curator. Independent of TASK-056 (inactivity watchdog); deliver before closing the current iteration.

## Goal

When the managed worker leader exits normally, finish the existing execution with bounded output drain and exact retirement of its remaining descendants. Do not leave the service blocked indefinitely by an abandoned child process or require the operator to manually kill an already identified execution cgroup.

## Context

Two actual self-hosted incidents exposed the same missing behavior. TASK-056 execution 9416890c623b4cb7ae7ef61ebf2f7dca and TASK-184 execution 5261365128034293afd4c323117051a5 left detached integration-test monitor/worker processes in their execution cgroups after the worker finished. TASK-184 logged execution finished at 09:17:34 UTC on 2026-11-24, but status remained recovery-required / agent_running / worker-running with matching-live containment. The operator identified the exact execution cgroup, observed populated 1 and the two surviving test processes, then wrote to cgroup.kill. The scheduler automatically resumed retained work without reset; manual retry was refused as busy.

In src/devlegate/worker_supervisor.py the normal leader-exit path joins stdout/stderr readers without a timeout (unlike interrupted execution), and only calls containment.force_terminate when an interruption_kind exists. Descendants may keep inherited pipes open or remain populated after closing them. runtime.py correctly refuses checkpoint/publication until worker retirement is proved, but the normal exit path lacks bounded descendant retirement. The execution-finished log alone is not a reliable indication of retirement.

## Required behavior

1. After observing the actual managed worker leader exit, use a bounded drain/grace interval for its stdout/stderr and remaining descendants. Reuse the existing termination timeout/containment primitives where appropriate; no new user configuration or arbitrary delay for an already empty execution. Bound all post-exit waits including prompt delivery and inherited pipe ownership; avoid a blocking stream close or thread join that defeats the deadline.
2. If the exact owned execution containment is still populated after the grace interval, terminate only that containment through its existing force-terminate capability, regardless of whether the leader exited with an interruption kind. Verify the kernel's empty boundary (or the existing deterministic provider's equivalent proof in tests), and finish bounded output draining. Never signal the service cgroup, infer ownership from process names, reuse stale ownership or launch another worker before retirement. Preserve fail-closed behavior on missing, inconsistent or unreadable evidence or failed retirement.
3. If retirement and output/egress integrity are proved, carry the actual original worker return code and claim through the existing post-worker checkpoint/publication path. Successful cleanup of residual descendants by itself is not an operator interruption, new attempt, reason to invalidate an otherwise valid completed claim or request for reset. If output/claim integrity cannot be established, use the existing truthful failure/recovery semantics, never manufacture success. Preserve ordinary nonzero exit, operator abort, stop, reset and retry behavior.
4. Log an actionable bounded reason if retirement is blocked or output cannot be drained; distinguish leader exit from confirmed whole-execution retirement and publication. Do not mislead operators with a bare execution-finished message as the only observation of a blocked retirement. Keep ordinary successful logs concise; no new diagnostic framework.
5. Keep the fix local to worker supervision and existing retirement interfaces. No watchdog, new recovery scheduler, process pool, generalized resource registry, new persistence protocol, migration or rewrite of execution history. Test-side cleanup remains independently required, not replaced by this fix.

## Acceptance criteria

- An ordinary leader exit with no surviving children retains current prompt, report, return-code and checkpoint behavior.
- With surviving detached descendants, including a child retaining inherited stdout/stderr, bounded grace and exact containment termination converge to confirmed retirement; the valid original claim is published once without a needless successor when its egress is intact.
- Failed or unproven retirement remains recovery-required, blocks publication and overlapping execution, and explains the blocker to the operator. Unrelated processes are unaffected.
- Existing interruption, shutdown, reset and recovery contracts remain intact. Exact checkpoint passes focused tests, Ruff and hosted CI.

## Required regressions

1. Real subprocess worker emits a valid claim, exits, and leaves a detached child with closed inherited output streams. Verify the exact execution containment is retired automatically, no survivor remains, and the ordinary completed report/checkpoint is published once.
2. Repeat with a descendant holding inherited output pipes open. Verify bounded completion/drain and retirement rather than indefinite join. Include a nonzero leader exit or incomplete egress case to prove no fabricated completion.
3. Inject failed termination, still-populated or indeterminate containment observations. Verify no checkpoint/overlap, actionable diagnostic, preserved recovery authority, and no termination outside the proven execution boundary.
4. Exercise normal no-descendant exit and existing interruption/stop/reset paths; verify an unrelated sentinel process survives. Use existing disposable service/containment test infrastructure, with independently bounded test cleanup on assertion or startup failures.
5. Run focused worker supervision, containment and lifecycle/recovery regressions, lint, diff check and hosted exact-checkpoint full CI.

## Boundaries

This is post-leader-exit retirement, not inactivity detection while a worker is running. TASK-056 owns watchdog behavior; TASK-184 owns blackboard and its own test teardown. Historical reset-operation attribution and log-follow continuity are separate problems. No runtime migration or manual cleanup of the self-hosted service in tests.

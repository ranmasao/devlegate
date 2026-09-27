---
"type": "devlegate.ticket"
"title": "Add an agent-only long-running test tool"
"depends_on": ["TASK-026", "TASK-029"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

Give implementation workers a dedicated project-aware test tool that can run
focused validation profiles without relying on the generic shell tool's short
foreground timeout.

The tool must be limited to test execution; it is not a second unrestricted shell.

## Context

Even focused Devlegate tests can take longer than a generic agent shell call is
comfortable waiting for. Repeated shell timeout/retry cycles waste wall-clock time
and agent steps, and can cause the worker to rerun tests whose process state is
unclear.

The worker still needs interactive test control while implementing:

```text
edit -> focused test -> inspect failure -> edit -> focused test
```

Therefore moving all tests out of the worker is not sufficient. QA and full CI are
separate concerns:

- QA owns deterministic handoff validators such as lint;
- this tool exists for agent-controlled focused tests;
- full CI remains the authoritative repository-wide proof.

## Required behavior

- Materialize/expose a reserved worker tool, tentatively `devlegate_test`, through
  the supported worker adapter.
- The tool can invoke only project-defined **agent test profiles** from the exact
  validation-policy generation; it cannot execute arbitrary shell commands.
- Every test run is bound to the current ticket, execution ID, exact execution
  workspace, and selected profile.
- The test process runs in the exact execution workspace and tests the worker's
  current code, not the canonical product checkout.
- Support test durations substantially longer than the generic shell foreground
  timeout.
- Provide controlled run observation sufficient for an agent to know whether a test
  is running, passed, failed, timed out, or was cancelled, without guessing from a
  vanished shell call.
- A profile may define a project-appropriate timeout/resource ceiling; machine-local
  overrides may tighten operational limits without changing shared test meaning.
- If selector support is provided, selectors are structured/constrained inputs
  interpreted by the selected profile, not arbitrary command fragments.
- Capture bounded result output for the agent and retain a navigable execution log
  for longer diagnostics.
- Test subprocesses have explicit process-group ownership and cleanup. A worker
  cannot leave orphan test processes after execution termination.
- Cancellation/worker drain/service shutdown cleanly retires owned test processes.
- The tool does not grant Git, workflow, service-host, filesystem-outside-workspace,
  or arbitrary process-execution authority.
- Devlegate runtime remains stdlib-only; project test dependencies are supplied by
  the project's configured toolchain/environment.
- Do not make agent test results authoritative QA or CI evidence automatically.
  They are implementation feedback unless a separate later policy explicitly
  promotes them.

## Acceptance criteria

- A worker can invoke a named focused test profile that runs longer than the generic
  shell foreground timeout and receive its eventual result without restarting it.
- The agent can distinguish running/pass/fail/timeout/cancel states.
- The tool refuses unknown or QA-only validator profiles.
- The test command is derived from project policy and cannot be replaced with an
  arbitrary shell command by tool arguments.
- Test execution is confined to the worker workspace and the selected execution
  identity.
- Worker termination leaves no owned test process group alive.
- Test logs are associated with the current execution and are inspectable without
  polluting canonical workflow evidence.
- Existing `devlegate_report` semantics remain independent.
- Full tests and lint for the implementation remain green.

## Required regressions

- Start a long focused profile whose duration exceeds the generic shell timeout;
  observe one continuing run and its final result, not duplicate executions.
- Unknown profile -> refuse before process launch.
- QA-only lint profile -> refuse through the agent test tool.
- Structured selector containing shell metacharacters cannot execute a second
  command.
- Worker abort/drain while a test is active retires the test process group.
- Test process exits nonzero -> agent receives failed state and bounded diagnostics.
- Tool/backend interruption does not report PASS by absence of a running process.
- Two worker executions cannot observe or control each other's test runs.

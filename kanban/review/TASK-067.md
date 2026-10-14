---
"type": "devlegate.ticket"
"title": "Finish Git primitive deduplication beyond the shared command runner"
"depends_on": ["TASK-061"]
---

## Current review — REQUEST CHANGES

Latest execution `4c565dafa61544af8d7c8f39e18f82fb`, checkpoint `8f3b420bc388f90965ee37ede7679cfb9d5e97f3`, worker claim `completed`.

**Completed and worth preserving:** `src/devlegate/git.py` now provides the canonical `run_git(cwd, *args, check=True, input_text=None)` implementation; `runtime.py`, `execution_workspace.py` and `runtime_locator.py` use it, retaining the existing `_git` test seams. A focused subprocess argument/result unit test was added. This removes duplicated command-execution mechanics with no new dependency.

**Outstanding:** The changes only consolidate `subprocess.run` execution. TASK-067 also calls for **inventory and deduplication of actual repeated low-level Git primitives and safe removal of owner passthroughs**. Neither the diff nor worker summary demonstrates that those were assessed. Do not mark the ticket complete merely because `run_git` is shared.

[Exact-checkpoint CI #38638390848](https://github.com/ranmasao/devlegate/actions/runs/38638390848) was still running at review time; inspect its final result before asserting full validation.

## Remaining work — narrow and evidence-led

1. Briefly inventory duplicated **Git-only** operations among `runtime.py`, `execution_workspace.py`, `runtime_locator.py`, `recovery_workspace.py`, `recovery_transactions.py`, `recovery_workflow.py`, `reconciliation_transactions.py` and any existing Git helpers. Compare concrete call sites and *semantics*, rather than assuming commands with the same spelling are interchangeable.
2. Check repeated ref resolution/parents/history/ancestry/status/CAS and worktree mechanics. Extract **only primitives that are genuinely reused and share return/error semantics** into `git.py` or an existing suitable module. A semantic helper should do something useful (e.g., normalize/prove a result or require expected-old SHA), not be a one-line renaming of a single command used once.
3. Review `ServiceEngine.recovery_git`, `ServiceEngine.reconciliation_git`, any other `_git` forwarders, and their Protocol declarations. Remove a wrapper **only if** it adds no locking, state authority, Git mutation authorization, error handling or test seam that matters. Otherwise document why it stays. No requirement to eliminate wrappers at the expense of correctness.
4. Include a concise inventory result in the final worker report: which repetition was removed, which was intentionally retained (different preconditions / no actual duplication). If the audit honestly finds no further reusable semantic primitives, **say so explicitly with examples**; do not manufacture an API or expand scope.
5. Reuse existing integration/regression tests. Add only minimal focused unit tests for newly extracted helpers if current tests do not cover them, or a specific newly found safety gap. Full pytest and Ruff must pass at the next exact checkpoint.

## Non-negotiable boundaries

Preserve current `run_git` work. No GitPython/pygit2, extra distribution, generic Git object model, Protocol expansion, speculative command registry, duplicate new Git runner, or broad tests simply for moved code.

Keep domain authorization, owner lock, WAL state, provenance/evidence, expected-old lease/CAS and crash-safe publish order in existing recovery/reconciliation/workflow code. Never replace a guarded side effect with an unguarded infrastructure call. Maintain `check=False`, missing-ref, exit-code, mocking and exception behavior.

Do not touch `_run_iteration_body()` refactoring, TASK-063, TASK-066, CLI, packaging or unrelated code.

## Acceptance

Shared command execution **plus documented examination of repeated primitives and eliminated safe duplication** (or precise evidence why nothing else is appropriately shared), with the full test suite and Ruff green. No appended review history.

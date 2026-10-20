---
"type": "devlegate.ticket"
"title": "Perform an independent architectural audit of the runtime God Module"
---

## Context
The initial extraction of Git functionality from the historically ~10k-line `src/devlegate/runtime.py` was mechanically useful but did not fully transfer semantic responsibilities. Repeated cross-cutting lifecycle bugs suggest hidden coupling between scheduler, workspace, Git transactions, durable state, recovery, CLI/IPC and prompt assembly. A high-risk refactor must not be entrusted as one unbounded implementation task.

## Goal
Conduct an **independent architecture and responsibility audit** of the current runtime and its extracted modules; establish evidence before deciding what, if anything, to refactor.

## Required work
1. Measure current module size/complexity, imports, call graph and state ownership; trace major `ServiceEngine` phases and side effects. Do not assume historical line count still matches HEAD.
2. Construct a responsibility map for scheduling/admission, execution identity, worker lifecycle, Git workflow/ref management, workspace management, persistence, reconciliation/recovery, logging/status/diagnostics, host/daemon/IPC and distribution boundary.
3. Locate leaks across boundaries: raw subprocess results, mutations from observational functions, ambiguous owners of durable fields, duplicated invariants, implicit transaction stages, dependency cycles and long methods coupling Git and SQLite.
4. Review test placement and ability to prove real Git effects, durable replay and process restart, including why TASK-083 required repeated rework. Identify architectural risk without attributing all failures to a particular model.
5. Recommend stable module contracts, seam boundaries, observability requirements and prioritized incremental refactor slices with measurable acceptance criteria, proof obligations and rollback strategy. Include an explicit **do-nothing** option for sections already sound.
6. Audit **all existing Ruff suppression directives and configuration-level exclusions** throughout production code, tests, tools and generated/fixture files: file-level `# ruff: noqa`, line-level `# noqa`, `# noqa: CODE`, `# ruff: noqa: CODE`, `per-file-ignores`, `extend-per-file-ignores`, and related ignored-rule settings. Record exact locations, affected rules, original rationale if discoverable, and whether suppression is justified today. In particular investigate the widespread test-file `# ruff: noqa: F403, F405, I001` directives and wildcard imports: determine whether they hide avoidable dependency/circular-import issues after test-module splitting. Distinguish deliberate exceptions (e.g. fixtures or required API compatibility) from shortcuts that silence warnings instead of fixing root causes. Recommend narrow replacements (explicit imports, localized `noqa`, appropriate test support modules), with estimated risk and targeted test/lint proofs. Report findings and separate focused remediation tickets; **do not bulk-remove suppressions or refactor all imports as part of this audit**.

## Boundaries and acceptance
- Audit and decomposition proposal only, not broad mechanical extraction or immediate rewriting of `runtime.py`.
- Account for concurrent TASK-083/TASK-076 changes: audit exact revision and identify what may become stale.
- Deliver a report with concrete file/function references, dependency/ownership diagram, risk-ranked findings and separately schedulable small-ticket recommendations.
- Backlog only; no priority over completing current stabilization/deployment barrier.

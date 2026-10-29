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

## Additional baseline and evidence obligations — static architecture analysis (2026-10-23)

The prior static AST scan of `release/0.5.7` counted **44** package modules (26,691 physical lines, 1,046,178 source bytes), **88** direct internal import edges, **273** `ServiceEngine` methods, and **480** recognized direct `self.method()` call edges. `runtime.py` was **11,351 lines / 481,365 bytes (46.0% of package bytes)**, `cli.py` 3,138 lines. The largest `ServiceEngine` routine, `_run_iteration_body`, measured **1,292 lines** (approximately lines 3855–5146 in the measured snapshot) and **49 distinct direct self-call targets**. Other candidate concentrations: `_validate_state_invariant` (448 LOC, no direct `self.*` AST access), `cleanup_workspaces` (343), `_make_status_snapshot` (233), `check_result` (232), `_run_polling` (229). Module import fan-out was 22 for `runtime` and 19 for `cli`. These numbers are **historical measurements, not acceptance thresholds**: identify the exact product commit and recompute or disclose drift before using them as a baseline.

These measurements support investigation of concentrated orchestration, **not** an automatic God Object diagnosis, proof of actual side effects, a target number of classes/files, or evidence that extraction improves worker productivity. The direct-self-call graph had no multi-node strongly connected component; this says nothing about indirect calls, callbacks or semantic cycles. Likewise the reported **71 direct readers / 2 attribute writers of `self._state`** misses `self._state[key]` writes, nested dictionary/list mutations, aliases, indirect writes and `_save_state` effects.

Refine the audit deliverables:
1. Inventory **field-level durable state ownership and writes**, including indirect/nested updates, aliases, transaction/WAL boundaries and thread ownership. Explicitly map `_snapshot_lock`, `_operator_command_lock`, `_lifecycle_lock`, `_receipt_lock`, `_owned_execution_id`, publication freshness and lock ordering.
2. Produce a **transition map of `_run_iteration_body`**: admitted phase, read-only observations, proof/validation, authority, mutation, durable save/publication, crash re-entry and rejected-unsafe continuation. Do not extract arbitrary line ranges or transfer mutation authority to helpers.
3. Evaluate **`_validate_state_invariant` as a small pilot seam**, verifying true purity, imports, supported state schemas and exact diagnostic behavior; zero direct self-access does not prove purity. Suggest a separate implementation ticket only if the seam is objectively sound. Evaluate status/plan snapshot builders for consistency and no stale Git/state observations; cleanup proof versus ref-retirement operations require destructive-path regressions before extraction.
4. Trace concrete **cross-boundary comprehension problems**: where maintaining one operation requires reading distant `ServiceEngine` branches or undocumented external module behavior. For a small sample of comparable historical tickets measure unique source+test context tokens, re-reads/compaction, cross-file changes and rework, alongside behavioral/safety preservation. Treat these as proxies requiring normalization for ticket difficulty, not automatic refactor success metrics.
5. Compare candidate designs against **do nothing** and thin-wrapper/facade alternatives, explicitly checking for hidden shared mutable state, circular dependencies, protocol inflation and regressions to single-owner mutation authority. Preserve any legitimately centralized coordination. No global stylistic Git import rewrite; monkeypatch attachment points may change.

This extension is **self-contained**; the original AST/graph report need not be present for execution. It does not supersede the ticket's mandatory Ruff/noqa and test wildcard-import inventory, and it does not authorize implementation or a new tick–tock scheduling policy.

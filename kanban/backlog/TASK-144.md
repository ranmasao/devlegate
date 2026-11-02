---
"type": "devlegate.ticket"
"title": "Admit fresh successor with repaired failed lineage and original review"
"depends_on": ["TASK-143"]
---

## Milestone

TASK-139 historical repair decomposition: 5/5 — actual resume of productive worker generation.

## Goal

Consume exactly one fully repaired historical intent and admit a new worker execution with (a) all cumulative code from the rebased transport-failed checkpoint and (b) the originally published curator review from the earlier genuinely reviewed execution. Complete the positive historical-repair vertical slice.

## Context

An ordinary `_latest_reviewed_rework_report()` refusal is correct until an exact durable historical repair has succeeded. Existing `_prepare_execution_workspace()` rework from the *older reviewed* checkpoint can discard the later failed attempt's edits; the successor must start from TASK-143's **repaired failed** checkpoint, not from the reviewed predecessor. Neither old execution report may be rewritten.

## Required behavior

1. Before ordinary failed-after-reviewed rejection in owner admission, recognize **only** a durable exact `historical_repair.status=repaired` identity for the selected runnable ticket, with original review provenance, current product and control ticket blob and exact repaired ref/workspace unchanged. Reject any drift or incomplete stage.
2. Bind one new execution ID durably to the repaired failed-attempt predecessor and to the original immutable review artifact, with owner-serialized idempotent handoff/consumption. Never label the old failed execution reviewed or overwrite its checkpoint; never launch duplicate workers after crash.
3. Prepare/validate the existing repaired workspace without resetting it to the older reviewed checkpoint. Check branch, common-dir, registered path/HEAD/cleanliness, base ancestry, submodules, expected local/remote refs, worker absence and Git locks. Existing prompt/rework provenance must deliver the genuine original curator feedback to the fresh worker.
4. On success, resume ordinary Devlegate worker lifecycle with normal checkpoint/report/review handling for the **new** identity. Record consumed repair evidence for audit; a second ordinary admission may not reuse the same authorization.
5. Document complete operator sequence, meaning of historical ownership acknowledgment, unsafe-state refusal, restart and conflict recovery. No automatic repair from backlog-to-todo, no manual touching of real TASK-134 data, no special-case code.

## Acceptance criteria

- Disposable real-Git full positive end-to-end: genuine review+feedback -> transport failure with nonempty cumulative delta -> defer/backlog/requeue -> advanced product -> exact inspection/authorization -> pin/rebase/restart -> new worker. Assert all failed checkpoint edits survive, prompt includes original curator review verbatim, new execution ID differs from both old IDs, and old execution reports/review/ticket bytes and evidence refs remain intact.
- Idempotent restart cannot create two successor workers or consume one repair intent twice. Wrong generation/control/ref must fail closed.
- No change to normal active reconciliation, ordinary rework, scheduler selection, or accepted integration semantics.

## Required regressions

Complete positive CLI/IPC owner flow and worker prompt, stale handoff, changed ref/product/ticket, missing review, dirty/foreign/locked/recreated worktree, interrupted before/after state handoff and worker launch, concurrent request, double restart. Run Ruff, targeted and full configured CI.

## Boundaries

TASK-139 remains a separate deferred final negative-test/review gate after this DAG is integrated. No TASK-134 production mutation, history fabrication, generic force retry, reset or migration.

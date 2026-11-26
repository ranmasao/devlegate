---
"type": "devlegate.ticket"
"title": "Remove routine reconciliation stage commands from the operator interface"
"depends_on": ["TASK-201"]
---

## Milestone

Deterministic reconciliation consolidation — 5/5, operator surface reduction.

## Goal

Remove CLI/IPC operations whose only purpose was manually initiating or sequencing now-automatic deterministic reconciliation. Retain a small, honest way to inspect blockers and make genuinely necessary semantic or authority decisions.

## Context

Use integrated TASK-198–201. Current reconcile auto/update-base/resume command domain exposes internal progress choices, and existing control rewrite acknowledgement is a separate authority operation. TASK-167 proposes broader interactive repair UX; do not implement it here. The old cr_recon roadmap allows removal of transitional commands once semantics are automatic.

## Required behavior

- Inventory actual command/IPC handlers, request fingerprints/receipts, tests, help, scripts and docs for reconcile auto/update-base/resume and relevant recovery instructions. Remove obsolete routine entry points and dead dispatch/admission paths in a deliberate one-shot change after proving their ordinary cases are automated. Do not keep compatibility aliases, deprecated receipt schema migration or unused alternate execution paths merely for history.
- Preserve distinct exceptional authorization for actual product/control lineage changes that automatic policy cannot authorize, and the single explicit conflict-resolution confirmation already introduced. Do not remove an operation's only safe supported exit; retain or consolidate that exceptional decision with exact current-state binding and truthful refusal. Operator must not select internal stage, type a SHA for routine recovery, or know ResourceBinding details. Use existing status/inspect to describe automatic progress and exact blocked reason; no broad repair planner or new generic mutator. Document which commands remain and why.
- Retain intentional retry, reset, drop, force/recovery and control reconciliation boundaries where they express different intent/authority; do not silently substitute destructive action. No change to agent roles or normal worker protocol. Do not rewrite historical artifacts.
- Update docs and CLI help, including error hints that currently recommend removed commands. Report before/after command and user-action inventory for ordinary clean transfer, interruption, conflict and exceptional authority cases. Required routine action count is zero; conflict needs content resolution plus at most one explicit confirmation after editing. A command or status response must not claim success before effects are established.

## Acceptance criteria

No routine reconciliation stage command is needed or retained without a demonstrated independent authority use case. Existing normal lifecycle auto recovery passes end-to-end; conflict and exceptional operator decisions still have supported exact safe exits. No stale docs or misleading help; no compatibility aliases/migration.

## Required regressions

1. CLI help/parser/IPC inventory proves removed operations absent and supported remaining operations route to the single owner; no dangling command hints or fallback direct mutations.
2. End-to-end normal drift/REWORK and restart/replay require no command; demonstrate bounded conflict resolution with only content edits and one confirmation; stale candidate confirmation refuses.
3. Preserve explicit exceptional authority, control rewrite acknowledgement, independent retry/reset/drop/recover semantics and mutation refusal/authentication. Verify no removed command is necessary to recover any supported ordinary state. Focused tests, lint, diff check and exact-checkpoint hosted CI.

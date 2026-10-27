---
"type": "devlegate.ticket"
"title": "Design unified read-only inspection of project, ticket and execution objects"
"depends_on": ["TASK-051"]
---

## Milestone
Future introspection capability, after Devlegate 0.5.7 (not a 0.5.7 release blocker).

## Goal
Plan a coherent, Docker-inspect-like read-only CLI interface to inspect authoritative Devlegate objects and their relationships. Syntax is provisional, not a committed public command contract.

## Scope
- Inspect project resolved effective properties and configuration provenance without duplicating TASK-051's existing project-specific implementation contract.
- Inspect ticket: current workflow state, known execution history, reviews, checkpoints and outcome relationships.
- Inspect execution: available metadata, provenance, report, checkpoint, result, log and prompt artifact location, optionally render the stored prompt from existing evidence.
- Consistent concise human-readable and structured JSON modes; explicit distinction of current state, historical evidence, absent records, inaccessible evidence and incomplete execution.
- Read-only, no mutation/implicit network fetch, no parallel evidence store, no secret leakage by default; permission/scope for showing prompt text requires deliberate design.
- Inventory existing status/log/prompt/review/report data sources and propose shared resolution boundaries rather than creating yet another file walker.

## Deliverable
Design and task split, with example inspect queries, data ownership map and regression strategy. No implementation within 0.5.7; do not expand TASK-051's admitted scope.

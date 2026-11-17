---
"type": "devlegate.ticket"
"title": "Audit module cycles and runtime coupling before further decomposition"
"depends_on": ["TASK-165", "TASK-168"]
---

## Scheduling

Research only, after current Git effect inventory (TASK-165) and test proof inventory (TASK-168) are completed. Do not directly refactor `runtime.py` in this audit.

## Motivation

`runtime.py` remains large and multiple domain modules call back into private ServiceEngine APIs. A clean Python import graph can still hide dependency cycles through injected owner objects, `owner._state`, private methods, and effects. A mechanical LOC reduction or generic `common.py` extraction is not an architectural success criterion.

## Goal

Build a reproducible evidence-backed map of actual static imports, runtime/domain dependencies, mutable state ownership, and transactional boundaries. Identify a small number of safe, bounded decompositions and the test proof needed for each.

## Required investigation

1. Inventory the exact integrated product SHA, module imports including local/delayed imports and type-only edges, strongly connected components and actual import cycles. Do not label every back-reference a Python import cycle; distinguish import cycles from runtime callback/data coupling.
2. Map `ServiceEngine` calls into extracted modules and their reverse callbacks into `owner._state`, private owner methods, Git/SQLite/WAL, worker supervisor, resource binding and effect domains. Separate read-only dependency edges from authority-changing writes and annotate failure/recovery paths.
3. Investigate real responsibility and coupling in `runtime.py` and adjacent modules; identify independent domain value types/error types or narrowly typed interfaces that could replace broad private dependencies without weakening single-owner semantics. No generic common module or arbitrary repository abstraction.
4. Cross-reference TASK-165's Git effects and TASK-168's actual test proof boundaries. For each candidate, estimate removed/added dependency edges, new interface surface, safety risks and focused/hosted regression scope. An observed architectural improvement must be measurable beyond line movement.
5. Publish an audit-only report with dependency graph and SCC inventory, evidence paths, prioritized bounded implementation tickets and explicit PASS/FAIL/UNKNOWN conclusions. Honest negative findings are a completed audit if substantiated.

## Boundaries

No runtime refactor, no architecture migration, no service deployment changes or blanket test rewrites in this ticket. Do not modify real managed owner resources. Preserve Git/WAL/CAS and sole-mutation-owner guarantees.

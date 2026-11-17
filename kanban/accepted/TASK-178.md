---
"type": "devlegate.ticket"
"title": "Render inspect ticket for operators without nested JSON"
"depends_on": ["TASK-177"]
---

## Goal

Replace the unreadable generic `inspect ticket` table of serialized nested JSON with a purpose-built human-readable ticket inspection.

## Required behavior

1. Clearly separate canonical identity/title/type/state, dependency summary and blockers, control provenance, transition history and ticket text. Keep long multiline ticket body separate from fixed-width table cells; no literal JSON dumps or escaped Unicode in normal text.
2. Distinguish current committed ticket data from historical provenance and unavailable evidence. Avoid unbounded horizontal overflow for ordinary nested fields; retain full exact identifiers, paths, Unicode and meaningful messages (wrap where safe, do not silently truncate identity).
3. Preserve `--json/--yaml` semantics and canonical exact-object resolution, security/read-only constraints, no repository mutation or new status collection.
4. Verify samples at TTY 80/120, non-TTY and scenarios with long text, dependencies, missing/ambiguous evidence and blockers. Independent reviewer inspects rendered output and its hierarchy.

## Boundaries

Presentation only. No ticket schema migration or changes to dependency graph/provenance computation. Follow focused tests, Ruff and hosted exact-checkpoint CI.

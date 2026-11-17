---
"type": "devlegate.ticket"
"title": "Render inspect execution with clear current and historical evidence"
"depends_on": ["TASK-178"]
---

## Goal

Provide a readable operator view for the exact execution, instead of printing report, checkpoint, review, generation and historical versions as JSON in a single table cell.

## Required behavior

1. Separate execution identity/ticket, worker conclusion and report summary, checkpoint/provenance, review decision, latest-vs-historical execution, independently observed generation/current local identity, blockers, and historical artifact versions. Do not present a historical checkpoint as current proof.
2. Preserve the explicit `proved`, `unproven`, `unknown`, absent/deleted distinctions. Make summary readily scannable while exact full details remain available in `--json/--yaml`. Do not hide safety-relevant blockers or silently truncate IDs/hashes.
3. Render multiline worker/reviewer text in readable blocks, not escaped JSON; use existing shared presentation primitives only where they improve clarity.
4. Exercise real representative fixture shapes: completed/reviewed, missing/replaced proof, stale/historical execution, deleted review, large history, 80/120 TTY and non-TTY. Reviewer examines real outputs independently.

## Boundaries

No changes to execution history/provenance proof logic, read-only IPC, service owner, review acceptance or Git authority. Focused tests, Ruff and hosted CI.

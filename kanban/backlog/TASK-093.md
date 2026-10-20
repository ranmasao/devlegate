---
"type": "devlegate.ticket"
"title": "Expose execution workspace cleanup outcomes in service journal"
"depends_on": ["TASK-092"]
---

## Incident and motivation

On 2026-10-20, rslab2 completed accepted integration of LAB-185, but its subsequent cleanup remained difficult to observe: the operator had to inspect `status --json`, worktree registrations and Git refs to understand whether resources were retired or blocked. Add operator-visible service logging for cleanup without weakening existing safety guarantees.

This requirement was briefly added to TASK-092 while its fourth execution was already running, triggering a correct lifecycle refusal: `control descendant changed the active ticket; lifecycle replay refused`. TASK-092's original bytes have been restored. **Do not modify an admitted/in-flight ticket to implement this follow-up.**

## Requirements

1. Both immediate post-integration workspace retirement and periodic/bounded historical cleanup report meaningful activity to Devlegate's normal service log (systemd journal under hosted service).
2. Log actual cleanup-pass start with bounds/scope and finish with counts of examined, retired, blocked/skipped and remaining resources, distinguishing per-pass from inventory totals.
3. Log successful per-ticket/execution retirement and confirmed effects on worktree, local ref and remote ref. Do not report deletion before confirmation.
4. Log concrete refusal reasons and resource status for blocked or partially retired candidates; include altered Git refs, ownership conflicts and ambiguous provenance where applicable. Preserve error details if a pass is interrupted.
5. Avoid repetitive journal spam for unchanged historical blockers on every scheduler poll; use changed-state events, bounded summaries or rate limiting. Avoid unnecessarily exposing full local paths, secrets, report text or unrelated execution data.
6. Ensure journal information agrees with `workspace_cleanup` status projection and accurately represents successful, partial and failed cases.
7. Add isolated regression tests asserting appropriate events for success, refusal and partial failure, including immediate `accepted -> done` cleanup and historical cleanup.
8. Limit scope to logging and necessary diagnostics; do not broaden into generic QA pipeline or weaken cleanup retirement ownership/provenance gates.

## Scheduling boundary

Start only after TASK-092 has been accepted and integrated, so cleanup semantics are stable. Use an ordinary new execution, not mutation of TASK-092 during its lifecycle.

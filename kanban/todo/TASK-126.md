---
"type": "devlegate.ticket"
"title": "Diagnose TASK-073 orphan resources and repair recurring cleanup defect or apply one-shot fix"
"depends_on": ["TASK-125"]
---

## Purpose
TASK-073 is a deferred human-only final Git-architecture acceptance gate in `kanban/backlog`, not durably done. Its historical worktree, local ref and upstream ref remain. Two incomplete execution reports exist: `9ada49c1cd904f18b90e63c0b5be7b02` and `b69e17a85bd04c25b0348a6db8fb77b5`. Earlier control-state moves changed ticket blob provenance. Do not assume resources are garbage merely because this ticket is non-done.

## First: establish root cause and recurrence
- Correlate both reports, their execution/control/checkpoint SHAs, ticket blob history and control transitions, product ancestry, evidence refs, worktree registration/HEAD/dirty state, local and remote `devlegate/work/TASK-073` SHAs, runtime owner/state, and cleanup refusal.
- Reconstruct the precise historical sequence that retained resources. Distinguish intended protection of non-done work, genuine orphaned/obsolete generation, active/recoverable execution, provenance discontinuity, and partial retirement. Show which invariant or transition produced the state, not just that the inventory keeps reporting it.
- **Determine whether a supported present-day workflow, crash/restart, rework, deferral, abandoned execution, failed integration or partial cleanup can reproduce the same unwanted retention.** Build the smallest deterministic real-Git reproducer. If it cannot recur, substantiate with current code paths and regression evidence; absence of an easy reproducer alone is not proof of impossibility.

## Conditional outcome (choose based on evidence)
**A. Reproducible systemic problem:** repair the normal production lifecycle/cleanup logic at its actual ownership boundary, with no TASK-073-specific special case. Preserve fail-closed authorization; an unfinished ticket never becomes eligible for destructive cleanup solely due to age, `backlog` state or an execution report. Use explicit evidence of superseded resource generation, no live owner, exact Git identities, lease/CAS and fresh pre-effect observations. Include crash/restart and partial-progress convergence tests. Coordinate with TASK-125 cadence and blocked inventory semantics; do not reintroduce per-idle Git polling. If a safe automatic disposition cannot be proven, implement a generic explicitly authorized retirement path rather than silently deleting.

**B. Demonstrably historical, non-recurrent anomaly:** leave production cleanup unchanged beyond TASK-125. Provide an **untracked, one-shot script/procedure** to repair only the proven obsolete TASK-073 resources. It must use current exact SHA preconditions, clean/registered worktree ownership checks, explicit operator approval before destructive execution, local ref expected-old CAS, remote lease, re-observation, safe partial replay and verified postconditions. Do not commit this repair script as a distributed feature or invent compatibility/migration machinery. If safe proof is lacking, stop and report precise blocking evidence instead of deleting anything.

In either branch, retain TASK-073 itself in backlog for a future fresh audit generation; preserve original historical execution reports, pinned evidence and curator approval requirement. Never rewrite old control/report history, falsify provenance, automatically mark TASK-073 `done` or self-APPROVE the story. Do not run destructive operations against the actual operator's local machine or remote refs automatically merely because the ticket worker discovered them.

## Deliverable
A concrete root-cause and **recurrence verdict with evidence**, selected remediation branch, exact changed production code/tests or one-shot untracked repair artifact/instructions, safety preconditions, residual gaps and regression proof. Tests must cover active owner, dirty/ambiguous workspace, wrong local/remote SHA, repeated attempt, interrupted partial retirement and recovery. No generic VCS abstraction, compatibility migration or permanent TASK-073-specific production logic.

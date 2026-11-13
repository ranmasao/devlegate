# Workspace inventory and prune

```sh
devlegate workspace ls --json
devlegate workspace prune --dry-run --json
devlegate workspace prune --apply --limit 20 --request-id prune-2026-11-13 --json
```

`workspace ls` inventories managed execution worktrees and local/upstream refs.
`workspace prune` defaults to a read-only preview. Both use table output by
default and support `--yaml` and `--json`. Decisions include the ticket, observed
resource identity, status, and a human-readable reason. `eligible` means the
ordinary done-ticket policy currently proves disposal safe; `blocked` or
`unproven` means resources are retained. `retired` in an apply result reports
completed removal. Failed applies report independently observed worktree, local
ref, and upstream ref outcomes. Remote observation errors are explicit blockers,
never absence.

These commands require the running service owner. Inventory and preview are
serialized read-only observations: they do not fetch, refresh indexes, save a
cursor or receipt, launch work, or grant a cleanup lease. A remote product tip
whose ancestry is unavailable locally leaves eligibility unproven. Apply repeats
the safety observations under owner admission and may therefore differ from a
preview. Inventory is capped at 10000 resources; `scan_complete: false` means the
result is incomplete. Prune cohorts default to 100, with `--limit` from 1 to 10000.

Prune reuses the retained `cleanup-workspaces` owner admission and cleanup engine.
An apply records a bounded immutable cohort and durable request receipt before
effects. Reusing its request ID with the same options returns the recorded result;
an interrupted admission resumes only that cohort after restart. Use a new request
ID for a fresh attempt after resolving a blocked outcome. Admission and durable
writes retain the runtime revision and authority checks. Worktree registration,
locks, dirty files, submodule integrity, independent resource generation evidence,
execution dependencies, exact local-ref compare-and-delete, and upstream leases
remain mandatory. Failed stages are reobserved on retry.

Only a durably done, integrated execution with its accepted report, published
product checkpoint, and independently bound resource generation qualifies.
Live, backlog, review, accepted-but-unintegrated, abandoned/archived, and historical
unbound resources are retained. Automatic done-ticket cleanup, including immediate
integration retirement, requires this same generation provenance. There is no
`--force` provenance bypass. Reports, reviews, binding
evidence, and other audit history are retained.

Prune never resets execution continuity or makes a backlog ticket runnable.
`inspect workspace <ticket-id>` remains the detailed read-only identity view.
Reset confirmation (`clean apply`) is an independent domain. The old public
`clean status` and bare `clean` inventory spellings have been removed.

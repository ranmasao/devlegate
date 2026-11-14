---
"type": "devlegate.ticket"
"title": "Workspace resource listing and prune CLI over proven done-ticket cleanup"
"depends_on": ["TASK-161"]
---

## Goal

Expose Docker-like resource-scoped cleanup, **not** a generic `gc` command:

```sh
devlegate workspace ls --json
devlegate workspace prune --dry-run --json
devlegate workspace prune --apply --limit 20 --request-id <id> --json
```

`workspace prune` operates only on resources whose **ordinary completed-ticket cleanup policy** already proves they are eligible. It must not reset execution continuity or make a backlog ticket runnable. `workspace ls` is a resource inventory; `inspect workspace <ticket-id>` is the detailed read-only identity view owned by TASK-161.

## Required behavior

1. Replace `devlegate clean status` with `devlegate workspace ls` and an explicit prune preview. The default invocation of `workspace prune` is read-only/dry-run; destructive pruning requires explicit `--apply`. Use established table/YAML/JSON output conventions and an understandable per-resource reason (eligible, blocked, unproven, retired).
2. Reuse existing owner-serialized `cleanup-workspaces` admission and retained cleanup machinery, including bounded cohorts, precise independent ownership, idempotent durable receipts, lock/dirty/submodule checks, exact-ref CAS/lease and failed-stage recovery. Keep daemon automatic cleanup using the same policy; do not create an alternate deletion engine.
3. Only **proven done/integrated** execution resources qualify. Never prune live, backlog, review, accepted-but-unintegrated, abandoned/archived or historical unbound resources as though they were done. Remote observation failure is not proof of absence. Do not delete immutable execution reports, reviews or other audit evidence.
4. A read-only list/dry-run must not save cursor/receipts, launch work, change any Git/SQLite state or imply a lease. An apply must be serialized, revision/authority checked and report actual effects and blocked outcomes accurately. No `--force` bypass for provenance.
5. Replace old public `clean status` spelling without permanent compatibility alias. Keep reset and prune as independent domains; leave `clean apply` replacement/removal to TASK-132.

## Tests and acceptance

Disposable real-Git integration and owner/IPC tests for eligible done, dirty/locked/unproven, non-done, remote failures, concurrency, dry-run non-mutation, apply and restart/idempotence. Document the policy and command examples; run configured CI and Ruff. No effects on live operator resources and no migrations.

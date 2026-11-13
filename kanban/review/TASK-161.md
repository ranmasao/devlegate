---
"type": "devlegate.ticket"
"title": "Universal read-only inspect domain for ticket, execution, workspace and service"
"depends_on": ["TASK-131"]
---

## Goal

Introduce one **universal, read-only** Devlegate CLI domain, modeled on Docker's object inspection rather than nested action-specific inspection:

```sh
devlegate inspect ticket TASK-132 --json
devlegate inspect execution <execution-id> --json
devlegate inspect workspace TASK-132 --json
devlegate inspect service --json
```

The object type determines the schema. These are observation operations, **not** operation planners, reset authorizations, cleanup requests, or wrappers that create a new service. Preserve existing `--json`, YAML, and table conventions and consistent explicit unknown/unproven states.

## Required behavior

1. Provide canonical, typed, versioned read-only views for ticket (state, immutable blob, dependency graph, provenance), execution (exact execution ID, immutable report/checkpoint/review, latest-independent-generation distinction), workspace (current registered path/ref/remote observations, independent ResourceBinding, dirty/locked/submodule state and blockers), and service (owner/lifecycle state and published diagnostic snapshot). Avoid ambiguous name/SHA-based fallbacks, hidden changes, or superficial JSON dumps containing sensitive internals.
2. Move TASK-160's `devlegate clean inspect <ticket-id>` **operator preview** into `devlegate inspect workspace <ticket-id>`, preserving the exact canonical identity, schema/digest contract consumed by TASK-130/131 reset authorization, or make an explicit versioned schema update with coordinated reset-contract tests. A workspace without a complete reset proof remains inspectable with `digest=null` and clear blockers. `inspect` must never imply permission to mutate.
3. Share existing service-owner serialization, read-only IPC snapshots and proven Git observation paths. No separate owner, service startup, implicit fetch, Git hook/filter execution, index refresh, SQLite writes, or worktree/ref mutation. Refuse unavailable/ambiguous data explicitly and preserve exact report/review history.
4. Replace the old `clean inspect` public CLI spelling rather than keeping permanent aliases or a compatibility layer. Do **not** rename `clean apply` or alter the destructive reset lifecycle in this ticket; TASK-132 owns final reset CLI integration. Do not implement prune here.
5. Add exact-object resolution, missing/foreign/ambiguous evidence, concurrent owner/active worker, local/remote unavailable, read-only filesystem/SQLite, JSON schema and CLI/IPC regressions. Ensure tests and docs no longer rely on `clean inspect`.

## Boundaries

No migration, no historical ResourceBinding adoption, no destructive authorization, no cleanup policy change, no modifications of actual TASK-073/TASK-129/TASK-131 resources. Make a bounded, independently reviewable read-only change and pass configured CI.

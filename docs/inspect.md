# Read-only object inspection

```sh
devlegate inspect ticket TASK-132 --json
devlegate inspect execution <exact-execution-id> --json
devlegate inspect workspace TASK-132 --json
devlegate inspect service --json
```

The object type selects a versioned schema. Normal `--json`, `--yaml`, and table
output conventions apply. All four commands use read-only IPC to the existing
service owner. An unavailable or busy owner is an explicit error; inspection
does not start a service, launch a worker, or refresh the published status view.
IPC `inspect` accepts exactly `object_type` and `object_id`; service uses a null
ID. Inspection is never mutation permission, a reset authorization, or a cleanup
request. Every successful result has `informational: true` and
`mutation_authorized: false`.

Ticket, execution, and service use `devlegate.inspect-<type>.v1`. Each envelope
has `object_type`, `object_id`, `observation`, and `blockers`. Missing or ambiguous
canonical objects are errors. Unavailable independent evidence is explicitly
`unknown` or `unproven` and has blockers; it is never inferred from matching
names, paths, or SHAs.

## Ticket

The exact ticket ID resolves to one committed workflow path and state. The view
contains the immutable blob and text, parsed metadata, dependency graph nodes
(state/path/blob/edges), and control commit and transition commits. Missing graph
dependencies and ambiguous nodes are explicit blockers. Working-tree edits do
not replace committed ticket content. This view reads committed evidence only
and does not require local workspace or remote availability.

## Execution

Only the full execution ID resolves. Ticket IDs, branch names, SHA prefixes, and
the runtime's latest execution cursor are not substitutes. Duplicate IDs across
tickets and report/path identity mismatches are refused. The view includes the
exact immutable report, checkpoint, review and provenance, plus every committed
report/checkpoint/review version (including deleted or rewritten feedback).
Publication and review proofs use the existing lifecycle attribution rules.

`latest_execution` is `latest`, `historical`, or `unknown`, based on unambiguous
publication chronology, independently of `independent_generation`. A missing
ResourceBinding does not hide the immutable checkpoint or adopt a historical
resource. `current_local` is proved only by independent read-only local identity
checks. This view does not contact the remote or claim remote continuity.

## Workspace

See [the workspace schema and reset digest contract](clean-preview.md).
`devlegate.inspect-workspace.v2` exposes registered path/ref/remote observations,
dirty/locked/submodule state, independent resource generations, and blockers.
It preserves the canonical digest algorithm and all identity bindings while
replacing private runtime authority and remote URL contents with hashes. Reset
admission and replay consume this same version. Incomplete proof remains inspectable with
`digest: null`. Neither a digest nor `snapshot_complete` grants mutation authority.
Missing or unsafe working ticket/report copies are blockers; their committed
immutable evidence remains visible.

## Service

The owner identity (including lifecycle readiness) and existing published
diagnostic status snapshot are returned. No runtime database dump, environment,
private owner state, or new status observation is added. Diagnostics describe
the owner's last publication; owner/lifecycle identity is observed at request
handling. An unpublished diagnostic snapshot is explicitly unavailable.

## Read-only boundary

All Git inspection reuses the proven no-optional-locks/no-lazy-fetch observation
runner. Workspace and independent generation checks use the existing filter
preflight before status. No hooks, conversion filters, fetch, index refresh,
SQLite write, or worktree/ref mutation is part of inspection. Requests share the
existing owner queue, including during active worker ownership; conflicting
ownership remains a workspace blocker. No historical ResourceBinding adoption,
resource migration, prune, or cleanup policy is introduced.

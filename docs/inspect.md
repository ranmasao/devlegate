# Read-only object inspection

Set `TICKET_ID` to the ticket's exact ID and `EXECUTION_ID` to the full execution
ID before running the corresponding command:

```sh
devlegate inspect ticket "$TICKET_ID" --json
devlegate inspect execution "$EXECUTION_ID" --json
devlegate inspect workspace "$TICKET_ID" --json
devlegate inspect service --json
```

The object type selects a versioned schema. Normal `--json` and `--yaml` output
conventions apply. All four commands use read-only IPC to the existing
service owner. An unavailable or busy owner is an explicit error; inspection
does not start a service, launch a worker, or refresh the published status view.
IPC `inspect` requires `object_type` and `object_id`; service uses a null ID.
Execution inspection supports the optional boolean `include_prompt`.
Inspection is never mutation permission, a reset authorization, or a cleanup
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

The human view separates the current committed ticket and its text from control
HEAD and path history. It summarizes direct dependencies and their committed
evidence, and reports graph-wide blockers; the complete graph remains available
in JSON and YAML. Path history lists commits affecting the ticket paths, not
necessarily verified state transitions. Long ticket text is shown separately,
with terminal control characters escaped.

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

`local_log` reports the canonical execution log path and whether the log is
`present`, `missing`, or `unavailable` locally. Use `devlegate logs execution
"$EXECUTION_ID"` to read it. Inspection does not read the log contents.

`worker_prompt` reports local journal availability and, when validated, the
prompt SHA-256, directive, delivery marker, and a summary of component and
provenance evidence. The default output omits prompt text and private component
source details. To request the exact retained text:

```sh
devlegate inspect execution "$EXECUTION_ID" --prompt --json
```

The opt-in is execution-only. JSON and YAML preserve the saved UTF-8 text in
`observation.worker_prompt.text`; human output escapes terminal controls. Treat
opt-in output as private. The owner checks identity, admission and applicable
review bindings, SHA-256, and any saved component manifest. The saved assignment
must match the admitted control ticket, and the project component must match the
execution's independently recorded product admission, even when that admission
has no worker documents. Older journals without a project key require independent
local generation evidence. A journal without a component manifest can show
metadata, but exact text requires a matching owner-recorded execution digest.
Missing, unsafe, unproven or oversized evidence is diagnosed without substituting
or truncating text. If the prompt and the committed view together exceed the IPC
limit, the view reports the refusal alongside the prompt metadata.

`constructed` means the journal does not record successful stdin delivery;
`sent` means the local worker adapter recorded a successful stdin write. Neither
proves provider receipt or the full context used by the model. The journal and
log are local diagnostics, separate from committed execution evidence.

## Workspace

See [the workspace observation schema](clean-preview.md). Inspection is never a
precondition for [reset](reset.md); its digest supplies no mutation authority.
`devlegate.inspect-workspace.v2` exposes registered path/ref/remote observations,
dirty/locked/submodule state, independent resource generations, and blockers.
It preserves the canonical digest algorithm and all identity bindings while
replacing private runtime authority and remote URL contents with hashes. Reset
admission and replay consume this same version. Incomplete proof remains inspectable with
`digest: null`. Neither a digest nor `snapshot_complete` grants mutation authority.
Missing or unsafe working ticket/report copies are blockers; their committed
immutable evidence remains visible.
The human view groups the ticket and control binding, workspace and Git
observations, and each independent execution generation. Blockers appear first;
private worker authority is hash-bound and its safety conclusions are reported
through blockers. Artifact trees and detailed identity remain in JSON and YAML.
Values in the human view can wrap; use machine output for exact tokens.

## Service

The owner identity (including lifecycle readiness) and existing published
diagnostic status snapshot are returned. No runtime database dump, environment,
private owner state, or new status observation is added. Diagnostics describe
the owner's last publication; owner/lifecycle identity is observed at request
handling. An unpublished diagnostic snapshot is explicitly unavailable.
The human view selects service identity, lifecycle, worker count and runtime
phase/stage; it labels published diagnostics as potentially stale rather than
repeating the project status. JSON and YAML retain the complete v1 snapshot,
with `diagnostic_snapshot: null` when no status has been published.

## Read-only boundary

All Git inspection reuses the proven no-optional-locks/no-lazy-fetch observation
runner. Workspace and independent generation checks use the existing filter
preflight before status. No hooks, conversion filters, fetch, index refresh,
SQLite write, or worktree/ref mutation is part of inspection. Requests share the
existing owner queue, including during active worker ownership; conflicting
ownership remains a workspace blocker. No historical ResourceBinding adoption,
resource migration, prune, or cleanup policy is introduced.

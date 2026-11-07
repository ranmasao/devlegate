# Abandonment request and evidence foundations

This implementation is not yet an operational abandonment workflow. It adds
the terminal board, request schema, scheduler reservation and owner-only
evidence library. The service deliberately refuses pending abandonment
requests: Git-free analysis isolation and independently reviewed finalization
are not yet available. Do not move live tickets to `abandoned` manually to
request cleanup. No abandonment-specific cleanup authorization is enabled.

## Board and request format

`ABANDONED_PATH` defaults to `kanban/abandoned`. It is included in workflow
fingerprints, status counts and new control-plane bootstrap. Existing
five-board control histories remain readable without a migration. An absent
sixth directory in an older history is treated as empty. An abandoned ticket
is never runnable and never satisfies a dependency. Dependants remain valid
and report that dependency's `abandoned` state.

An abandonment request is a separate ticket type. For example, in a disposable
project, a request could have this form (replace the example HEAD with the
full actual initial product commit):

```markdown
---
"type": "devlegate.abandonment"
"title": "Abandon the superseded experiment"
"target": "EXAMPLE-1"
"start_head": "0123456789abcdef0123456789abcdef01234567"
---
## Abandonment reason
The experiment was superseded by the replacement design.

## Report requirements
Describe the reason, each execution and review, meaningful implementation and
research, integrated versus discarded work, unfinished work, evidence gaps,
future conclusions and exact manifest/checkpoint identities.
```

`target` must name exactly one implementation ticket. `start_head` must be a
full lowercase SHA-1 or SHA-256 object identity, not a ref or abbreviated SHA.
Both body sections must occur exactly once and contain text. Ordinary tickets
cannot use the request-only metadata keys. Dependencies use the existing
strict schema. Duplicate requests reserving the same target, self-targeting,
missing targets and already terminal targets are rejected.

Requests in `todo`, `review` or `accepted` reserve their target against fresh
scheduler selection. Request selection has priority over implementation
tickets. Until the isolated execution and finalization path is implemented,
the plan is explicitly blocked, including for accepted requests. The ordinary
worker and product integration paths must not process them.

## Evidence library

`devlegate.abandonment_evidence` is an owner-only library, not a new public IPC
or worker endpoint. The caller supplies the repository-owner assertion and
exact admitted control HEAD and specifications. The extractor checks the
specifications against their immutable Git blobs. A changed request reason or
target cannot silently substitute for the admitted text.

The extractor traverses a proven linear control history in publication order,
not execution-ID order. It validates execution JSON and exact lifecycle
publication using the existing provenance checker. Each execution receives
its own base-to-checkpoint delta. Attributable rework decisions use the
existing independent review binding checker. Unavailable or changed evidence
and unproven review decisions remain explicit gaps. A missing latest
checkpoint never falls back to an older execution. An initial-to-latest state
comparison is labelled as a state comparison, not cumulative contribution,
and is emitted only if ancestry is proven.

Files, control text and diffs have immutable object identities, SHA-256 content
digests, byte sizes and stable artifact IDs. File entries identify renames,
deletions, submodules and symlink blobs without dereferencing links. Inline
material is limited to 256 KiB per artifact and 4 MiB total across control
text, code and diffs. Every omission has an index entry. Binary/non-UTF-8
excerpts are omitted conservatively; their bytes remain available for paging.

`EvidenceBundle.read(manifest_id, artifact_id, offset, length)` accepts only
the sealed allowlist and at most 256 KiB per call. Offsets and lengths must be
integers within the artifact. Responses identify source, full-content digest,
range, chunk digest and UTF-8 or explicit base64 encoding. Reads regenerate
and stream-hash the immutable source before releasing the selected range.
Changed or absent objects are refused. Reading a late range rescans the
source, trading owner-side I/O for bounded memory and avoiding persistent
copies of large objects. Clients must consume pages incrementally.

The reader does not accept filesystem paths, ref names, arbitrary Git commands
or symlink targets. It disables external diff/text conversion and lazy object
fetching. Only manifest data and read responses may eventually cross into an
isolated agent. Passing the owner-side Python objects, repository path, owner
IPC or credentials to that agent would violate the execution boundary.

## Required completion and recovery boundary

The remaining service integration must bind admission to exact request and
target blobs, prove quiescent ownership, persist the exclusive disposition,
launch an evidence-only isolated agent and validate the semantic report through
Reviewer. A cgroup is a process-lifetime boundary, not filesystem isolation.
The current explicit refusal must remain until that stronger boundary exists.

The intended authoritative report placement is
`abandonments/<target>/<request-execution>.md`. Publication must follow all
existing execution/review records and atomically move the unchanged target to
`abandoned` and the request to `done`, with idempotent replay and no product
merge. Only that reviewed canonical transition may authorize abandonment
retirement. None of these publication or retirement effects are implemented
by the evidence library.

Retirement must reuse guarded owner-side operations with separate abandonment
authorization, not weaken the existing done/integration proof. Only proven
target-owned worktrees, branches and exclusive code pins may be removed.
Immutable execution JSON, curator reviews and the final report stay in control
history. A remote ref with a matching name and SHA does not prove uninterrupted
remote identity. Unknown remote state or uncertain generation must block that
destructive step. Persisted per-resource cleanup intent and observations must
support restart after partial retirement; blocked cleanup must not reverse the
terminal board transition.

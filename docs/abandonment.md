# Abandonment request and evidence foundations

This implementation is not yet an operational abandonment workflow. It adds
the terminal board, request schema, scheduler reservation, owner-only evidence
library, durable exclusive admission, supervised evidence analysis and scoped
bounded read channels. The service records analysis proposals in runtime state;
independently reviewed control publication and finalization are not yet connected.
Do not move live tickets to `abandoned` manually to
request cleanup. No abandonment-specific cleanup authorization is enabled.

## Board and request format

`ABANDONED_PATH` defaults to `kanban/abandoned`. It is included in workflow
fingerprints, status counts and new control-plane bootstrap. All six configured
workflow directories are required; attachment and readiness checks refuse a
missing sixth directory. New projects receive `kanban/abandoned/.gitkeep` from
the existing control bootstrap. For an existing five-board project, the operator
must deliberately add and publish the empty configured directory with a
`.gitkeep` on the control branch before using this version. This is a one-time
setup action, not an automatic historical migration. Do not add a workflow
directory to the product branch. An abandoned ticket is never runnable and
never satisfies a dependency. Dependants remain valid and report that
dependency's `abandoned` state.

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
tickets. A runnable request is admitted only under the repository owner after
quiescence and exact committed specification/resource checks. The ordinary
checkout worker and product integration paths do not process these requests.
The plan remains explicitly blocked while reviewed finalization is pending,
including for requests moved to `accepted`.

Admission persists the request/target blob identities, initial and observed
product HEADs, reason digest, execution identity and local resource-generation
observation before workspace creation or worker launch. A changed reason, target
or target specification blocks replay rather than silently retargeting the
request. Removing an admitted request does not release the durable reservation.
Live or uncertain worker ownership, reconciliation, pending integration, recovery
and foreign/unproven worktrees are refused without canceling an existing worker.
Dirty owned worktrees are observed without modifying them; their uncheckpointed
data is explicitly a gap in the immutable checkpoint evidence.

The scheduler runs analysis in `STATE_DIR/analysis/analysis-*`, a private directory
outside repository ancestry. It durably records prepared/running/reported stages,
the manifest and directory identities, supervised process identity and proposed
ExecutionReport. A reported result replays without launching another worker. A
lost process with proven absent containment produces a failed report, never an
invented semantic summary. Live/unknown containment or the uncertain launch
window blocks with an ownership diagnostic. The current implementation does not
yet provide publication, independent acceptance or an operator resume command
for that blocked launch window. The report proposal alone cannot change boards
or authorize disposal.

Upstream observations are retained for later lease comparisons and labelled
`unproven` for continuity. A matching remote name and SHA is not independent
proof that a deleted ref was never recreated. Upstream-only resources with no
local ownership evidence are refused at admission.

## Evidence library

`devlegate.abandonment_evidence` is an owner-only library, not a new public IPC
or worker endpoint. The caller supplies the repository-owner assertion and
exact admitted control HEAD and specifications. The extractor checks the
specifications against their immutable Git blobs. A changed request reason or
target cannot silently substitute for the admitted text.

The extractor proves the linear control chain with one bounded observation,
then reads trees only at this target's execution/review publication events, in
chronological order rather than execution-ID order. It validates execution JSON
and exact lifecycle
publication using the existing provenance checker. Each execution receives
its own base-to-checkpoint delta. Attributable rework decisions use the
existing independent review attribution checker and verify the exact original
same-commit ticket move and review publication. A still-deferred rejection is
identified as `rework-deferred`; historical attribution does not require a new
worker admission or rescan unrelated later control commits. Exact historical
acceptances are attributable with the same bound header and `disposition:
accept`, published with the unchanged review-to-accepted move. Legacy acceptance
without attributable text remains an explicit review gap. The default ordinary
rework binding still rejects acceptance headers. Unavailable or changed evidence
and unproven review decisions remain explicit gaps. A missing latest
checkpoint never falls back to an older execution. An initial-to-latest state
comparison is labelled as a state comparison, not cumulative contribution,
and is emitted only if ancestry is proven. When the owner supplies an exact
observed product HEAD, each checkpoint is labelled as in or outside that commit's
ancestry. Acceptance alone never proves integration; ancestry does not prove
the effects survived later reverts, and absent ancestry does not disprove
equivalent changes or cherry-picks. Without that observation integration is
explicitly unknown.

Files, control text and diffs have immutable object identities, SHA-256 content
digests, byte sizes and stable artifact IDs. File entries identify renames,
deletions, submodules and symlink blobs without dereferencing links. Inline
material is limited to 256 KiB per artifact and 4 MiB total across control
text, code and diffs. Every omission has an index entry. Binary/non-UTF-8
excerpts are omitted conservatively; their bytes remain available for paging.

`EvidenceBundle.read(manifest_id, artifact_id, offset, length)` accepts only
the sealed allowlist and at most 256 KiB per call. Offsets and lengths must be
integers within the artifact. Responses identify source, full-content digest,
range, chunk digest and UTF-8 or explicit base64 encoding. The initial full
streaming scan records a SHA-256 digest for each 256 KiB source page as well as
the full-content digest. Subsequent reads verify complete pages against that
index before releasing bytes, and check that the exact underlying objects still
exist. One sequential stream and one cached page per repository make sequential
reconstruction linear in source size. A backward/random seek can restart the
stream; skipped bytes also consume the owner's budget. No binary is mirrored
into a persistent bundle. Clients must consume pages incrementally and owners
must close the bundle at the end of the analysis session.

Conservative per-repository owner-session defaults are 16 GiB of source I/O
(including initial scans, control observations, rejected reads and seeks),
65,536 source operations, and 600 seconds elapsed. Each Git producer has a
60-second CPU and 1 GiB virtual-memory ceiling, including between paging requests;
active reads also
enforce a 60-second producer deadline, including while waiting for pipe output.
Limits are shared across artifacts and may be lowered by the owner.
Exhaustion explicitly refuses further work; it does not reset budgets for each
page. The manifest states these defaults. Page hashes require only 32 bytes per
256 KiB of indexed content, plus bounded indexing overhead. These budgets bound
owner work; consumers must also bound their own retained response history.
If a diff producer cannot complete within those budgets, its exact source
identity and an explicit gap are retained alongside the attributed file index.

The reader does not accept filesystem paths, ref names, arbitrary Git commands
or symlink targets. It disables external diff/text conversion and lazy object
fetching. Only manifest data and read responses cross the evidence tool channel.
Owner-side Python objects and privileged service dispatch are never exposed
through this channel.

## Required completion and recovery boundary

Mandatory kernel-enforced agent confinement is explicitly deferred to TASK-152.
The experimental launcher and kernel-isolation tests have been removed. TASK-151
uses the existing trusted-agent model: worker instructions, OpenCode directory
permissions and supervised cgroup lifetime containment. A cgroup is a process
lifetime boundary, not filesystem or credential isolation. An agent with general
shell access as the service UID may reach other resources. This implementation
does not claim OS-enforced denial of Git, credentials or privileged IPC.

`EvidenceWorkspace.prepare` creates a fresh private evidence directory outside
repository ancestry. It contains only the manifest, not a checkout or `.git`.
The manifest is read-only, bounded to 64 MiB including its index and JSON
encoding, and pinned by content digest; its directory identity is checked before
worker admission. It does not mirror omitted source objects.

`WorkerSupervisor.run` supports this workspace with the existing execution
logging, prompt delivery, process identity, worker egress, cancellation and
cgroup retirement. The worker gets a generated `devlegate_evidence_read` tool
bound to one manifest and one private socket, not owner-side Python objects or
the privileged service dispatcher. The owner services reads in the supervised
wait loop, on the repository-owner thread. Socket I/O is nonblocking, with one
active connection and bounded request/response buffers; idle or partial clients
do not stop supervision. Owner extraction still has its independent source-I/O
and producer deadlines. A channel failure interrupts and retires the worker.

`EvidenceSocket` accepts only the same four read fields as `EvidencePipe`, one
request per connection. Its default quotas match the pipe quotas below.
Malformed requests, unauthorized IDs and excessive ranges are refused and
consume quotas. It never unlinks a preexisting endpoint on admission, and only
removes its own endpoint on close. There is no generic filesystem or Git API.

`EvidencePipe` uses the existing length-prefixed IPC framing but has no service
method dispatcher: the only request fields are `manifest_id`, `artifact_id`,
`offset` and `length`. The repository-owner thread serves each request and
retains its Git capability. Defaults are a 4 KiB maximum request, 1 MiB maximum
encoded response, 8,192 total requests, 2 GiB total encoded responses, 30-second
pipe I/O deadlines and a 600-second session deadline. Invalid reads consume
quotas. Stalled/oversized frames and exhausted budgets close the session. No
response history is accumulated.

The analysis prompt requires a concrete reason, every execution/review outcome,
meaningful changes and research, integrated versus discarded work, unfinished
work, known gaps, future conclusions and exact manifest/checkpoint identities.
`devlegate_report` remains the only semantic egress. Analysis reports are bounded
to 64 KiB UTF-8; completed claims must contain the required nonempty sections and
exact identities. Structural completeness is not semantic correctness or review
acceptance. Incomplete/blocked claims remain untrusted draft evidence too.

The remaining service integration must publish the proposal for independent
Reviewer validation, require exact acceptance, finalize the control transition
and connect retirement/recovery. The current explicit finalization refusal
remains because those owner transitions are unfinished, not because of the
deferred kernel sandbox requirement.

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

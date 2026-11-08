# Reviewed abandonment requests

This implementation connects evidence analysis, immutable proposal publication,
independent review and the atomic terminal board transition. Local retirement
uses independent generation evidence and the reviewed terminal decision. The
full feature remains unfinished: remote branch continuity and exclusive code-pin
retirement still fail closed, and uncertain launch/control-publication conflicts
do not yet have a dedicated owner recovery command. Do not use this unfinished
implementation to dispose of live operator resources. A manual board move is
not a substitute for the reviewed transition or resource ownership proof.

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
The request remains reserved while its analysis or independent review is pending.
An accepted request enters abandonment finalization, never product integration.

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
window blocks with an ownership diagnostic. There is not yet an operator resume
command for that blocked launch window. A report proposal alone cannot authorize
the terminal disposition or disposal.

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

## Trust and execution boundary

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

## Independent review and publication

After receiving a durable analysis result, the owner publishes the exact
ExecutionReport and proposed text under `executions/<request>/<execution>.json`
and `.md`, the bounded manifest under
`abandonment-proposals/<request>/<execution>.manifest.json`, and the source binding
under `abandonment-proposals/<request>/<execution>.json`. The latter binds both
admitted ticket blobs, the
source control HEAD, initial and observed product HEADs, resource generation,
manifest identity/digest, proposal digest and semantic conclusion. The same
publication moves the unchanged request from `todo` to `review`. Failed analysis
also goes to independent review; it cannot be accepted as a completed report.

The curator independently checks the exact proposal and manifest, then publishes
`reviews/<request>/<execution>.md` with this header (replace every placeholder):

```text
<!-- devlegate-abandonment-review-v1
ticket: REQUEST-ID
execution: FULL-32-HEX-EXECUTION-ID
control: ADMITTED-CONTROL-HEAD
publication: ANALYSIS-PUBLICATION-COMMIT
manifest: MANIFEST-ID
proposal: PROPOSED-REPORT-SHA256
disposition: accept
-->
```

For a failed report without proposed text, `proposal` is `none`; such a report
can only receive `rework`. The review body must explain the independent decision.
In the **same control commit**, move the byte-identical request from `review`
to `accepted` for `accept`, or to `todo` for `rework`. That commit must contain
only the review artifact and request move. An altered artifact, mismatched
publication/digest, changed source or conflicting disposition is refused.
Ordinary implementation review headers do not authorize abandonment.

Rework preserves the previous immutable proposal and review. The owner reserves
a fresh execution/manifest identity and supplies the previous review to the new
analyst. Old acceptance cannot authorize the new generation. Source amendments
still fail the original admission binding; rework does not silently retarget it.

On exact acceptance of a completed report, one owner transaction publishes
`abandonments/<target>/<request-execution>.md`, moves the unchanged target to
`abandoned`, and moves the unchanged request to `done`. The canonical text
contains the exact proposal plus reviewer and manifest provenance. All target
reports/reviews precede this commit. No analysis artifact or target code is
merged into the product branch. Dependants remain blocked by `abandoned`.

Publication builds an isolated Git tree without editing the control worktree or
index. Parent/commit identities and per-path content digests are durably
journaled before the owner pushes with an exact remote lease, then fast-forwards
its checkout. The owner journal does not duplicate the large evidence index.
A restart after commit construction, push or checkout advancement reuses the
same transaction. It neither reruns the analyst nor creates a duplicate report.
Loss of the scratch evidence workspace after proposal publication is harmless:
replay reads the immutable published manifest. A changed publication parent,
dirty control checkout or conflicting remote lineage remains blocked with its
retained transaction; dedicated owner reconciliation is still unfinished.

## Retirement and recovery

Only canonical reviewed terminal publication authorizes abandonment cleanup;
ordinary done/integration checks are unchanged. Retirement revalidates the
published proposal, exact review, terminal commit, current board blobs, target
history, process quiescence and independent resource generation before each
destructive boundary. No product ancestry is required. The existing worktree
removal and local ref CAS/occupancy/restoration primitives are reused. Dirty
files in the registered owned worktree can be discarded; foreign/reused paths,
uncertain workers, changed refs and invalid submodules are retained.

The per-target retirement journal records `pending`, `blocked` or `retired` plus
worktree, local/upstream ref and code-pin progress. It is separate from request
admission, so terminal publication releases the scheduler reservation even if
cleanup is blocked. Partial worktree/local deletion resumes after process loss.
Blocked cleanup is not repeatedly polled by scheduler ticks. Inspect it with
`devlegate clean`; `devlegate clean --apply` explicitly retries the bounded
cleanup cohort using the retained terminal authority. Terminal board state and
immutable text history survive any cleanup failure.

**Current retirement limitation:** an existing upstream ticket branch is always
retained because its continuity is explicitly unproven. A matching name and SHA
or a successful lease is not proof of uninterrupted identity. Existing target
code-pinning refs are also retained pending independent ownership/generation
proof. These conditions block cleanup before local destructive work. The owner
observes both local and remote code-pin namespaces; unknown remote observations
also block. Finishing authorized remote/pin retirement and their recovery path
is required before this feature is complete. Do not remove these guards or
manually erase resources merely to make a cleanup status green.

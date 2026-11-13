# Read-only workspace inspection

`devlegate inspect workspace <ticket-id> --json` asks the running service owner for
an informational snapshot. Table and YAML use the normal CLI output options.
The command does not reset, retire, publish, adopt, or resume anything. A missing
service is an error; inspection never starts another runtime or worker.

## Structured interface

The reusable owner-side entry point is
`devlegate.clean_preview.inspect_clean(owner, ticket_id)`. Repository access must
be on the service owner thread. IPC `inspect` accepts exactly `object_type` and
`object_id` (here `workspace` and the exact ticket ID) and
queues a non-durable observation through the existing operator admission slot.
Busy, draining, and unavailable owners can refuse admission. No SQLite receipt
is written for inspection, including refusals. Inspection bypasses the normal
post-command status refresh, which has broader Git observation capabilities.

The versioned result has these fields:

- `schema`: `devlegate.inspect-workspace.v2`.
- `informational`: always true; `mutation_authorized`: always false.
- `snapshot_complete`: whether all required local/provenance/authority and exact
  upstream observations were supported and free of conflicts. This does not
  assert upstream continuity or eligibility to reset.
- `identity`: the confirmation-bound snapshot described below.
- `digest`: lowercase SHA-256, or null on incomplete/conflicting observations.
- `blockers`: actionable refusal reasons and the remote continuity limitation.
  These are presentation text, outside the digest.

`identity` includes the exact canonical ticket ID, state, path, blob, control
commit, configured workflow paths, dependency/workflow blob graph, and ticket
transition history. `control_resource` binds the actual canonical checkout's
registration, branch, common directory, and administrative directory; a matching
HEAD in another repository is insufficient. Execution entries identify report blobs and
publications, complete report contents, review/checkpoint attribution where
available, independent generation intent, receipt/operation evidence hashes and
filesystem identities, and the TASK-150 `ResourceProof`. Current inventory
reports the configured path, registration, HEAD, dirty/locked/submodule state,
local ref value, symbolic target, administrative conflicts, and upstream
`observed`/`absent`/`unavailable` with the exact observed SHA when present.
Unknown cleanliness or submodule integrity is explicit, never equivalent to
clean. The runtime authority identity binds persisted pending ownership,
worker identity, active worker count, owned execution ID, and lifecycle intent
by canonical SHA-256, without publishing private runtime state. Only cleanup
presentation and rotating scan cursor fields are omitted. The authority object
contains `sha256` and `mutable_receipts_present`; the latter preserves the exact
absent/empty receipt distinction during reset reobservation. This explicit v2
schema change is shared by inspection and reset admission/replay. Independent
intents likewise expose `remote_urls_sha256` instead of credential-bearing URL
contents, binding the exact URL array without disclosing it. Previous-schema
digests and retained intents are refused rather than migrated or adopted.

Immutable report history must have one unchanged publication and an exact
lifecycle transition. Review attribution uses the existing review provenance
boundary. Current working evidence must match its committed bytes; staged or
untracked control evidence prevents a complete preview. Multiple executions are
listed explicitly. There is no latest-filename/SHA fallback for missing,
contradictory, or historical unbound generations. A legacy report can describe a
checkpoint without proving resource ownership. Older superseded generations may
therefore leave a preview incomplete and require an independent disposition.
Accepted/abandoned state and retained disposition authority are visible; a
review decision by itself does not prove product integration or retirement.
Accepted/done tickets require a proven accepted review. Deleted historical
artifacts and orphan reviews are blockers. For abandoned targets,
`abandoned_disposition` includes canonical terminal artifact blobs and reuses the
existing independent terminal proof against the retained abandonment journal.
A missing journal is explicitly unsupported; inspection never recreates it.

## Read-only boundary

Git observations disable optional locks, fsmonitor, untracked cache writes,
hooks, replace objects, and lazy fetching. No fetch is issued. Status is used
only after independent local generation identity checks and a filter applicability
preflight. Recursive submodules pass the same gate;
parent status does not implicitly inspect submodules first. Symlink substitution,
foreign registrations, replaced same-SHA resources, changed/missing refs, locks,
and interrupted Git administrative operations fail closed. No index, reflog,
resource receipt, worker evidence, or runtime state is changed by inspection.
The trusted local filesystem and configured remote transport have the same
external-administrator boundary as the existing Git observation APIs.

The original preflight rejected any configured `filter.*.clean` or
`filter.*.process` entry. GitHub CI exposed the false rejection: an inherited
driver configuration (such as Git LFS installed on the runner) does not imply
that any tracked path selects that driver. This prevented otherwise pristine
fresh generations, including initialized submodules, from producing a digest.

The preflight now reads effective configuration with includes enabled and uses
NUL-delimited `ls-files --cached` and `check-attr --stdin filter` observations.
Git resolves attribute macros, worktree/index fallback, info attributes, and
configured global/system attribute sources itself; these queries do not run
conversion filters. A driver is unsafe when an effective string-valued `filter`
attribute on a tracked path names a configured clean/process driver. Inspection
refuses before status, reports the driver/path/repository, and leaves the digest
null. Driver names `set`, `unset`, and `unspecified` collide with `check-attr`
state labels and are conservatively refused when the output matches: their
applicability cannot be disproved from that output. Unused configured drivers
with unambiguous names are permitted without disabling or replacing
their configuration. Each initialized nested submodule is checked independently.
Global/system configuration location environment variables are retained so the
preflight and status use the same configured interpretation. Repository-location
environment injection remains excluded by the existing observation boundary.

Failed/malformed path or attribute observations and unavailable configuration
remain unproven. Configuration is read again before and after status; observed
drift invalidates the preview. These reads are not a filesystem lease: external
administrators must not change configuration or attributes concurrently, and a
future confirmation must repeat the preflight under its own guards.

## Canonical digest and follow-on confirmation

`canonical_digest(identity)` hashes ASCII JSON encoded with sorted object keys,
compact separators, ASCII escaping, and no NaN values. Array order is meaningful.
The schema is inside `identity` as well as the result envelope. Timing, request
IDs, and display text are not added to this identity. An unchanged snapshot
reproduces its digest. Identity/authority changes either change the digest or
make inspection incomplete (null digest). Control and owner authority are
checked again before returning. This is a point-in-time observation, not a
lease against external writers.

Plain Git can observe an exact upstream ref SHA or absence but cannot distinguish
delete/recreate at the same name and SHA (remote ABA). `ResourceProof` therefore
retains `state: unproven` for upstream continuity even when `local_state: proved`
and `upstream_exact: true`. A complete preview still lists this limitation.

## Operator confirmation (authorization and intent only)

TASK-130 introduces an explicit operator request:

```sh
devlegate inspect workspace T-1 --json
devlegate clean apply T-1 --plan-digest <digest> --generation <generation> \
  --request-id <unique-operator-request-id> --json
```

Copy `digest` from a complete preview and `generation` from the unique execution
whose `proof.local_state` is `proved` (`identity.executions[].intent.generation`).
The CLI requires both values and a caller-retained request ID; there is no
implicit confirmation or worker-facing apply capability. The operator IPC
method is `clean-apply`, with exactly `ticket_id`, `plan_digest`, and `generation`.

The request enters the existing ServiceEngine owner/admission queue. Scheduler
contention and other request IDs receive a not-admitted busy response; duplicate
in-flight IDs share the admission result. Reusing an ID with different arguments
is rejected. The owner observes the complete identity again, including schema,
ticket state/blob/control HEAD, execution/report/generation, worktree identity,
exact local/upstream refs and owner authority. Mutable admission also captures
the durable runtime revision and checks it against the live owner state. A null
digest, stale identity, unavailable evidence, active worker, non-idle phase, or
pending integration/retry/recovery/reconciliation/retirement/lifecycle authority
refuses authorization. Only inactive `backlog`, `done`, or proved `abandoned`
states are eligible; `todo`, `review`, and `accepted` remain active. Backlog state
or age alone grants no authority, and an unexecuted ticket has no proved resource.

The owner atomically persists the full identity-bound `clean_reset_intent` and
its accepted mutable receipt in the existing SQLite WAL/FULL state transaction.
The write compares the observed runtime revision inside `BEGIN IMMEDIATE`;
intervening writes cannot be overwritten by this intent. In-memory acceptance
and the acknowledgement follow successful commit. Any uncertain write outcome
requires restart before another mutable request. Retry the **same request ID
and exact arguments** after restart to recover a committed acknowledgement or
attempt admission again if nothing committed. A stale uncommitted request must
be replaced by a fresh inspection and a new confirmation ID.

The response is `status: authorized-pre-effect`, `effects_performed: false`.
This implementation performs no deletion, reset, ticket movement, or publication.
The retained intent blocks scheduler work, other operator mutations, and applying
workspace cleanup. Restart validates the identity digest, generation, fingerprint,
and matching durable receipt and restores this hold; it never replays effects.
Missing or inconsistent authorization/receipt evidence fails closed. This is an
intentional pre-effect endpoint pending independently reviewed TASK-131 support.
TASK-130 changes require independent review before downstream reset work proceeds.

The persisted remote policy is
`exact-observation-only-no-continuity-claim`: confirmation binds the currently
observed remote SHA and URL evidence but does not assert remote ABA continuity
or grant remote deletion authority. TASK-131 must revalidate all identities and
introduce its own pre-effect lease/CAS guards and remote disposal policy before
any effects. No preview or stored confirmation is a perpetual resource lease.

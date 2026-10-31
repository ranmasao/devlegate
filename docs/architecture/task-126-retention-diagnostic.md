# TASK-126 bounded retention diagnostic

Product snapshot: `75d1b4ff54921d84aa3ecf69a6f5c846a9c24187`.
This diagnostic is not an authorization to retire resources or an acceptance
of TASK-073. It uses the task's injected review facts and product repository
only; the operator's control and host state were not observed.

## Established facts and limits

The review identifies two incomplete reports: execution
`9ada49c1cd904f18b90e63c0b5be7b02`, control
`96d26409ed22d72853b45095f029a5c6d5a70649`, base and head
`a30447295d6423da442aab23dbb00623090e91ad`; and execution
`b69e17a85bd04c25b0348a6db8fb77b5`, control
`7657bd2a8e8ba628c53d5ea7fafb94ffe35c170b`, base
`a30447295d6423da442aab23dbb00623090e91ad`, head
`1ec3a1b705d4036137806c7a0f55275b3e48faca`. The reports themselves and
control transitions are not included in the authorized task input. Their
incomplete status does not establish abandonment or disposal authority.

In the product object database, `git merge-base --is-ancestor` confirms that
the second checkpoint descends from the common base and is an ancestor of the
inspected product HEAD. This does not bind any currently retained resource to
either report, prove publication, or establish that its evidence is redundant.

The directive states that TASK-073 is in backlog and resources remain. If the
durable ticket is currently non-done and a candidate passes identity checks,
`cleanup_workspaces` in `src/devlegate/runtime.py:7043` blocks historical
retirement with `ticket is not durably done` before attempting the accepted
execution proof. This is intentional fail-closed behavior; it does not explain
which operation originally retained any particular resource. The inventory
retains such decisions and classifies them as partial rather than counting them
as actionable blocked candidates (`runtime.py:4886-4939`). TASK-125 thus
changes scheduling and reporting, not authorization for deletion.

Other lifecycle paths have narrower preconditions. Rework validates the prior
checkpoint and a clean registered worktree before retirement and moves the
local branch with expected-old CAS (`execution_workspace.py:228-261`). A
zero-delta stale-product retry persists historical evidence before retiring a
workspace and refs (`runtime.py:5064-5123`). Forced retry first checkpoints a
stranded workspace, pins recovery evidence, and records a resume requirement
(`runtime.py:1411-1493`). None of these paths proves that it ran for TASK-073.
They also do not prove all possible failure and restart paths converge.

## Policy decision and remaining proof

The curator's subsequent decision establishes that retention of a deferred,
non-done ticket is intentional. It is not an automatic cleanup defect. The
guard reproduces that retention for any non-done candidate; it does not prove
that any generation is obsolete. The precise historical sequence and provenance
continuity remain undetermined. The decision calls for a separate, explicitly
authorized operator reset, rather than either automatic deletion or a one-shot
repair. No repair or deletion is authorized by this diagnostic.

The operator's read-only observations on 2026-10-31 reported an idle runtime,
zero active workers, and a clean registered worktree and local and remote
execution refs at `1ec3a1b705d4036137806c7a0f55275b3e48faca`, with the ticket
in backlog. These observations are historical and do not establish the absence
of persisted recovery dependencies. They must not be used as effect-time proof.

The existing explicit workspace recovery is restricted to an unsafe pre-worker
binding and retains that execution identity; it is not a reset of an idle,
deferred generation. Ordinary historical cleanup requires durable done state.
Neither path authorizes the requested operator reset. A reset must be serialized
with the service owner, preserve the ticket and evidence, bind confirmation to
exact identities, and reject active or persisted recovery dependencies. It must
re-observe before each destructive stage and use expected-old local ref CAS and
a remote lease. A durable reset intent is needed to distinguish an authorized
partial retirement from unexplained missing resources after a restart.

## Historical evidence request (superseded)

The following request records the earlier diagnostic's limitations. The curator
has determined that collecting the full historical reports again is not a
prerequisite for implementing the operator reset. It is not a request to access
private control state from an implementation workspace.

Provide a sanitized, explicitly authorized read-only task artifact, scoped to
TASK-073, containing the following. Include observation time, repository
identity, command exit status or an explicit unavailable/ambiguous marker, and
whether each observation is current or historical. Redact secrets and unrelated
ticket content; retain full Git object IDs and the exact relevant messages.

1. Both full historical reports and the relevant committed ticket transition
   chain, including commit IDs and parent IDs, ticket paths/states and blob IDs
   before and after each move from the first report through the current durable
   control head. Include the current ticket blob ID and state, and any relevant
   rework, deferral, integration or cleanup events. Supply enough report fields
   to bind execution IDs, control heads, bases, checkpoints and outcomes.
2. Current engine phase and TASK-073 owner/recovery/retirement state, including
   whether a worker or recovery process is live, identity of its execution and
   checkpoint, and how liveness was observed. Include pending integration,
   retry and cleanup intents relevant to this ticket.
3. TASK-073 worktree registration, canonical path, branch, HEAD, cleanliness
   including untracked files and submodules, and whether the path is missing,
   duplicated, locked or associated with another branch. Provide the local
   branch and exact upstream endpoint/ref and both observed SHA values or
   confirmed absence. Do not provide credentials.
4. Relevant pinned evidence ref names and targets, publication/product ancestry
   evidence, and the exact read-only cleanup inventory/refusal and timestamp.
   Identify any resources already removed and evidence of an interrupted
   retirement, if known. Include failure/unavailable results instead of
   interpreting them as absence.

After receiving this artifact, reconstruct transitions and classify each
resource generation before building a minimal real-Git recurrence test. Any
eventual retirement must re-observe owner and exact identities at effect time,
require explicit authority, protect dirty or ambiguous worktrees, use local
expected-old CAS and remote lease, and safely replay partial completion. Test
active ownership, dirty or ambiguous topology, changed local and remote SHAs,
repeated attempts, and interrupted retirement/restart. Preserve the backlog
ticket, reports, evidence and human approval requirement.

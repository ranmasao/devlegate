---
"type": "devlegate.ticket"
"title": "Add worker inactivity watchdog with deterministic recovery context"
"depends_on": ["TASK-055", "TASK-061"]
---

## Milestone

Devlegate 0.5.7 — worker liveness and autonomous recovery hardening.

## Scheduling

Deferred from 0.5.6 to 0.5.7. Do not schedule TASK-056 on the 0.5.6
release line. TASK-055 (execution ownership boundary) is already completed;
TASK-057 (forced-restart continuation intent) remains in the 0.5.6 scope.
TASK-061 must first extract the shared workflow recovery/reconciliation
boundary during 0.5.7; the watchdog must reuse it rather than adding new
recovery paths inside the existing runtime.py monolith.


## Goal

Detect workers that remain process-alive but stop making observable progress,
classify them as stalled after a bounded inactivity interval, retire the exact
execution ownership boundary safely, preserve the current repository state and
execution evidence, and continue through Devlegate's shared owner-controlled interrupted-worker
recovery and admission path, with a small deterministic recovery-context prompt
fragment for the fresh attempt.

This ticket addresses the observed failure mode where OpenCode remained alive in
`do_epoll_wait` with an established HTTPS socket but produced no further worker
output for roughly 95 minutes after its last tool/edit activity.

## Context

## Incident motivation

Observed on LAB-169:

- worker process remained alive;
- no child/tool process was active;
- process state was sleeping in `do_epoll_wait`;
- HTTPS socket remained ESTABLISHED with empty send/receive queues;
- no meaningful worker output or OpenCode session progress occurred for roughly
  95 minutes;
- the last repository mutation had already succeeded, so restarting from a clean
  ticket prompt would risk duplicating or contradicting partial work.

This demonstrates:

    process alive != worker making progress

The watchdog must therefore observe worker activity, not only process liveness.

## Scope boundary with TASK-055

TASK-055 owns the question:

    what exact OS/process ownership boundary must be retired?

TASK-056 owns:

    when is a live worker considered stalled?
    how is that condition represented?
    how is a fresh worker attempt continued safely?

Do not solve escaped-descendant ownership here. Use the execution-retirement
primitive established or selected by TASK-055.

## Required behavior

## Activity model

Maintain a monotonic worker-activity timer for each active worker attempt.

Reset the timer whenever Devlegate observes new bytes/events from the worker's
declared stdout/stderr boundary.

At minimum:

- new stdout output resets inactivity;
- new stderr output resets inactivity;
- normal worker completion cancels the watchdog;
- service restart/recovery reconstructs or safely re-establishes watchdog state
  without spawning duplicate workers.

Do not depend on parsing OpenCode's private log/database format.

If Devlegate later has a typed worker-event protocol, typed events may also count
as activity, but stdout/stderr observation is the required baseline.

## Tool/descendant activity

Agent inactivity must not automatically kill a worker merely because the worker
itself is quiet while an explicitly owned child/tool process is still executing.

At minimum distinguish:

    worker quiet + no active owned descendant/tool
        -> inactivity timer eligible to reach stalled

    worker quiet + active owned descendant/tool
        -> do not classify as agent-idle solely from worker silence

Do not infer child activity from arbitrary host processes. Use only processes
proven to belong to the current execution ownership boundary.

A separate tool-execution timeout policy is outside this ticket unless required
for a minimal safe implementation.

## Default policy

Provide bounded defaults suitable for dogfooding, initially:

    warning threshold: 5 minutes
    recovery threshold: 10 minutes

These values must be configurable as machine-local runtime policy.

The defaults are provisional operational policy, not public compatibility
contract.

The watchdog should expose warning/idle information before recovery, but warning
state alone must not mutate execution state.

## Shared recovery boundary (architectural correction — 2026-11-23)

**This is a worker-liveness trigger for the existing recovery pipeline, not a
second execution scheduler or a second worker-admission protocol.** The original
post-reset implementation baseline contains the raw stdout/stderr byte-observation
seam, not the previously discarded watchdog/recovery implementation.

After a worker is proven stalled and its exact ownership boundary is retired,
the subsequent lifecycle must converge on Devlegate's existing stranded-worker
recovery, interrupted-execution normalization and `automatic_resume` admission,
or on a shared refactoring with equivalent ordinary process-loss semantics:

- **Watchdog-specific responsibility:** observe raw stdout/stderr activity,
  apply warning/recovery thresholds and owned-child exclusions, persist a bounded
  `worker_stalled` reason and evidence *before* destructive retirement, and prove
  the old worker/containment absent. Do not infer provider/network failure from
  silence.
- **Shared responsibility:** normalize the ended attempt as interrupted,
  preserve its attribution and repository edits, determine whether continuation
  is safe, and admit a fresh worker using the same owner authority, worktree,
  resource-generation and restart rules as ordinary interrupted execution.
- The existing `recover_stranded_execution()` /
  `_normalize_stranded_execution()` / `automatic_resume` flow is the starting
  point. Where that shared flow currently rejects `stalled_recovery`, does not
  recognize the `worker_stalled` interruption reason, or cannot safely preserve
  dirty/uncommitted work and its provenance, adapt **the shared boundary** rather
  than layering a new stall-only successor transaction around it. Reuse existing
  generation and worker-operation receipts, not an ad hoc PID check.
- **Do not substitute unrelated ordinary/zero-delta `automatic_retry`.**
  That path has different admission conditions and is not a proof of safe
  continuation of an interrupted worker with in-progress changes.
- Do not add a watchdog-specific durable successor-ID allocation protocol,
  parallel replacement state machine or separate exactly-once execution
  guarantee. The required safety guarantee is **at most one active worker**
  owning the task and no overlap with the proven old containment. Repeating
  arbitrary externally visible worker operations can never be generally ruled
  out by the orchestrator alone.
- In the normal live-stall case with complete, trusted retirement evidence,
  continuation is automatic through shared recovery. On service restart,
  revalidate persisted exact ownership; resume proven-safe states by that same
  shared path, and **fail closed** where existing generic admission/recovery
  cannot prove an unambiguous outcome. Do not turn a fail-closed uncertainty
  into guessed worker absence, silent successful completion or a second
  concurrent execution.
- An execution still awaiting retirement must never reach shared admission.
  Existing operator stop/reset/process-loss priorities and safeguards remain
  authoritative. The watchdog must not indiscriminately convert an
  operator-requested stop into `worker_stalled`.

If a reproducible general resource/admission replay defect also affects ordinary
process-loss recovery, fix that **shared defect** at its owning boundary when
needed to satisfy this ticket's proven-safe continuation case. Do not build a
separate watchdog replacement engine to work around it.

## Stalled recovery

When recovery threshold is reached and the worker is eligible for inactivity
classification:

1. classify the current worker attempt as stalled;
2. persist exact internal diagnostic evidence;
3. retire the exact execution ownership boundary using the proven mechanism from
   TASK-055;
4. preserve the current repository/workspace state;
5. preserve the previous attempt's execution evidence and lifecycle reason;
6. once retirement is proven, enter the shared stranded-worker normalization
   and `automatic_resume` admission path for a fresh worker against the preserved
   current state; never skip the shared generation/workspace admission checks;
7. attach a deterministic recovery-context prompt fragment to the normal worker
   startup prompt and ticket context;
8. never allow the old and replacement workers to run concurrently.

Do not implement this as an invisible `kill(); Popen()` of the same worker.

The previous attempt must remain distinguishable in durable provenance, with a
reason equivalent to:

    worker_stalled

The replacement attempt must be attributable to watchdog recovery through
existing execution provenance; attribution does not require a new independent
scheduler, retry protocol or successor transaction.

## Internal diagnostics

Devlegate may retain rich internal evidence, including fields such as:

- execution/attempt identity;
- process identity and ownership metadata;
- watchdog timestamps;
- last observed activity timestamp/type;
- service/runtime state;
- retirement result;
- preserved checkpoint/workspace identity.

This evidence is orchestrator-private and is not automatically agent-visible.

## Agent-visible recovery context

The replacement worker receives a small recovery prompt fragment that is a
deterministic projection of recorded execution evidence.

The fragment must NOT reveal orchestrator-private implementation details that the
worker would not normally know from its declared environment.

Do not expose, unless already part of the worker interface:

- execution IDs;
- PIDs/PGIDs;
- service instance IDs;
- absolute Devlegate state/worktree paths;
- internal checkpoint identities;
- cgroup/systemd details;
- statements that the worker is in a sandbox, special worktree, or restricted
  environment;
- internal watchdog implementation details.

The worker-visible fragment should contain only information that is either:

- directly observable from the current repository state; or
- part of the declared worker interaction boundary.

## Deterministic recovery prompt

The recovery prompt must not use an LLM-generated summary.

It must be reproducible from recorded evidence using deterministic formatting.

At minimum include:

1. a neutral continuation reason, for example:

       Previous attempt stopped after an extended period without observable
       output.

2. a statement that existing repository changes are current state and must be
   inspected before further edits;

3. changed relative paths derived mechanically from repository state, using a
   stable command/representation such as:

       git status --porcelain=v1
       git diff --name-status

4. a bounded verbatim tail of observed worker stdout/stderr preceding the stall;

5. continuation guidance such as:

       Continue from the current repository state.
       Do not assume the previous attempt completed.
       Inspect existing changes before repeating operations.

Do not include generated interpretations such as:

- inferred intent;
- inferred root cause;
- inferred remaining work;
- semantic summaries of failures;
- claims about why a provider/network/tool stalled.

## Recovery-context bounds

Recovery context must remain small and predictable.

Define deterministic limits for:

- maximum stdout/stderr tail lines and/or bytes;
- maximum changed-path entries;
- truncation markers;
- stable ordering;
- stable text encoding.

The exact same stored evidence and repository state must render the same recovery
fragment.

Do not inject an unbounded prior execution log into the next worker prompt.

## Status/inspection UX

Expose enough worker-liveness information for operators to distinguish:

    running and recently active
    running but idle/warning
    stalled/recovering

At minimum consider showing:

- current worker stage;
- idle duration or last-activity age;
- watchdog warning state.

Do not expose private provider internals that Devlegate does not authoritatively
know.

The watchdog must not claim a provider/network hang merely because output stopped.

## Recovery and service restart

Watchdog state must behave safely across service restart.

Required invariants:

- a restarted service must not create two replacement workers for one stalled
  attempt;
- durable stalled classification must remain attributable to the correct attempt;
- if service restart occurs while retiring/replacing a stalled worker, recovery
  must resume or fail closed from durable state;
- already completed workers must never be restarted by stale watchdog state.

Use the shared lifecycle/recovery mechanisms described above, **not** a parallel
stall-specific successor state machine. Proven-safe live and restarted states
must proceed through the ordinary automatic-resume admission. Unproven state may
remain blocked for operator reconciliation; it must never launch a speculative
second worker. The orchestrator guarantees no concurrent owned workers, not
exactly-once execution of arbitrary agent actions.

## Acceptance criteria

- A worker that remains alive but produces no stdout/stderr activity and has no
  active owned descendant/tool crosses warning and recovery thresholds
  deterministically.
- Any new stdout/stderr activity resets the inactivity timer.
- Activity immediately before the recovery boundary prevents stale watchdog
  firing.
- A quiet worker with a proven active owned child/tool is not classified as
  agent-idle solely due to worker silence.
- Recovery retires exactly the current execution ownership boundary and never
  leaves old/replacement workers running concurrently.
- Existing repository changes are preserved.
- The prior stalled attempt remains in durable provenance.
- The replacement worker receives a deterministic recovery-context prompt
  fragment.
- Recovery prompt rendering requires no LLM call.
- Agent-visible recovery context contains no orchestrator-private IDs, process
  metadata, internal paths, sandbox/worktree implementation details, or cgroup
  state.
- Bounded stdout/stderr tail and relative changed-path evidence are reproducible.
- Service restart during watchdog recovery does not admit overlapping workers,
  revive a provably completed worker, or lose the stalled-attempt attribution;
  ambiguous ownership/replay is allowed to fail closed, not to guess success.
- Proven-safe recovery reuses the shared interrupted-execution normalization and
  `automatic_resume` path; no separate stall-specific successor-admission
  state machine or unrelated zero-delta retry is introduced.
- Existing worker interruption, retry, process-loss, forced-stop, and lifecycle
  semantics remain green.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. stdout activity repeatedly resets watchdog;
2. stderr activity repeatedly resets watchdog;
3. fully quiet live worker with no descendants reaches warning state;
4. same worker reaches stalled/recovery state at configured timeout;
5. output arriving just before timeout cancels stale firing;
6. quiet worker with active owned child/tool is not agent-idle timed out;
7. normal worker completion cancels watchdog;
8. with old ownership proven retired, normal live-stall recovery reaches the
   existing shared normalization and automatic-resume path and admits a new
   worker against the preserved task workspace;
9. watchdog recovery cannot create overlapping old/replacement workers or two
   concurrent replacements, including a service restart;
10. stalled reason is durably attributable to the previous attempt;
11. existing changed files survive into replacement attempt;
12. changed-path list is relative, deterministic, and bounded;
13. stdout/stderr tail is verbatim, bounded, deterministically truncated;
14. recovery prompt contains no execution ID, PID/PGID, internal state/worktree
    path, service/cgroup identity, or sandbox disclosure;
15. recovery prompt is identical for identical stored evidence and repository
    state;
16. recovery prompt does not require OpenCode private logs/database;
17. service restart during warning/recovery does not duplicate replacement;
18. completed execution is not revived by stale watchdog state;
19. operator status exposes idle/warning state without asserting an unproven root
    cause;
20. previous worker lifecycle/recovery regressions remain green;
21. a proven-safe restarted stall converges on shared recovery, while missing or
    mismatched generation/containment evidence fails closed without new admission;
22. changes already made in the task worktree, including modified tracked and
    untracked files, remain available to the continued worker; generic recovery
    must not silently clean or overwrite them;
23. shared `automatic_resume` remains correct for ordinary process loss and
    operator-approved interruptions, with distinct `worker_stalled` attribution.

## Non-goals

This ticket does not:

- implement a generic tool timeout framework;
- parse OpenCode internal logs or SQLite state;
- diagnose provider/network failures semantically;
- add LLM-generated recovery summaries;
- expose orchestrator internals to workers;
- replace TASK-055 process-ownership work;
- introduce a general plugin system;
- create a second stalled-worker-specific replacement scheduler, durable successor
  authorization protocol or exactly-once guarantee for arbitrary agent effects.

The recovery prompt fragment is a built-in lifecycle/context component of the
worker prompt system, not a separately installed plugin.

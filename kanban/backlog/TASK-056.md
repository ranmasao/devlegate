---
"type": "devlegate.ticket"
"title": "Add worker inactivity watchdog with deterministic recovery context"
"depends_on": ["TASK-055"]
---

## Milestone

Devlegate 0.5.7 — worker liveness and autonomous recovery hardening.

## Scheduling

Deferred from 0.5.6 to 0.5.7. Do not schedule TASK-056 on the 0.5.6
release line. TASK-055 (execution ownership boundary) is already completed;
TASK-057 (forced-restart continuation intent) remains in the 0.5.6 scope and
may provide reusable lifecycle semantics for this watchdog implementation.


## Goal

Detect workers that remain process-alive but stop making observable progress,
classify them as stalled after a bounded inactivity interval, retire the exact
execution ownership boundary safely, preserve the current repository state and
execution evidence, and continue with a fresh worker attempt carrying a small,
deterministic recovery-context prompt fragment.

This ticket addresses the observed failure mode where OpenCode remained alive in
`do_epoll_wait` with an established HTTPS socket but produced no further worker
output for roughly 95 minutes after its last tool/edit activity.

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

## Stalled recovery

When recovery threshold is reached and the worker is eligible for inactivity
classification:

1. classify the current worker attempt as stalled;
2. persist exact internal diagnostic evidence;
3. retire the exact execution ownership boundary using the proven mechanism from
   TASK-055;
4. preserve the current repository/workspace state;
5. preserve the previous attempt's execution evidence and lifecycle reason;
6. create/start a fresh worker attempt against the preserved current state;
7. attach a deterministic recovery-context prompt fragment to the normal worker
   startup prompt and ticket context;
8. never allow the old and replacement workers to run concurrently.

Do not implement this as an invisible `kill(); Popen()` of the same worker.

The previous attempt must remain distinguishable in durable provenance, with a
reason equivalent to:

    worker_stalled

The replacement attempt must be attributable to watchdog recovery.

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

Reuse existing lifecycle/recovery mechanisms instead of creating a parallel state
machine where possible.

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
- Service restart during watchdog recovery does not duplicate work.
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
8. replacement begins only after old ownership boundary is retired;
9. watchdog cannot create two concurrent replacements;
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
20. previous worker lifecycle/recovery regressions remain green.

## Non-goals

This ticket does not:

- implement a generic tool timeout framework;
- parse OpenCode internal logs or SQLite state;
- diagnose provider/network failures semantically;
- add LLM-generated recovery summaries;
- expose orchestrator internals to workers;
- replace TASK-055 process-ownership work;
- introduce a general plugin system.

The recovery prompt fragment is a built-in lifecycle/context component of the
worker prompt system, not a separately installed plugin.

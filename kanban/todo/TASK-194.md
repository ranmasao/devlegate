---
"type": "devlegate.ticket"
"title": "Keep read-only operator inspection available during active worker execution"
"depends_on": ["TASK-161", "TASK-104"]
---

## Milestone

Devlegate 0.5.8 — self-hosting runtime usability and correctness.

## Goal

Allow an operator to inspect the service, tickets, committed executions and available workspace observations while the current worker continues running, without giving inspection mutation authority or weakening owner serialization.

## Context

During TASK-104 execution on 2026-11-25, devlegate inspect ticket TASK-104 failed with "devlegate: service error: service busy; mutable request was not admitted". The same admission mechanism is still present at integrated product checkpoint 3b9b6d6. submit_inspect() marks the command mutable=False, but ServiceEngine._submit_operator_command() rejects a busy scheduler regardless of this flag; ticket_reset.poll_worker() services only reset while the owner supervises a worker. Simply exempting read-only commands from the first check would leave them unserved or timed out. docs/inspect.md already describes read-only inspection including during active worker ownership, but the implementation does not deliver that contract. status and other published read-only views already work without entering the mutation queue.

## Required behavior

- During a normally owned, long-running worker execution, the existing inspect service, inspect ticket ID and inspect execution FULL_ID (including opt-in --prompt for a committed execution) must return their existing typed observations without waiting for that worker to finish. Inspect the exact committed state and preserve the distinction between committed evidence and local/runtime observations. Do not manufacture an execution report for an active reportless execution.
- Use the weakest sufficient observation boundary: service diagnostics use existing published data; committed ticket and execution evidence is bound to a coherent control revision; live local observations use existing owner-held read-only capabilities at safe points or explicitly indicate unavailable/unproven data. Do not require live workspace continuity merely to display an immutable report. Preserve existing independent prompt verification and privacy rules.
- Make inspect workspace ID usable during ordinary worker supervision, including for the current ticket: return a truthful read-only view, including live-owner blockers and unknown/unproven or incomplete observations where necessary. Do not promise a stable editable worktree while the worker changes it, or infer a reset/cleanup authorization from an observation. Preserve workspace v2 digest and its existing mutation-time revalidation contract.
- Address the shared admission boundary and worker polling path together. Prefer reusing the owner queue and existing published snapshots/capabilities. Preserve owner-thread serialization of live repository/runtime observations; do not permit concurrent access to mutable owner state from IPC threads, create another owner or add a general asynchronous execution framework. Do not perform a full status refresh merely to answer inspect service. Keep existing IPC authentication, typed schemas, response bounds and cancellation semantics.
- Check workspace ls and non-applying workspace prune (the other read-only commands sharing the same queue). Serve them when a bounded, safe observation is available; if an inventory cannot safely be observed within its budget while the worker runs, explicitly report the read-only limitation rather than a mutable-admission error. Do not broaden this ticket into redesign of inventory or cleanup. Applying prune, reset, retry and other mutations retain their existing admission/serialization rules, including reset during worker supervision.
- Avoid holding the admission lock or blocking worker supervision unnecessarily for read-only requests. Observation failure, timeout, client disconnect or an unsafe local observation must not interrupt the worker, corrupt the active operator command or grant a second mutation slot. Keep real shutdown, drain and contention diagnostics honest and distinguish an unavailable observation from a refused mutation. Cover normal worker launch and a worker supervised as part of an admitted retry/recovery command.
- Update the existing inspection/IPC documentation to match actual behavior. No new CLI commands, schema redesign, durable read receipts, migration, prompt weakening, status presentation redesign (UX-005), or changes to recovery policy. Do not modify actual self-hosting resources.

## Acceptance criteria

- A live service with an ongoing ordinary worker can answer all four inspect object types through real CLI/IPC, subject to the existing exact-object rules and honest availability of local evidence, without completing or restarting the worker.
- Committed ticket/execution evidence and saved prompt proof remain exact, and live workspace observations cannot authorize mutation; no hidden fetch, hook/filter execution, index refresh, SQLite write, ref/worktree mutation or new worker admission is attributable to inspection.
- Read-only requests coexist safely with existing owner commands and worker retirement/control transitions. Failures are bounded and accurately described. Status remains available; reset and mutable-operation admission are not weakened.
- Focused regressions, lint, diff check and exact-checkpoint hosted CI pass.

## Required regressions

1. Real service IPC/CLI fixture with a deliberately held live worker, a committed historical report with a valid saved prompt, and tickets in canonical workflow states. While that worker remains alive, inspect service, current and other ticket, exact historical execution with and without --prompt, and current workspace. Assert meaningful existing schema results, correct identities, informational=true and mutation_authorized=false, no false mutable-admission error, and no new execution. An active reportless execution must not be replaced by another report.
2. Repeat relevant observations while a worker is running through an admitted retry/recovery operation; read-only requests must not replace its command ownership. Exercise concurrent reset or stop with observation pending, and ensure worker polling, cancellation, retirement and acknowledgements remain correct.
3. Exercise live workspace mutation during observation, missing/unproven local evidence, invalid IDs, failed observation and bounded timeout. Immutable evidence remains visible when independently available; no false proof or action authorization, no worker interruption and no stale queued read later becoming a mutation.
4. Exercise workspace ls and prune preview during worker supervision; demonstrate safe read-only response or a specific bounded observation refusal. Applying prune and other incompatible mutations remain refused. Prove observation does not write receipts/state, change Git objects/refs/index/worktree or invoke executable filters/hooks; account separately for legitimate concurrent worker/owner activity.
5. Preserve idle-service inspection, published status, exact historical prompt provenance and existing reset/cleanup digest tests. Run relevant focused tests, ./dev lint and git diff --check; exact-checkpoint full hosted CI remains the independent integration gate.

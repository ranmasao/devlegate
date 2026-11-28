---
"type": "devlegate.ticket"
"title": "Restore ticket log follow continuity across execution lifecycle transitions"
---

## Milestone

Devlegate 0.5.8 — current maintenance iteration; **required before closing this iteration**. Keep in backlog until explicitly scheduled by the curator. This is an existing logging-contract regression, not a new feature or an amendment to TASK-056.

## Goal

A single ticket-scoped follow invocation continuously observes the ticket's execution logs until the canonical ticket reaches done or abandoned, including attempts that end without a durable ExecutionReport.

## Context

During TASK-056 dogfooding, execution 9416890c623b4cb7ae7ef61ebf2f7dca ended with unproven containment retirement before report publication. After operator retirement and retry, the existing ticket follow showed no new output; restarting the reader displayed the new execution's log. In src/devlegate/log_reader.py, _follow_ticket delegates to _follow_execution, whose EOF loop sleeps indefinitely when the execution is no longer active and has no report. The outer ticket observer cannot discover the replacement. The current ticket terminal check also recognizes done but not abandoned.

A second dogfooding incident occurred after the review/backlog amendment/todo transition of TASK-056: an existing ticket follower exited with `devlegate: ticket not found: TASK-056` while service logs showed another execution starting; a fresh follower attached successfully. _ticket_observation scans the mutable workflow directories sequentially, so an ordinary concurrent ticket move or checkout update can produce a transient zero-location (or double-location) observation even if every committed control generation is valid. This is a separate race from waiting for a missing execution report, but violates the same ticket-follow continuity contract.

A third dogfooding incident during TASK-200 on 2026-11-27: an established ticket follower terminated with `devlegate: execution report evidence changed or is inaccessible`; the execution report subsequently existed in canonical control history. The exact interleaving is not established. The current _TicketReportObservation treats some directory/watch or cached evidence metadata changes as fatal before it distinguishes a legitimate publication/checkout transition from actual corruption. Investigate the observed failure against current code and reproduce a concrete interleaving rather than assume that this is the same race as the previous incidents.

## Required behavior

- Ticket follow observes the ticket, not a single immutable execution ID. Retry, reset, rework, review, accepted, backlog, temporary absence of an active execution, and daemon restart do not terminate or permanently suspend it.
- On observed departure or replacement of an execution, drain the available old log and allow ticket observation to continue without waiting indefinitely for an ExecutionReport that may never be published. Distinguish missing report from a successful conclusion; allow late reports without replaying already emitted bytes. Preserve correct attribution and existing output/marker conventions.
- Detect subsequent execution IDs and stream their logs from the beginning (initial tail applies only to the initial snapshot), including when a new log file appears after admission. Preserve existing chronology and no-duplicate cursor behavior, including report/publication races.
- Terminate ticket follow at either canonical terminal state, done or abandoned, after draining observable remaining logs. Do not require a report for an attempt that legitimately ended without one. Keep execution-scoped follow semantics separate; an ended reportless execution must not be portrayed as completed successfully or wait indefinitely for an impossible report.
- Once a ticket follower has established the ticket identity, an ordinary lifecycle move or control checkout synchronization must not terminate it with a transient not-found or ambiguous-state error. Observe a coherent committed or already-published ticket-state view where practical, or treat transient inconsistent observations as inconclusive and re-observe without discarding cursors. Do not infer deletion, abandonment or corruption from one non-atomic scan. Preserve real diagnostics for unknown IDs at invocation and genuinely invalid persistent state; do not introduce a lock against the workflow owner, a new polling service, or broad retries of unrelated errors.
- Apply the same continuity contract to report discovery and chronology. A normal control publication, synchronization or replacement of a mutable checkout representation must not be mistaken for corruption of an already established immutable report. Distinguish coherent evidence identity/content from transient filesystem metadata and observation races. Preserve real detection of missing, altered, malformed, or inaccessible committed evidence; do not silently accept historical rewrites or suppress arbitrary LogReaderError exceptions. Prefer reuse of existing committed workflow/evidence projections where sufficient over introducing another independent cache/invalidation protocol.
- Keep this a read-only observation path. Do not add recovery/scheduling authority, new persistence protocols, or repeated expensive Git/provenance verification as a workaround. Preserve meaningful diagnostics for genuinely inaccessible or corrupt evidence.

## Acceptance criteria

- A ticket follower opened before a failed/reportless attempt remains useful through retry and displays subsequent execution output without operator restart.
- A follower waits through all nonterminal ticket states and exits on done or abandoned, with no silent loss or duplicate emission of observable log bytes during switches.
- A valid lifecycle transition concurrent with ticket observation does not cause a false not-found/ambiguous error or require restarting an established follower.
- Late report publication and log creation do not wedge the reader or misattribute a result. Execution-level follow remains scoped to the requested execution.
- Focused logging tests, lint and authoritative CI pass; no mutation of execution evidence or workflow state is introduced.

## Required regressions

1. Follow a live execution, end it without a report, leave the ticket nonterminal, then admit a new execution. Assert the original follower drains the old log, displays the new header and output exactly once, and remains attached.
2. Repeat across a reset and multiple review/rework cycles, including a period with no active execution and a daemon restart.
3. Delay report publication and new-log creation across execution switch; assert no missed or repeated lines, false success marker, or permanent wait on the old ID.
4. Reach done and abandoned independently, with and without a reportless prior attempt; assert remaining observable output is drained and the follower exits.
5. Exercise execution-scoped follow with a normal report and with a definitively ended reportless attempt; assert truthful bounded termination without following another execution.
6. Retain existing initial tail, chronology, read-only, corruption and Ctrl-C regressions.

7. Deterministically interleave ticket observation with an ordinary move between workflow directories (including review/backlog/todo and todo/review). Exercise a scan that would see zero locations and one that would see two, even if the move itself is atomic. Assert an existing follower remains attached, discovers the subsequent execution, and does not lose or duplicate output. Retain meaningful initial unknown-ticket and persistent invalid-state diagnostics. Prefer a deterministic interleaving test over a timing-dependent stress loop.

8. Reproduce the newly reported evidence-change termination using a deterministic interleaving of a real report publication/control synchronization with an established ticket follower. Include existing cached reports, a newly published report, and any benign directory/file identity change actually implicated by investigation. Assert that the same follower drains the old execution and follows the next execution exactly once, with preserved chronology and truthful markers. Separately exercise genuine historical report alteration/removal, malformed evidence and sustained inaccessibility: maintain meaningful failure diagnostics. Do not turn watch loss or arbitrary evidence errors into unconditional success or unbounded retries. Explain the proved cause, compare the old/new observation mechanisms and remove superseded helpers/tests rather than add a second competing observation path.

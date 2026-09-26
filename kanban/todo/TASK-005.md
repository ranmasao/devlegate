---
"type": "devlegate.ticket"
"title": "Read execution logs while executions are active"
---

## Milestone

Devlegate 0.5.5 self-hosting observability hardening.

## Goal

Allow `devlegate logs execution` to resolve and read the current execution log
before an ExecutionReport has been published.

## Context

Devlegate creates the operational execution log as soon as a worker starts, but the
current log resolver first searches `ExecutionReportStore(control).list()` and only
then maps the resolved ID to the log file.

An active execution therefore has:

- a durable execution ID;
- an existing log file;
- live/runtime ownership state;

but no ExecutionReport yet. During precisely that period,
`devlegate logs execution <id-or-prefix>` reports that the execution was not found.
The command begins to work only after the execution finishes and its report is
committed to control history.

## Required behavior

- Execution-log resolution supports the currently active durable execution identity
  even when no ExecutionReport exists yet.
- Historical execution IDs continue to resolve from canonical execution reports.
- Prefix resolution considers relevant live/current and historical identities
  together and remains fail-closed on ambiguity.
- `--follow` binds to the identity resolved at command start and cannot silently
  switch to a later execution.
- The resolver must not treat arbitrary filenames in the log directory as canonical
  execution provenance.
- Missing/invalid identities and missing log files retain clear operator errors.

## Acceptance criteria

- While a worker is active and before lifecycle report publication,
  `devlegate logs execution <id-or-prefix>` can read its existing log.
- The same command continues to work after the execution becomes historical.
- Ambiguous prefixes are rejected deterministically.
- Historical behavior and bounded-tail/follow semantics remain intact.
- Full tests and lint remain green.

## Required regressions

- Given an active execution with a durable current ID and log file but no execution
  report, when its exact ID or unique prefix is requested, then the log resolves and
  is readable.
- Given that execution later gains a report, when the same selector is requested,
  then it resolves to the same execution log.
- Given a prefix matching both a live/current identity and another historical
  execution, when the prefix is requested, then Devlegate rejects it as ambiguous.
- Given `--follow` resolved to one execution, when another execution appears, then
  the follower remains attached to the originally resolved log.

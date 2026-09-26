---
"type": "devlegate.ticket"
"title": "Make Devlegate-generated commits human-readable"
---

## Milestone

Devlegate 0.5.5 self-hosting UX hardening.

## Goal

Make ticket-related commits produced by Devlegate readable in normal Git and
GitHub history while preserving exact execution provenance outside the subject.

## Context

The first self-hosted execution produced a checkpoint subject like:

```text
Devlegate checkpoint TASK-001 14043d5cb75248ba9cc5b34f89f6859d
```

and a control lifecycle subject like:

```text
Devlegate lifecycle TASK-001 14043d5cb75248ba9cc5b34f89f6859d completed
```

These subjects expose machine identity at the expense of human meaning. Git history
is a human-facing project interface even when Devlegate owns the commit.

Execution reports already provide canonical machine provenance. Commit messages
should summarize the ticket and result, with machine identifiers carried in the
message body/trailers rather than dominating the subject.

The first implementation attempt made checkpoint commits and execution lifecycle
commits human-readable, but review found two gaps:

- the accepted-to-done control commit still uses
  `Devlegate integrate <ticket-id>`;
- the worker result is flattened into one physical line and truncated at an
  arbitrary character boundary, rather than being formatted as the requested
  concise two-to-three-line human-readable summary.

Keep the same ticket identity and harden the implementation rather than creating a
replacement task.

## Required behavior

- A Devlegate checkpoint commit subject uses the ticket identity and title in a
  human-readable form, for example
  `TASK-001: Use real Debian maintainer metadata`.
- The checkpoint commit body contains a concise two-to-three-line summary of what
  the worker actually changed, derived from the completed worker claim/report
  rather than invented independently.
- The summary formatting is deterministic, readable as raw Git commit text, and
  does not cut a word at an arbitrary character boundary. The two-to-three-line
  requirement refers to the summary text itself, excluding labels, blank lines,
  and trailers.
- The full execution ID is absent from the subject and is retained in a stable
  trailer such as `Devlegate-Execution: <execution-id>`.
- Ticket-related control-plane commits are also human-readable. This explicitly
  includes both execution lifecycle commits and the accepted-to-done integration
  commit.
- Where the execution report is available, the accepted-to-done control commit
  carries a concise result summary and stable provenance rather than falling back
  to a bare `Devlegate integrate <ticket-id>` subject.
- Accepted product integration keeps the checkpoint commit unchanged when it
  fast-forwards the product branch, so the human-readable checkpoint message
  becomes the product history message.
- Commit messages are diagnostic metadata only. Recovery, lifecycle proof, and
  integration correctness must not depend on parsing human-facing prose.
- Questions, remaining work, raw logs, or long worker output are not copied into
  commit messages.

## Acceptance criteria

- `git log --oneline` for a completed ticket shows a meaningful ticket/action
  subject without the execution hash in the subject.
- `git show` for the checkpoint contains a readable two-to-three-line
  worker-result summary and the full execution identity in a machine-readable
  trailer.
- Execution lifecycle and accepted-to-done control commits have human-readable
  subjects and preserve useful summary/provenance without weakening exact
  transition validation.
- No ticket-related generated commit falls back to
  `Devlegate integrate <ticket-id>` when the ticket/report context required by
  this workflow is available.
- Existing execution reports remain the authoritative provenance record.
- Full tests and lint remain green.

## Required regressions

- Given a completed worker claim with a ticket title and execution ID, when
  Devlegate creates a checkpoint, then the subject is human-readable, the summary
  occupies two-to-three physical text lines without mid-word truncation, and the
  full execution ID appears outside the subject.
- Given that checkpoint is accepted and integrated by fast-forward, when product
  history is inspected, then the same human-readable commit message is present.
- Given an execution lifecycle transition on the control branch, when its generated
  commit is inspected, then its subject is readable without requiring the execution
  hash and exact lifecycle validation still succeeds.
- Given an accepted ticket with an execution report, when Devlegate completes the
  accepted-to-done transition, then the generated control commit has a
  human-readable ticket/action subject plus concise summary/provenance rather than
  `Devlegate integrate <ticket-id>`.
- Given recovery or replay, when commit prose changes within the supported format,
  then protocol correctness is still derived from canonical state/evidence rather
  than commit-message parsing.

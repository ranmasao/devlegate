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

## Required behavior

- A Devlegate checkpoint commit subject uses the ticket identity and title in a
  human-readable form, for example
  `TASK-001: Use real Debian maintainer metadata`.
- The checkpoint commit body contains a concise two-to-three-line summary of what
  the worker actually changed, derived from the completed worker claim/report
  rather than invented independently.
- The full execution ID is absent from the subject and is retained in a stable
  trailer such as `Devlegate-Execution: <execution-id>`.
- Ticket-related control-plane commits are also human-readable. Where the execution
  report is available, their body may repeat the same concise result summary and
  provenance; duplication between product and control history is acceptable.
- Accepted integration keeps the checkpoint commit unchanged when it fast-forwards
  the product branch, so the human-readable checkpoint message becomes the product
  history message.
- Commit messages are diagnostic metadata only. Recovery, lifecycle proof, and
  integration correctness must not depend on parsing human-facing prose.
- Questions, remaining work, raw logs, or long worker output are not copied into
  commit messages.

## Acceptance criteria

- `git log --oneline` for a completed ticket shows a meaningful ticket/action
  subject without the execution hash in the subject.
- `git show` for the checkpoint contains a concise worker-result summary and the
  full execution identity in a machine-readable trailer.
- Relevant control transitions have human-readable subjects and preserve required
  provenance without weakening exact-transition validation.
- Existing execution reports remain the authoritative provenance record.
- Full tests and lint remain green.

## Required regressions

- Given a completed worker claim with a ticket title and execution ID, when Devlegate
  creates a checkpoint, then the subject is human-readable, the body contains the
  concise result summary, and the full execution ID appears outside the subject.
- Given that checkpoint is accepted and integrated by fast-forward, when product
  history is inspected, then the same human-readable commit message is present.
- Given a ticket lifecycle transition on the control branch, when its generated
  commit is inspected, then its subject is readable without requiring the execution
  hash and exact lifecycle validation still succeeds.
- Given recovery or replay, when commit prose changes within the supported format,
  then protocol correctness is still derived from canonical state/evidence rather
  than commit-message parsing.

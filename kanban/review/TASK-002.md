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

The first self-hosted execution produced checkpoint/control commit subjects dominated
by execution IDs. Git history is a human-facing project interface even when
Devlegate owns the commit, while ExecutionReport remains the canonical machine
provenance record.

Two implementation attempts have now been reviewed.

The first attempt left the accepted-to-done control commit as
`Devlegate integrate <ticket-id>` and flattened/truncated the worker summary.

The second attempt fixed those UX gaps, but exact-head CI run `36253734690`
failed with five tests. At least two failures are direct regressions from this
change:

- `tests/test_commit_messages.py` was added without the mandatory Devlegate
  EUPL source header, failing
  `test_devlegate_owned_source_has_exact_eupl_header`;
- changing `_complete_accepted` from two arguments to three broke existing
  monkeypatch/recovery test seams, including
  `test_live_accepted_integration_window_precedes_dependent_scheduling`,
  which raised a TypeError. Three real-service recovery tests also timed out in
  the same CI run and must be investigated as part of the changed integration path.

The worker report incorrectly described the remaining full-suite failure as
pre-existing/unrelated. Exact-head CI is authoritative for acceptance.

The worker-facing contract for `devlegate_report.summary` is also currently too
weak: the tool schema only says `summary: string`, so workers legitimately mix
implementation summary, validation counts, and other status prose. Because summary
is now used in human-facing Git history, define its intended semantics at the
egress boundary rather than trying to infer meaning in the formatter.

Keep the same ticket identity and harden the implementation rather than creating a
replacement task.

## Required behavior

- A Devlegate checkpoint commit subject uses the ticket identity and title in a
  human-readable form, for example
  `TASK-001: Use real Debian maintainer metadata`.
- The checkpoint commit body contains a concise two-to-three-line summary of what
  the worker actually changed, derived from the completed worker claim/report.
- The worker-facing `devlegate_report` contract explicitly defines `summary` as
  a concise human-readable description of implemented changes, normally one to
  three short sentences. It must not be used for test counts, validation status,
  remaining work, questions, execution IDs, or provenance.
- `remaining` continues to carry concrete unfinished work; `questions` carries
  required external questions/decisions.
- The commit formatter performs deterministic safe normalization/wrapping only. It
  must not invent a semantic summary, cut words at arbitrary character boundaries,
  or attempt to classify free-form worker prose after egress.
- The full execution ID is absent from the subject and retained in a stable trailer
  such as `Devlegate-Execution: <execution-id>`.
- Ticket-related control-plane commits are human-readable, including execution
  lifecycle commits and the accepted-to-done integration commit.
- Where an execution report is available, the accepted-to-done control commit
  carries concise result summary and stable provenance rather than
  `Devlegate integrate <ticket-id>`.
- Accepted product integration keeps the checkpoint commit unchanged when it
  fast-forwards the product branch.
- Commit messages remain diagnostic metadata only. Recovery, lifecycle proof, and
  integration correctness must not depend on parsing their prose.
- All new Devlegate-owned Python source/test files satisfy the repository licensing
  header invariant.
- Changes to integration APIs preserve or deliberately update all existing recovery
  and test seams; no previously green exact-head CI test may be dismissed as
  unrelated without evidence.

## Acceptance criteria

- `git log --oneline` for a completed ticket shows a meaningful ticket/action
  subject without the execution hash in the subject.
- `git show` for checkpoint/lifecycle/integration commits contains a readable
  concise worker-result summary and stable provenance trailers.
- The generated OpenCode `devlegate_report` tool and/or core worker contract tells
  the worker what belongs in `summary`, `remaining`, and `questions`.
- Accepted-to-done integration no longer emits a bare
  `Devlegate integrate <ticket-id>` commit when report context is available.
- Existing execution reports remain the authoritative provenance record.
- Exact-head CI for the resulting checkpoint is green, including licensing,
  accepted-integration, and real-service recovery tests.
- Full tests and lint remain green.

## Required regressions

- Given a completed worker claim, checkpoint message formatting keeps the execution
  ID out of the subject, keeps words intact, and produces a concise readable body.
- Given the generated worker contract/tool description, a worker is explicitly
  instructed that `summary` describes implemented changes and excludes validation
  status/remaining work/provenance.
- Given that checkpoint is accepted and fast-forward integrated, product history
  preserves the same human-readable checkpoint commit.
- Given lifecycle and accepted-to-done control transitions, generated control
  commits are human-readable and exact-transition validation still succeeds.
- Given recovery/replay and existing monkeypatch seams around accepted integration,
  changed internal method signatures do not break recovery behavior or tests.
- Given newly added Devlegate-owned test/source files, licensing-header validation
  passes.
- Given the exact full CI suite, the previous five failures from run
  `36253734690` do not recur.

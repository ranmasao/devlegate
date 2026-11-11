---
"type": "devlegate.ticket"
"title": "Collect operator-facing UX friction and actionable CLI diagnostics"
---

## Milestone

Deferred Devlegate operator and developer experience improvements. **Backlog collection only**: do not schedule, implement or automatically requeue this ticket while observations are still accumulating. When a coherent release-sized set is ready, explicitly refine and split it into bounded implementation tickets, retaining this register for future observations.

## Goal

Maintain one canonical, evidence-backed register of operator-facing UX problems discovered during self-hosted development and Arch dogfooding. Record the exact user action, observed behavior, likely boundary, desired behavior and regression idea. Prioritize actionable errors over cosmetic wording, without weakening runtime ownership or provenance checks.

## Context

An operator tried `./dev package arch --ticket TASK-089` first while the self-hosted Devlegate service was running and then after `devlegate stop`. Under integrated TASK-156, both attempts correctly refused unsafe/unproven source selection, but the *first* diagnostic gave no indication that a running owner was the reason. The *second* diagnostic correctly identified the immutable historical report `34878bd12a3844a7ab4057bffb635c8e` with missing `resource_generation` and suggested the clean exact-checkpoint `--repo` manual path.

TASK-156 already fixed the previous interactive-TTY silent `exit 1` and added fail-closed historical identity diagnostics. **Do not duplicate or regress those completed fixes.** A UX improvement must preserve the existing correct refusal semantics.

## Required behavior

1. Keep this ticket in backlog as a living issue register. For each new observation append a numbered entry with date/context, exact command or workflow, actual output, expected operator action, safety implications, and a reproducible acceptance/regression case when possible. Separate confirmed behavior from speculation; reference the ticket/commit that already fixed an observation.
2. Before scheduling implementation, triage observations by common boundary and scope into individually reviewable tasks. Do not make a single worker implement an ever-growing mixed-feature backlog. Preserve references back to this register and explicitly close or supersede addressed items instead of silently deleting historical observations.
3. Diagnostics should communicate *what was refused* and *what the operator can safely do next* when the reason is known. Do not leak secrets, private state paths, internal lock details or mutable resource identities; do not suggest stopping a service if the failure is not actually established to be owner contention.
4. Preserve nonzero exit codes, read-only source selection, the distinction between runtime/project authority and historical generation evidence, and the prohibition on inferred or fabricated provenance. No data migrations, compatibility layer or change to TASK-089 behavior merely for better UX.

## Observations

### UX-001 — Running owner incorrectly presented as generic provenance refusal

- **Status:** Open; first actionable UX issue.
- **Observed 2026-11-11** with installed Devlegate `0.5.7.dev0` sourced from `f6a61185ec4c08c8d4985509e663458ca1c36b63`.
- **Action:** From the Devlegate project checkout, execute `./dev package arch --ticket TASK-089` while the corresponding Devlegate service is running.
- **Actual stderr:**
  ```text
  dev: package: ticket source refused: independent execution/resource evidence could not be verified; check owner retirement and generation receipts
  ```
- **Observed control:** `devlegate stop` reports `service stopped`; a retry then produces the precise historical refusal:
  ```text
  dev: package: ticket source refused: latest execution 34878bd12a3844a7ab4057bffb635c8e lacks checkpoint identity (resource_generation); no independent generation binding remains; use --repo with an independently verified clean exact-checkpoint checkout for manual testing
  ```
- **Problem:** The running-owner refusal gives an unrelated-looking hint about generation receipts; the user cannot tell whether waiting, stopping the owner or repairing evidence is appropriate. The observed sequence strongly indicates authority contention, but the implementation must prove the actual typed reason before giving specific advice.
- **Desired:** When the *typed* failure specifically indicates the project runtime/owner is active, present a concise distinct explanation and safe next step (for this CLI, stop the corresponding service using `devlegate stop`, then retry). If the owner is absent and generation evidence is missing, preserve the current exact historical-provenance diagnostic. Other ambiguous/unverified resource errors must remain generic and fail closed.
- **Regression:** In disposable project/owner fixtures, simulate (a) a verified active owner and (b) a stopped owner with missing generation in the latest report. Invoke the real CLI through the intended dispatcher, assert distinct actionable stderr, no raw private path or secret, nonzero exit, and no mutation of owner state, worktrees, refs or execution reports. Include a third unrelated `UnprovenResource` refusal to prove it is **not** mislabeled as active-owner contention.
- **Relationship:** Follow-up to TASK-156's otherwise successful diagnostics; not a reason to reopen or roll back TASK-156.

## Acceptance criteria

- Each UX observation is independently reproducible or explicitly marked as unconfirmed; command/output evidence and anticipated safe operator path are preserved.
- When promoted to implementation tickets, each chosen improvement has a bounded scope, regression coverage and no reduction in provenance/ownership safety.
- UX-001, if implemented, distinguishes **confirmed active owner** from **unproven historical checkpoint** and never advises unsafe mutation or invents source identity.
- This accumulating backlog item does not become an automatically scheduled or unbounded implementation task.

## Required regressions

- Reproduce UX-001 in a disposable Git-backed project with active owner: diagnostic must name owner contention and suggest safe operator action; exit remains nonzero and no source/owner state changes occur.
- Stop the owner and retain a latest historical report lacking `resource_generation`: preserve a specific latest-execution refusal and the clean `--repo` manual testing guidance.
- Inject a distinct insufficient-receipt/resource fault without a confirmed active owner: diagnostic must **not** falsely say the service is running.
- For subsequent UX observations, attach separate scenario-specific regression expectations at triage rather than generalizing all failures into one message.

## Boundaries

This is a persistent backlog register, **not** an implementation authorization. No owner runtime, ticket worktree, release branch, immutable review/report, archival asset, Git ref or systemd resource is to be modified by adding or updating this document. Keep user observations and hypotheses distinguishable and avoid unnecessary historical-data migrations.

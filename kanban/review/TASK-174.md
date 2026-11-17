---
"type": "devlegate.ticket"
"title": "Require focused worker validation and final Ruff gate without redundant local full suites"
---

## Context

The packaged implementation worker core contract currently says only "Run focused validation as appropriate." Workers have sometimes skipped lint and then failed hosted CI on avoidable Ruff issues such as overlong lines. Conversely, unrestricted local pytest after routine edits wastes time because exact-checkpoint GitHub Actions CI already runs the complete suite with branch coverage and Ruff.

## Goal

Establish an explicit, economical validation contract for FRESH, RESUME and REWORK workers. Make final Ruff and whitespace validation mandatory for a truthful completed claim, while using the smallest tests that adequately prove changed behavior during local iterations. Do not change GitHub CI or weaken independent exact-SHA acceptance.

## Required behavior

1. Update `src/devlegate/default_templates/prompts/core_contract.txt`. During normal editing, run targeted pytest node IDs or narrowly scoped semantic test files rather than `./dev check`, an unrestricted `pytest`, or repeated full-suite runs. Choose by behavioral domain and required proof boundary, not solely imports or filenames. When a change affects IPC owner handoff, OS process identity, durable Git/SQLite effects, recovery or similar boundaries, include the relevant stronger focused tests; cheap unit checks alone do not suffice.
2. Before claiming `completed`, require the selected final focused tests for the whole ticket to pass, `./dev lint` to pass across `src tests tools`, and `git diff --check` to pass. Re-run failing applicable checks after fixing issues. Avoid re-running the unchanged full local suite just to reproduce hosted CI. If unavailable/blocked or failing checks cannot be resolved, do not claim successful validation or a completed implementation; report the real constraint under existing `incomplete`/`blocked` semantics.
3. Make clear that hosted exact-checkpoint CI with the complete configured suite, coverage, and lint is required for independent review and integration. Local tests are a cheap defect filter, not formal proof of complete regression safety. Do not ask workers to put test counts, command transcripts, or validation results into `devlegate_report.summary`; existing worker logs/CI can supply evidence. Do not alter `devlegate_report` protocol.
4. Keep the core worker authority and continuation contract unchanged: worker never commits/pushes/moves tickets, cannot claim `completed` merely on tidy intermediate progress, and on RESUME/REWORK continues the canonical assigned work. No new runtime flags, test profile framework, persistent coverage statistics, or external dependencies.
5. Update the focused development guidance only as needed to distinguish `./dev test`, `./dev lint`, `./dev check` and hosted CI; preserve the existing `./dev` interface. Add tests in `tests/test_worker_prompt.py` verifying the exact contract propagates to every directive and preserves protected worker instructions. Do not try to prove actual LLM compliance by asserting the presence of text alone.

## Acceptance

- The canonical prompt for each directive unambiguously requires successful final Ruff and whitespace checks and proportionate focused tests, but not redundant local full-suite runs.
- Tests and documentation distinguish local validation from exact-checkpoint hosted CI without changing the configured CI job.
- Focused prompt regressions, `./dev lint`, `git diff --check`, then hosted CI are green.
- Reviewer checks the actual prompt semantics, not only a keyword assertion.

## Boundaries

Only worker-validation policy, its regression tests and directly relevant docs. Do not reorganize tests, refactor runtime, modify operational service authority, alter CI, or change result/report schemas. The broader test inventory is separately tracked by TASK-168.

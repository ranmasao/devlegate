---
"type": "devlegate.ticket"
"title": "Prioritize critical-path work in worker and reviewer contracts"
---

## Milestone

Devlegate 0.5.7 — prompt/role-protocol quality; narrow follow-up to completed TASK-087 (worker termination semantics) and the repeated TASK-145 review/rework executions. **Keep this ticket in backlog until explicitly scheduled.**

## Goal

Improve the existing implementation-worker prompt and Reviewer role protocol so a worker facing multiple unfinished requirements preferentially makes progress on the **critical path to satisfying the canonical ticket**, rather than using an invocation on a locally complete but non-blocking improvement. Preserve honest `completed`/`incomplete`/`blocked` claims, exact review feedback attribution, and all existing lifecycle boundaries. This is a **small prompt/template change**, not a new planning subsystem.

## Context

TASK-087 already strengthened `src/devlegate/default_templates/prompts/core_contract.txt`: a passing focused test, completed local `todowrite`, or tidy checkpoint does not justify prematurely ending the invocation; `incomplete` requires concrete `remaining` and an actual stop reason. The Architect and Reviewer skills already require bounded assignments and review of the entire canonical specification. The shipped rationale is `docs/PROMPT_CONTRACT_AUDIT.md`. Do **not** undo or duplicate that policy.

TASK-145 exposed a different gap across nine executions: some useful iterations improved an XFAIL-only test or auxiliary candidate cleanup while the main scheduler continuation / durable Git swap remained unimplemented. The decisive rework reviews specified a **single primary objective, its concrete implementation boundary and the dependent validations**. This is observational evidence, not an A/B demonstration that a prompt change will eliminate extra sessions.

The actual worker prompt is composed by `src/devlegate/worker_prompt.py` from the packaged core contract, a FRESH/RESUME/REWORK directive, canonical assignment and verified current review feedback. The project role-source templates are `.devlegate/templates/skills/*/SKILL.md.tmpl`, packaged defaults are `src/devlegate/default_templates/skills/*/SKILL.md.tmpl`, and `skills/*/SKILL.md` are generated, not editable sources.

## Required behavior

1. **Worker critical-path selection.** In the existing `core_contract.txt`, instruct the worker to assess unfinished acceptance criteria and important dependencies *before* choosing edits, identify the primary obstacle to whole-ticket completion, and prioritize actions which remove it. A smaller safe patch, added diagnostic, expanded test or housekeeping improvement must not substitute for the blocking implementation simply because it is easier to finish. A minimal reproducing test, safety precondition or design investigation **may** be the critical-path action when necessary; do not mandate production-code-first or unsafe continuation.
2. **Continuation and evidence.** On RESUME/REWORK, inspect retained code and the **current injected review feedback**, distinguish satisfied requirements from outstanding ones, preserve valid work, and target the review's most important unresolved blocker. Treat multiple review findings as dependencies/priorities, not a flat invitation to make arbitrary progress. Keep FRESH and generic RESUME usable when no curator feedback is present; do not introduce control-worktree reads or other Git lifecycle actions by the worker.
3. **Useful `incomplete` handoff.** Without changing the `devlegate_report` API or report schema, tell the worker to include in existing `remaining`/`report`: the primary unfinished objective, next concrete action and truthful reason the invocation must end. It must not fabricate inability to proceed, claim `completed` for a local subtask, or keep working past genuine safety/shutdown/resource limits. Avoid duplicating the established termination contract verbatim.
4. **Reviewer prioritization.** Update the existing Reviewer *source template* to require actionable REWORK feedback distinguishing (a) the **primary acceptance-blocking outcome and suggested next verifiable action**, (b) mandatory dependent checks, and (c) optional/non-blocking improvements. The reviewer still independently checks the **whole** canonical ticket; priorities do not waive criteria, authorize unsafe partial acceptance, create additional hardening tickets or quietly broaden the specification. Preserve exact execution-scoped review artifacts and role prohibitions.
5. **Artifact consistency.** Update packaged Reviewer default, project-owned managed Reviewer source, and rendered Reviewer skill through the existing generation/upgrade machinery. Update packaged worker prompt source (and, if justified, only concise FRESH/RESUME/REWORK directive wording). Do not edit generated skills in isolation. Preserve installed-runtime prompt reload semantics and conflicts on customized project templates.

## Acceptance criteria

- The constructed worker prompt clearly instructs critical-path prioritization across applicable FRESH, RESUME and REWORK executions while retaining current outcome semantics, safety boundaries, original assignment fencing and exact current-feedback provenance.
- The Reviewer protocol produces focused feedback with an identifiable primary mandatory objective and dependent verification, while remaining responsible for the full canonical acceptance criteria.
- All packaged/project-managed/rendered Reviewer artifacts agree; rendering and managed upgrades continue to reject customized/conflicting sources instead of silently overwriting them.
- No new ticket format, outcome type, field in `devlegate_report`, durable execution-plan schema, worker-to-control access, CLI/IPC command, migration, or scheduler policy is introduced.

## Required regressions

1. Extend `tests/test_worker_prompt.py` to verify the new critical-path contract reaches FRESH, RESUME and REWORK and that injected current feedback remains verbatim, fenced and traceably bound; preserve tests for truthful `completed`/`incomplete`/`blocked` semantics and no private control-plane instructions.
2. Extend `tests/test_agent_protocol.py` to verify generated Reviewer wording, template/source/render consistency, and safe managed `upgrade-templates` behavior, including customized-source refusal.
3. Use a documented TASK-145-like scenario (blocking implementation vs optional diagnostic/test polish) to make the normative priority distinction concrete in the source or tests **without claiming that textual assertions prove actual LLM choices**. Keep regression tests deterministic and scoped to delivered contract bytes and rendered artifacts.
4. Run focused prompt/protocol tests, Ruff and full configured CI; update `docs/PROMPT_CONTRACT_AUDIT.md` with the distinct *work prioritization* gap and bounded, observational evaluation guidance. No mandatory A/B, artificial per-criterion state or automatic semantic enforcement.

## Boundaries

Do not reopen TASK-087 or rewrite past TASK-145 evidence. Do not change the active worker protocol, Git integration, ownership, lifecycle or status rules beyond prompt/template edits and their tests/docs. Do not force decomposition of a single coherent ticket into new tickets. Preserve the current worker/reviewer/architect authority separation.

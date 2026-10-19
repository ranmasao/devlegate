---
"type": "devlegate.ticket"
"title": "Audit architect and reviewer skills for current Devlegate workflow"
---

## Context
The generated `skills/architect/SKILL.md` and `skills/reviewer/SKILL.md` act as role contracts for curator decisions. The project now has review artifacts, multi-generation rework, exact-checkpoint CI, provenance and remote evidence protection, installed-runtime dogfooding, and a strong objective to minimize HitL. The current documents may be stale, excessively procedural, missing critical obligations or poorly structured for LLM use.

## Goal
Independently assess whether both role skills still express the correct authorities, workflow and acceptance standards; whether SKILL.md is the right form; and how to maintain them reliably.

## Required study
1. Read the **generation sources**, generated skills, project context, current Kanban/review examples, lifecycle implementation and test/verification policy. Note possible stale generated references (e.g. product branch names) against current configured behavior; verify rather than assume.
2. Audit role separation and permitted operations: architect authors/specifies/prioritizes; reviewer independently evaluates evidence and moves `review -> todo` or `accepted`; engine owns integration and `done`. Ensure no advice invites unsafe direct Git edits, bypasses ownership or conflates execution `completed` with acceptance.
3. Test the documents against concrete recent situations: worker `incomplete` with exit 0, red CI, no report artifact, successive rework, failed/zero-delta retry, safe control changes, evidence of installed vs running runtime, autonomous diagnosis and escalation.
4. Assess information architecture: short role entrypoint vs normative policies/references/checklists, structured machine-checkable contracts where appropriate, prompts/examples, duplication, drift prevention, versioning, generation ownership and context/token cost. Compare viable formats instead of presupposing more SKILL.md prose is best.
5. Propose a revised, coherent skill structure and a migration/validation plan with testable decision scenarios; identify any required code/contract changes separately. Implement only if explicitly scoped in a follow-up ticket.

## Boundaries and acceptance
- Audit/report first; **do not manually edit generated SKILL.md** or silently change runtime/role authority in this ticket.
- Source-of-truth location and regeneration path must be explicit.
- Deliver evidence-linked gap analysis, proposed document tree/schema, role decision matrix, example decision tests, and prioritized small follow-ups.
- Backlog only, pending stabilization and explicit scheduling.

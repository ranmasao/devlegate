---
"type": "devlegate.ticket"
"title": "Fresh operator reset read-only preview over proven resource generations"
"depends_on": ["TASK-125", "TASK-150"]
---

## Context and authority

Fresh implementation of the still-missing operator preview from deferred TASK-129, following the pattern of archived TASK-134 -> fresh TASK-150. TASK-125 and TASK-150 are durably done; work from the current integrated product branch and its existing ResourceBinding/ResourceProof API.

The latest historical TASK-129 worker execution (5c739d11a3d0471e811124cf9b0df088) and checkpoint (a96f51d670e0c0843dc1bda02c51c1ae62da74d0) predate the independent generation receipts. They cannot be adopted as an owned execution under TASK-150. Leave the original TASK-129 ticket in backlog until an independent operator disposition after this successor is accepted. Preserve the exact historical ticket blob, review files, execution reports, worktree/refs and pinned evidence. Study that history as non-authoritative design input if useful, but do not cherry-pick the old checkpoint, migrate/adopt an old generation, fabricate receipts or add compatibility branches to production.

TASK-130/131/132 remain blocked on their existing dependencies. This ticket DOES NOT itself close TASK-129 or modify their requirements.

## Goal

Deliver the read-only, owner-serialized preview requested by TASK-129, using the existing CLI/IPC and Git observation boundaries. Provide `devlegate clean inspect <ticket-id> --json` (and existing output conventions where appropriate), and a narrow reusable structured plan/proof interface for TASK-130. Do not perform reset or resource retirement.

## Required contract

1. **Ticket/control provenance.** Observe the exact canonical ticket ID, state, path/blob SHA, control commit and workflow/dependency identity. Correlate immutable execution reports, review/checkpoint provenance and accepted/abandoned dispositions when applicable. Detect missing, stale, contradictory or ambiguous execution generations. A legacy report without independent ResourceBinding proof must remain unproven, even if Git SHA/path matches.

2. **Current resource inventory.** Report exact configured execution worktree path, Git registration and HEAD, clean/dirty/locked state, submodule integrity, local execution ref and precisely observed upstream ref (SHA, absent or unavailable). Reject foreign/misregistered worktrees, symlink substitution, changed refs, dirty resources and conflicting Git administrative state. Reuse the TASK-150 read-only generation proof rather than deriving ownership from names or matching commit SHA.

3. **Owner and pending intents.** Reflect pending execution, worker ownership, retry, recovery, reconciliation, integration, abandonment, publication and retirement intents that could make reset ineligible. Distinguish fully supported observations from unknown/unsupported ones; report actionable blockers instead of silently treating absence of evidence as absence of ownership.

4. **Deterministic confirmation-ready preview.** Produce a versioned, structured, stable identity-bound plan, with canonical SHA-256 digest over the fields that future TASK-130 confirmation must bind (ticket/control/blob, execution/report/generation, resource identity, exact observed ref values and relevant pending authority). Exclude unstable presentation fields from the digest. Relevant identity change must change the digest or fail the preview; unchanged snapshot must reproduce it. Clearly distinguish an informational preview from authorization to mutate: the digest alone is never reset authority, and TASK-130/131 must reobserve preconditions and apply their own owner/CAS/lease guards.

5. **Zero mutation.** Preview must not fetch, modify Git index or reflogs, create refs/worktrees, launch hooks/filters that write, persist SQLite or modify any runtime/worker/resource evidence. Use read-only observation capabilities and explicit unknown results when Git config cannot prove read-only cleanliness. Integrate through existing owner-serialized IPC and native CLI output conventions; avoid a second runtime or cleanup engine.

## Acceptance and tests

- Isolated real-Git and owner/IPC tests for an independently receipted fresh generation; absent resources; pristine, dirty, locked, symlinked, foreign and recreated-at-same-SHA worktrees; changed/missing local refs; upstream drift/absence/unavailability; submodules; report/review disagreements; multiple historical executions; pending ownership and conflicting intents.
- Verify unchanged input produces the same canonical preview digest and every material identity/authority change invalidates the prior preview.
- Assert the read-only path causes no Git filesystem mutation, SQLite revision change, worktree/ref modification, fetch, or unintended worker launch, including on refusal.
- Explicitly exercise old unbound historical TASK-129/TASK-073-like evidence as unsupported for reset, without effects on their actual resources. Use disposable fixtures only.
- Document preview semantics, fields, remote ABA limitation and follow-on TASK-130 confirmation contract. Run focused tests, full configured CI and Ruff; worker must submit a reviewable complete result.

## Boundaries

No migration or historical-generation adoption, no retroactive receipts, no generic deferred execution continuation, no destructive reset/apply, no non-done resource cleanup, no TASK-073 or TASK-129-specific production paths, no unrelated distribution changes. Preserve immutable control/report/review evidence, WAL/CAS, existing service lock and fail-closed behavior.

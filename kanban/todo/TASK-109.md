---
"type": "devlegate.ticket"
"title": "Diagnostic architecture audit of Git responsibilities and runtime decomposition"
"depends_on": ["TASK-071", "TASK-072", "TASK-078", "TASK-079", "TASK-080", "TASK-081"]
---

## Goal and scheduling

Conduct the curator-requested **diagnostic architecture audit** on the current integrated `release/0.5.7` HEAD. This is a fresh, independent audit generation rather than a rework of the historical TASK-073 final story acceptance gate. TASK-073 remains deferred in backlog; its earlier investigations, original criteria, and reported deficiencies are useful input evidence, **not** automatically inherited rework provenance.

The immediate aim is to determine which responsibilities in `src/devlegate/runtime.py` merit further extraction, which previously extracted modules or forwarding layers should be consolidated, and which implementation choices are already sound. **Do not perform final Git-story acceptance or product refactoring in this ticket.** Human curator decides subsequent work and any final acceptance.

## Required diagnostic work

1. Audit the exact integrated product HEAD; map ownership and dependency edges across `runtime.py`, `git.py`, Git domain workflows, execution workspaces, recovery and reconciliation, durable state, admission, scheduler, worker lifecycle, and host/daemon interfaces. Report source functions/call chains rather than just LOC counts.
2. Identify **cohesive extractions** from `ServiceEngine`: specify responsible domain, proposed destination module/owner, needed API boundary, transaction/authority and WAL constraints, risk, dependencies, and focused regression proof. Explicitly reject cosmetic one-line forwarding wrappers.
3. Identify **over-decomposition/consolidation** opportunities: redundant low-level Git command building, thin indirection, overlapping error handling, circular dependencies, duplicate typed observations, bloated Protocol seams, or duplicated transactional policy. Compare `keep`, `extract`, `merge`, `repair`, and `investigate` options; include do-nothing where appropriate.
4. Revisit historical TASK-073 findings on `_git_runtime` retry/interruption, remaining raw Git paths, typed error semantics, authority/owner checks, CAS, provenance, publication and recovery. Validate whether each issue persists on current integrated HEAD before repeating it as a defect. Distinguish proven bugs, architectural debt, and unverified hypotheses.
5. Inventory executed Git effects for representative local reads, sync, checkpoint/publication, evidence pin, recovery, reconciliation, and historical workspace cleanup. Include nested subprocesses and local/remote effects. Classify operations as local read, remote read, or state-changing (fetch is a local mutation). Account for bounded candidate screening and full cleanup inventory; identify N+1 effects. Report measured traces and honest evidence gaps, rather than conflating source call sites with process counts.
6. Deliver a prioritized **proposal only** for small implementation follow-ups, with concrete functions, invariants, regression strategy, risk/effort and dependency order. Do not create additional tickets or modify product behavior without curator approval.

## Output and validation

Produce a durable architecture audit document in the product repository with current HEAD, traceable references, responsibility and dependency map, concrete extraction/consolidation candidates, numerical Git effects where substantiated, outstanding measurement gaps, and a ranked decision matrix. Preserve useful verified insights from existing Git command inventory and previous TASK-073 reports; do not duplicate exhaustive broad CI runs. Where needed run narrowly selected focused checks or instrumentation, and reference existing exact-SHA GitHub Actions proof.

No migration code, generic VCS abstraction, arbitrary mutations, expanded frameworks, or changes to historical control/review/execution evidence. This is analysis and recommendations only.

## Completion and approval boundary

The audit is **completed** if its own evidence-based diagnostic deliverables are complete, even if it recommends significant further code changes or finds that historical TASK-073 final-acceptance criteria remain unmet. Report missing measurements explicitly and distinguish them from omitted required analysis. The worker and Reviewer must not claim that completing this audit approves the Git-story architecture. Final acceptance, consolidation choices and implementation scheduling require explicit curator decision.

## Provenance scheduling note

TASK-073 had a prior reviewed execution and was deferred from review to backlog. The 2026-10-29 backlog-to-todo promotion simultaneously modified its ticket specification, which the current rework provenance validator correctly refuses to bind. To preserve history without rewriting published Git commits or relaxing validation, this fresh diagnostic scope is scheduled as TASK-109. The old TASK-073 remains unchanged and deferred; its final disposition requires separate curator consideration.

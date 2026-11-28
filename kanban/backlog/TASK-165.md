---
"type": "devlegate.ticket"
"title": "Repeat Git architecture and command-effect audit on current integrated product"
---

## Post-audit scope and scheduling — 2026-11-28

Retain the full independent Git architecture/effect audit and TASK-073 crosswalk. Phase I–III identify local static effect maps and known legacy paths but do not account for all executed commands, conditional effects, runner semantics or full traces. Schedule after principal L/C/I and observation substitutions so evidence reflects the architecture actually retained. Each intervening implementation must still verify its own changed Git effects, authority and crash cuts. Include TASK-198–202, TASK-191 and subsequent committed-read paths; re-evaluate raw runtime runner/shutdown behavior, CAS, remote leases, exact effect acknowledgement and local-vs-remote classification. Use observed disposable traces, not source call-site estimates as measured counts. Do not conflate TASK-163 optimization with correctness of this architecture. Negative or unknown conclusions remain valid findings, not automatic TASK-073 approval.

## Scheduling

**Next development iteration after Devlegate 0.5.7; backlog only until
explicitly scheduled by the curator.** This is the new prerequisite for the
deferred TASK-073 final architectural acceptance gate. It is **not** part of
the 0.5.7 release freeze, TASK-107 release cleanup or a general runtime
refactoring mandate.

## Context

TASK-073 is a parent story and human curator acceptance gate for removing
scattered raw Git operations from ServiceEngine and establishing coherent
transport, typed semantics and domain-workflow ownership. The last substantive
independent assessment was 2026-10-16 and found partially proven semantics,
unreviewed direct runner paths, missing full command traces, an ambiguous
`_git_runtime()` shutdown retry, and incomplete numerical comparison.
Its 2026-10-26 follow-up noted worktree retirement/candidate-screening costs,
but was not a fresh, complete final audit. The product has changed substantially
since then; neither the historical matrix nor line numbers and command counts
may be treated as findings about today's product.

TASK-073 now depends on this independent re-audit. TASK-165 produces **new
evidence**, not a verdict about whether the curator should approve the story.
A complete, honestly negative audit can still satisfy TASK-165; a favorable
report alone never closes TASK-073.

## Goal and audit reference

Independently re-evaluate TASK-073's architectural criteria **1–7** against
the **exact integrated product commit at TASK-165 execution/admission**.
Record that SHA and the relevant historical baseline(s) explicitly, including
the previously audited snapshot where available. Do not conflate a
source-level call-site reduction with a lower number of spawned Git processes,
or interpret historical source line numbers as current code.

Use the complete TASK-073 specification and previous rejected audit as
historical inputs; also account for **all integrated intervening changes**,
including changes to Git semantics, resource binding, recovery/rework handoff,
worktree retirement, reset, publication and runner/shutdown behavior.
Do not assume any named recent ticket fixed or worsened an earlier concern
without inspecting the actual integrated implementation.

## Required analysis

1. **Architecture boundaries.** Map transport runner, typed Git semantics,
   domain Git workflows and ServiceEngine. Inventory all current Git-command
   construction and executions, including delegated helpers, subprocess
   paths and any indirect runners in `runtime.py`, `git.py`,
   `execution_workspace.py`, recovery, reconciliation, binding, cleanup
   and other reachable modules. Distinguish legitimate infrastructure
   command construction from business logic and misleading one-line wrappers.
   Report LOC/module edges as supplementary structural evidence, not proof
   of good decomposition; do not propose generic VCS abstractions.
2. **Error and negative-result semantics.** Inspect/refute the previously
   identified examples and find any current cases where absent refs, false
   ancestry, corrupted/unreachable repositories, malformed output,
   shutdown interruption or OS/Git process errors are conflated.
   Check root commits and fail-closed behavior. Label confirmed defect,
   possible risk requiring reproduction, fixed historical issue and
   unknown separately, with exact current function/path evidence.
3. **Ownership and transactional proof.** Trace direct and nested Git
   effects for owner/thread/shutdown checks, WAL ordering, evidence
   pinning, expected-old CAS, remote lease, pre-effect re-observation,
   control/product/workspace isolation, worker retirement and
   idempotent crash/retry/reconciliation. In particular re-evaluate
   `_git_runtime` / `merge_pending` retry behavior on the current
   source instead of copying the 2026-10-16 finding. Do not weaken
   necessary re-observation to make process counts look lower.
4. **Executed-command/effect ledger.** For representative *full*
   code paths, including local read/history, remote control sync,
   attach/bootstrap, checkpoint/evidence ref publication, accepted
   integration, recovery/reconciliation/rework handoff, reset and
   worktree retirement (steady state and bounded backlog catch-up),
   account for every actually executed Git command, including
   helper-internal and conditional commands. Separate local read,
   remote read and state-changing operations with TASK-073's Tier
   1/2/3 convention (`fetch` counts as local-state mutation).
   Differentiate remote vs local effects. Note any nested N+1 scans.
   Compare against a reproducible historical baseline wherever
   evidence exists; label historical comparisons with missing traces
   **unproven**, not equal or zero. Explain every additional command,
   including local-only reads, and its necessary safety rationale.
5. **Focused independent proof.** Reuse exact-SHA hosted CI (pytest/
   coverage and Ruff); do not run a redundant unrestricted full
   local suite merely for audit. Use narrowly selected tests or
   disposable instrumented real-Git fixtures only where required
   to resolve concrete audit uncertainties. Distinguish observed
   traces from inferred/helper-level command estimates. Preserve
   owner authority and all operating workspaces.

## Required audit artifact

Publish a durable substantive report in the current execution's adjacent
`.md` report artifact (and only focused supporting product documentation
when justified), containing:

- Audited product SHA and historical comparison SHA(s), method and scope.
- Criteria 1–7 matrix: **PASS / FAIL / UNKNOWN**, exact current source
  references, reasoned findings and what evidence would close unknowns.
- A source-call-site/semantic-operation inventory and a **full
  executed-command trace table** by representative workflow and effect
  tier, including counts, conditions, sources, old/new comparison and
  every extra command's explanation.
- A prioritized list of **bounded, independently testable fixes** where
  implementation remains necessary, and explicit residual risks.
- An evidence-to-TASK-073 crosswalk sufficient for human review of
  criteria 1–8 without repeating the entire investigation.

The worker may honestly report `completed` when the **audit itself**
satisfies this specification even if architecture findings are FAIL.
Incomplete trace scope, fabricated baseline parity, absent evidence or
a claimed architecture pass based only on green CI makes the audit
`incomplete`, with actionable remaining work. Independent Reviewer
verifies report coverage and attribution, not presumed architecture
perfection.

## Boundaries

Audit/report only. Do not refactor runtime Git, alter workflows or Git
history, amend TASK-073, change installed services, operate on real
owner resources, silently schedule follow-up tickets, or authorize the
human curator's final decision. Preserve old reports, reviews, current
ticket identity and TASK-073's human-only APPROVE requirement. No
compatibility/migration machinery or invented customer obligations.

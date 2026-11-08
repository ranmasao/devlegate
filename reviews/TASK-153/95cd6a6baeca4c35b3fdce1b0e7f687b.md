<!-- devlegate-review-v1
ticket: TASK-153
execution: 95cd6a6baeca4c35b3fdce1b0e7f687b
checkpoint: b4cde8b9d2994a81d5292b376f7eb1e2775a8408
control: db3f9d53165cccf3aceeafdc4113ec284efb511a
disposition: rework
-->

# TASK-153 — REWORK: fix the TASK-137 regression by subtraction, not feature accretion

## Exact reviewed generation

**REWORK**, not ACCEPT, of execution `95cd6a6baeca4c35b3fdce1b0e7f687b`, checkpoint
`b4cde8b9d2994a81d5292b376f7eb1e2775a8408`; product base
`90de39375177b34923e2c81df8e2e06158323a4e`; admission control
`db3f9d53165cccf3aceeafdc4113ec284efb511a`.
Worker transport and egress are valid, and the exact-checkpoint
[hosted CI](https://github.com/ranmasao/devlegate/actions/runs/42508599514)
passed (1844 passed, 3 skipped, Ruff green). The rejection concerns
**unjustified scope and complexity**, not missing CI.

## Core finding and required mindset

TASK-153 is **first and foremost a regression repair**. TASK-102 had already
implemented suppression of historic failures from the operator's current
`Failed executions` status. TASK-137's subsequent admission/retry unification
reused and tightened that status proof, so unrelated running workers and
product changes could revive TASK-088 as an apparently current failure.

The reviewed fix restores the expected visible behavior, but the cumulative
TASK-153 delta is now **+137/-24 production lines and +158/-13 test/fixture
lines** relative to integrated product HEAD. It introduces an approximately
60-line `_historical_failure_retired()` and retains the dual-output
`_ticket_supersession_proof()`, with expanded marker heuristics, caching
and event-processing mechanics. This is a warning sign for a repair of
previously functioning functionality, not proof that more features are needed.

**Do not continue extending the current solution merely because its tests
pass. Reassess it from the integrated product base and the actual
TASK-102 -> TASK-137 causal diff. Prefer removing the regression-producing
coupling to stacking new safeguards around it.**

## Mandatory next implementation

1. **Perform a source-level causal audit before editing.** Read TASK-102's
   integrated `_failure_superseded()`, TASK-137's change to it and its
   scheduler/admission call sites, and current TASK-153. Identify the exact
   newly introduced conditions that converted a *ticket-specific published
   status transition* into a *product/foreign-worker admission decision*.
   Include a short before/after explanation in the execution report, not just
   a claim that tests now pass.

2. **Build the smallest justified product diff against
   `90de39375177b34923e2c81df8e2e06158323a4e`.**
   Restore TASK-102's independent read-only status semantics, preferably
   using its inexpensive published first-parent workflow-path deletion/event
   observation or a minimally stronger equivalent. Restore the old
   ownership boundary without checking unrelated product dirt, product
   advancement, unrelated active worker identity or unrelated retirement.
   Keep conservative handling of missing/dirty/divergent/unpublished control
   and genuinely unresolved same-generation ownership.

3. **Leave TASK-137 admission/retry protections alone unless a concrete
   correctness reason forces a narrowly explained change.** It must still
   refuse unsafe repeat generation after arbitrary edits, product changes,
   missing Git proof, ambiguous ownership or recovery intents. Suppressing
   a historical row from `status` does **not** authorize a run. Confirm
   `plan`, scheduler admission and explicit retry agree about the CURRENT
   generation even when the status row is suppressed.

4. **Delete or simplify machinery added solely to compensate for the
   coupled design.** In particular, challenge the need for the shared
   two-result `_ticket_supersession_proof()`, its cache, the approximately
   60-line status helper and the generic `may_own_ticket()` guessing
   function. Preserve any one of these only if its necessity is demonstrated
   by a distinct concrete invariant and failing regression test. Avoid
   another `for_status` flag, generic lifecycle engine, new persistent
   state, migration, or broad rewrite. Reuse existing Git observation helpers
   where that actually reduces code.

5. **Keep a lean, risk-based test suite.** The decisive regression is
   TASK-088's published transition followed by unrelated TASK-151 worker
   activity and/or product change: human and JSON status must suppress
   the historical row while reports remain intact, and no unsafe admission
   is authorized. Cover same-generation live/uncertain owner, genuinely new
   failure, dirty/stale control, and same-ticket deletion/recreation or
   changed blob where relevant. Reuse existing TASK-102/TASK-137 tests;
   consolidate repeated assertions and state combinations rather than
   proliferating lengthy variants. If a performance regression guard is
   necessary, count Git calls with **a small number** of unrelated commits;
   hundreds of empty-tree commits are not required to distinguish a
   per-commit `ls-tree` algorithm. Do not remove unique safety cases for
   a cosmetic line target.

## Evidence required for acceptance

Report, with exact code references:

- The **root cause** in TASK-137 relative to TASK-102, and which checks
  are removed, restored or deliberately left strictly admission-only.
- A direct **diff-size comparison against integrated product base**, split
  into production and tests (additions/deletions/net), plus a concise
  explanation for every significant *new* helper or block. The goal is
  substantive simplification, **not an arbitrary maximum line count**.
  If the implementation is still materially larger than necessary for
  the regression, explain the invariant requiring each expansion.
- The minimal tests proving original status behavior, non-regression of
  TASK-137 authorization, and no false suppression under uncertain
  matching ownership or stale control.
- Passing focused tests, the full configured validation, Ruff and exact
  hosted-checkpoint CI before acceptance.

## Review disposition

Reject the current checkpoint for **scope discipline/DRY**, despite its green
CI and positive functional regression tests. Preserve its commits and
historical evidence, and return the **byte-identical** canonical TASK-153
from `kanban/review/TASK-153.md` to `kanban/todo/TASK-153.md`, with
this immutable review in the same control commit. No product merge, no
manual SQLite edits, no new TASK-154, and no touching real operator assets.
The next worker should prioritize a **smaller corrective patch** over a
third layer of complexity.

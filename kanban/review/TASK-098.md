---
"type": "devlegate.ticket"
"title": "Replace brittle exact Git-command trace assertions with behavioral recovery invariants"
---

## Context

During TASK-088 testing, `tests/test_control_workspace.py::test_prior_generation_is_repaired_with_evidence_and_same_execution_id` failed after an additional locale-independent exact-ref observation (`show-ref --verify --quiet`) was introduced. The safety-relevant checks of evidence publication, reproof before branch mutation, and the ordered mutating effects passed, but three further assertions independently froze the implementation trace: total Git calls (`54`), non-effect calls (`49`), and the complete ordered list of every `_git` invocation. Removing only one count exposed the next equivalent assertion. This is test/implementation coupling, not evidence of a recovery regression.

## Required work

1. Audit the complete test suite, including helper fixtures and parametrized expectations, for brittle assertions on exact subprocess/Git call counts, full read-only command traces, incidental ordering, mock invocation totals, and similarly fragile implementation details. Classify findings; do not assume every exact count or ordered sequence is wrong.
2. Replace unjustified trace snapshots with assertions expressing **observable contracts**: persisted state, revision binding, evidence preservation, expected outcomes, retry/crash idempotency, and absence of destructive or unauthorized changes. Test the allowed mutating operations, their exact arguments and safety-critical order where necessary; explicitly reject unexpected writes, ref updates, force operations or other unsafe effects.
3. Preserve and strengthen necessary observation-before-mutation guarantees (for example, exact-ref checks, remote lease validation and reproof). Do not weaken security or concurrency coverage to make tests pass. A read-only probe may be semantically required, but unrelated additions or reorderings of harmless probes should not invalidate a scenario.
4. For `test_prior_generation_is_repaired_with_evidence_and_same_execution_id`, remove redundant `54`/`49`/full-trace coupling and retain the existing evidence-before-branch and exact mutating-effect assertions; add any missing checks required to establish no forbidden mutations. Include focused regression proof that adding an extra safe Git observation does not cause a false negative, while omitting required evidence/reproof or introducing an unauthorized mutation still fails.
5. Document the test design rationale **in ordinary maintainer-facing language**, preferably adjacent to representative recovery tests and in the repository's existing test/development documentation. Explain that a test should describe externally meaningful invariants and safety boundaries, because incidental implementation traces evolve as validation improves. Keep commentary contextual, concise, and explanatory rather than phrased as agent-specific directives or blanket prohibitions.
6. Run the affected tests and full CI; record which brittle assertions were found, which were retained (with rationale), and which were rewritten. Keep tests deterministic across environments and Git versions without hiding real regressions.

## Acceptance

- No known unjustified exact-count or full-read-only-trace assertions remain after the suite audit.
- Recovery tests still fail for missing evidence, incorrect sequencing of safety-critical operations and unexpected mutations, but tolerate additional safe read-only observations.
- Commentary is useful to future human maintainers and automated contributors without being an instruction sheet aimed at agents.
- TASK-088's current count/trace failure is covered; this ticket does not replace TASK-088's functional repair or TASK-097's crash-replay fix.

Priority: **normal**. Prefer implementation after restoring the self-hosted service; do not block urgent TASK-088 recovery solely on broad test refactoring.

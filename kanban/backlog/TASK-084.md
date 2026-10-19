---
"type": "devlegate.ticket"
"title": "Define and harden semantic Git operation contracts"
---

## Context
The prior decomposition of the ~10k-line runtime module moved operations into `src/devlegate/git.py`, but many helper functions still expose raw `subprocess.CompletedProcess` and callers in `runtime.py` / `execution_workspace.py` interpret `returncode` on their own. TASK-083 demonstrated inverted `merge-base --is-ancestor` checks and incorrect `git cherry` argument semantics. Existing Git observation/evidence race hardening (including TASK-079) must not regress.

## Goal
Make Git operation *semantics* owned by a clear Git API, not scattered across orchestrator callers. Prioritize correctness, typed outcomes, preservation of provenance and reviewability over indiscriminate line-count reduction.

## Required work
1. Inventory direct Git subprocess/runner uses and `returncode`, stderr, output and ref-absence classification in `git.py`, `runtime.py`, execution workspace, reconciliation, recovery and related modules. Distinguish intentional low-level escape hatches from leaked interpretation. Produce a traceable audit matrix with callers, Git commands, expected results, ambiguity and risk.
2. Define explicit contracts for observation, ancestry, patch-equivalence, ref existence, mutation CAS/lease and remote publication. Separate *absent* from observation failure; distinguish normal false/negative from Git execution error, malformed output, ambiguous remote mutation outcome and proven conflict.
3. Apply **small, bounded** changes to high-risk call sites using semantic typed helpers/results; avoid a sweeping Git rewrite or altering transaction authority. Preserve injection/testability, explicit expected remote predecessors and exact identities.
4. Document which Git effects can be safely replayed and which require read-only re-observation after process/network failure. Do not map every nonzero `push` to proven rejection.
5. Add targeted `git.py` tests and real-Git integration tests for return codes 0/1/>=2, malformed output, ref absence, CAS races, unknown remote outcome and consumers' fail-closed behavior.

## Boundaries and acceptance
- Do not combine with TASK-083, live revision advancement in TASK-076, or a general scheduler redesign.
- Prefer incremental implementation milestones; if the inventory establishes excessive scope, deliver an auditable decomposition into narrower follow-up tickets **before** broad edits.
- Preserve backward-compatible supported behavior, execution provenance, service lock and restart semantics.
- Focused local tests/Ruff; exact-checkpoint CI as required by the project. Report the semantic API matrix and changed callers.
- Backlog only: no automatic admission before review of priority and safe installed-runtime upgrade.

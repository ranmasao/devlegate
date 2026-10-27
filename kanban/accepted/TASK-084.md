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

## Additional semantic-boundary evidence from static dependency analysis

At the analyzed 0.5.7 snapshot, `runtime.py` imported **22 internal modules**, including `git`, `control_git_workflow`, `execution_workspace`, `recovery_transactions`, `recovery_workspace`, `reconciliation_transactions`, `review_provenance` and `runtime_store`. `git.py` was ~995 LOC; `execution_workspace.py` ~828; `reconciliation_transactions.py` ~568. These are **static dependency/size observations, not evidence that a particular command call is wrong**.

During the required call-site inventory, distinguish (a) Git process/transport policy and OS failures, (b) typed observations such as absence vs valid negative ancestry vs malformed/unreadable repository, (c) domain-specific evidence, workspace and reconciliation transactions, and (d) the engine's owner/admission/WAL decisions. Preserve domain-specific operations where they are cohesive; avoid replacing them with a generic all-purpose `GitService` or cosmetically forwarding every command through `runtime.py`.

Changing `from devlegate.git import ...` to `from devlegate import git` is **not a semantic boundary** and can alter monkeypatch targets; do not include a global import-style migration. A direct-import graph does not capture executed subprocesses, nested helper effects, dynamic dispatch or runtime mutation: the audit must retain its per-call error, effect, ownership, replay and command-trace proof. Correlate findings with TASK-073's Git story acceptance rather than inventing independent architectural success from fewer imports or fewer source lines.

## Curator scope and sequencing clarification — 2026-10-26

This ticket is the **first bounded implementation/audit step** before scheduling TASK-080 and TASK-081. Establish the canonical semantic Git API inventory using what already exists in `git.py`, `control_git_workflow.py`, `execution_workspace.py`, `reconciliation.py`, transaction/recovery modules and runtime. For each existing operation, classify valid false/absence versus failure, local/remote and read/write effects, owner/shutdown authority, and replay safety. **Reuse working helpers first**; introduce a new typed wrapper only for a demonstrated ambiguous or high-risk consumer, not for API uniformity. A compact documented matrix is required; if broad remediation is needed, produce focused follow-up tasks rather than a cross-module rewrite.

Include TASK-099/100 as integrated safety baseline. In particular, `_prove_published_workspace_head()` currently fetches the product ref, and a full cleanup inventory can re-run candidate proof for many worktrees. Inventory its actual nested Git/remote calls and indicate the N+1/repeated-fetch effect; **measure and report** without changing cleanup scheduler or deleting TOCTOU checks under this ticket. TASK-073 must later evaluate complete measured traces. Avoid modifying any active/in-flight ticket.

---
"type": "devlegate.ticket"
"title": "Eliminate Git index.lock race in real-service auto-resume topology test"
---

## Milestone and scheduling

Test reliability hardening; backlog. Independent of TASK-179 and TASK-180, and not a blocker for those presentation-only changes.

## Incident

Hosted GitHub Actions [run 44172781916, attempt 1](https://github.com/ranmasao/devlegate/actions/runs/44172781916) on TASK-178 checkpoint `bec6024ff0fa1e5080f061519e7f7bfd9e62b883` failed with **2332 passed, 1 failed, 3 skipped**. Sole failure:

```text
tests/test_cli_topology.py::test_real_service_auto_resume_survives_review_barrier_and_reschedules
Git failed: git commit -m "return T-1 to todo"
fatal: Unable to create '.../.git/worktrees/control/index.lock': File exists.
```

The test runs a real Devlegate service, polls `status --json` until scheduler action is `none` at the review barrier, then directly writes and commits a rework transition into the **same live control worktree**. Observed scheduler `none` is not exclusive Git index ownership. There may be concurrent service Git activity or a stale lock; the log does not prove which. On **attempt 2 the same checkpoint passed full hosted tests and lint** without any code changes. TASK-178 was therefore accepted and integrated separately; this is a latent topology-test stability issue, not a new inspect-ticket behavior defect.

## Goal

Remove the race from this real-process topology test without weakening its proof of worker process loss, autonomous RESUME, review barrier, externally published REWORK and the expected subsequent single worker execution.

## Requirements

1. Independently examine the exact test sequence, concurrent service Git operations and lock ownership. Obtain reproducible ordering or instrumentation evidence to distinguish active lock contention from stale lock files.
2. Establish a deterministic test-side Git publication/coordination boundary, possibly using an independently owned publisher checkout or an existing proven quiescent state. Preserve real control-plane provenance semantics, ownership and repeatability. Do **not** use arbitrary sleeps, blindly delete `index.lock`, or add a global Git retry layer to hide the race.
3. Retain the existing real-service end-to-end assertions and topology. Do not skip/xfail, replace with fake owner, or alter the production sole-owner/Git safety model unless a separate proven product defect is found.
4. Use narrow reproductions/fault-injection where possible, repeated focused real-service test runs at relevant concurrency, `./dev lint`, `git diff --check`, and exact-checkpoint hosted CI. Avoid unrestricted full local pytest during ordinary implementation.
5. Document the failure mode, demonstrated deterministic fix, and evidence. If root cause belongs to production runtime rather than the test, report it separately and avoid unapproved architectural expansion.

## Acceptance

The test's external Git rework publication no longer collides with service-owned Git index operations under the tested scheduling boundaries; successful repeated runs have a justified synchronization proof rather than just random green outcomes. Fail-closed mutation and the original scenario assertions remain intact, and hosted CI passes.

## Boundaries

No runtime artifact migration, no change to ticket semantics, no CI blanket retries, no deletion of locks, no weakening of review/execution provenance. Historical GitHub Actions evidence and TASK-178 execution report stay immutable.

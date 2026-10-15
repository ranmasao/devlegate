---
"type": "devlegate.ticket"
"title": "Consolidate repeated Git history and reference primitives"
"depends_on": ["TASK-068"]
---

## Disposition: ACCEPTED

Execution `13edb1c37bcf4ea184c53d19544b8fff`, approved checkpoint `80e690c2fb9360708b4638c95b99b7b1ff236c35`.

[Exact-checkpoint CI #38651617445](https://github.com/ranmasao/devlegate/actions/runs/38651617445): **SUCCESS** — 1206 tests passed, 1 skipped; Ruff clean.

## Delivered

- Added shared `git.commit_parents()` for commit-parent enumeration with explicit failure behavior.
- Reused `git.resolve_commit()` in runtime recovery and workspace paths while retaining the existing test runner seams.
- Consolidated repeated low-level Git command parsing without conflating different `update-ref` CAS and `merge-base` policy contexts.
- Added minimal unit coverage for the extracted parent primitive.
- Reported raw command-count change: `rev-parse` 78 → 70, `rev-list` 8 → 7. Numbers are textual call-site counts, not runtime measurements.
- Worker reported four local environment-dependent test failures; the exact checkpoint passed the authoritative complete GitHub CI.

No further TASK-069 work required. Proceed with normal Devlegate accepted integration; additional Git consolidation, if ever needed, should be separately justified.

---
"type": "devlegate.ticket"
"title": "Finish substantive consolidation of repeated Git operations in runtime"
"depends_on": ["TASK-069"]
---

## Disposition: ACCEPTED — scoped ancestry/read consolidation

Latest execution `eb4e85b44b994f1f8602575ee7ebcb8c`; checkpoint `a3b39eed7e114263a443b018e2ad621af648cbc7`. [Exact CI #38686361874](https://github.com/ranmasao/devlegate/actions/runs/38686361874): SUCCESS, 1207 passed, 1 skipped, Ruff clean.

Implemented reusable strict `git.is_ancestor()` (exit 0 yes, 1 no, other nonzero error) and further `resolve_commit()` reuse. Fixed regressions by mapping `GitAncestryError` to appropriate fail-closed workflow errors at admission, retry, recovery, reconciliation and publication boundaries.

Source audit, runtime.py versus TASK-069 checkpoint: 208 -> 164 direct `_git()` / `self._git_runtime()` call sites (a 21% reduction); `merge-base` 27 -> 1, `rev-parse` approximately 71 -> 52. Runtime LOC 9181 -> 9101. This is a substantial *first* consolidation, not completion of layered Git extraction.

Further read-only Git primitives, typed failure semantics and remaining raw Git commands in `ServiceEngine` are explicitly tracked in TASK-071 and TASK-072. Do not claim TASK-070 eliminated the Git dependency or completed the wider refactor.

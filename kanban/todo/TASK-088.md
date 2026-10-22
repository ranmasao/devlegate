---
"type": "devlegate.ticket"
"title": "Fix first-time control init failure with absent control refs"
---

## Incident (Arch package dogfooding, 2026-10-19)

Fresh Arch Linux x86_64 host, installed packaged Devlegate 0.5.7.dev0-1, Git 2.56.0, local product master and local bare origin; product and remote at 0c541b1, clean checkout. `devlegate init smoke` succeeded, generated setup committed and pushed; `devlegate control init` failed before creating any control worktree:

```
devlegate: cannot resolve Git object 'refs/heads/devlegate/control': ФАТАЛНА ГРЕШКА: Изисква точно една версия
```

Observed Git behavior:
- `git ls-remote --heads origin refs/heads/devlegate/control`: exit 0, empty stdout (remote ref absent).
- `git show-ref --verify refs/heads/devlegate/control`: exit 128, ref absent.
- `git show-ref --verify --quiet ...`: exit 1, ref absent.
- `git show-ref --exists ...`: exit 2, ref absent.
- `git rev-parse --verify ...`: exit 128, matching the localized error detail.
- `git worktree list --porcelain`: only product master; no partial control worktree observed.

The exact internal failing call has **not** been traced. Do not assume the `show-ref` invocation alone is responsible. The installed package revision may differ from currently browsed source. Bootstrap is supposed to create a new orphan control worktree and publish control when both refs are missing.

## Required work

1. Reproduce on an isolated fresh repository and bare remote, across representative Git installations, using the packaged or exact corresponding release implementation. Locate precise failure, including Git wrapper resolution and ref-observation paths; report the failing command/call stack.
2. Treat ref `present`, `absent`, and `observation error` distinctly. Absence is an expected bootstrap condition, not a valid revision to resolve. Avoid localized stderr matching. Do not depend on any single installed Git version or parse localized stderr. Define semantic present/absent/error observation via documented portable Git behavior or verified capabilities; fail clearly when a required capability is unavailable. Test representative Git versions without pinning production to the incident version.
3. Fix the smallest correct boundary so `control init` creates/publishes a new control branch and attaches its worktree while preserving safety on errors and no ambiguous remote ref overwrites. Do not expand into broad TASK-084 Git API refactoring.
4. Add real-Git deterministic integration coverage across representative supported Git capabilities/versions: first bootstrap, repeat init idempotency, attach existing remote control, local-only ref disagreement, transport/permission failures, and interrupted/partially created bootstrap with safe diagnostics/retry. Check no accidental product branch mutation.
5. Document the Git capability support policy, without requiring a particular Git version solely to fix this incident. Verify exact checkpoint CI and Ruff; separately repeat the **packaged** Arch smoke test with a newly built artifact before closing.

Priority: **high; blocks first-use on Arch**. Backlog only; do not preempt active TASK-076.

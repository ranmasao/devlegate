---
"type": "devlegate.ticket"
"title": "Expose host commands in top-level CLI help and align start documentation"
---

## Dogfooding observation (Arch package 0.5.7.dev0, 2026-10-19)

`devlegate --help` lists Service, Project, Recovery and Other commands but omits `host`. However `devlegate host --help`, `host install --help` and `host uninstall --help` work; host policy can be installed, checked, removed and reinstalled successfully. Users cannot discover this lifecycle via top-level help.

Top-level help explicitly says `start` starts the persistent background service and bare `devlegate` shows help. Some project docs previously described bare invocation as start/automatic supervision. This is a **documentation drift hypothesis** requiring verification against currently deployed version before broad changes.

## Required work

1. Add `host` with accurate purpose to `devlegate --help` in a suitable section, keeping CLI parser/help and documented command vocabulary in sync. Avoid inaccurate implication that package install automatically provisions host policy or project units.
2. Audit current README, Operations and Architecture documentation for `start` vs bare-command semantics and host install separation; correct stale descriptions against the actual CLI contract, not by changing working runtime startup behavior gratuitously.
3. Add lightweight CLI snapshot/behavior coverage that `host` is discoverable and all shown commands parse; exercise `host --help`, `project --help`, and initial invocation.
4. Keep this documentation/discoverability fix separate from TASK-087 role-skill audit.

Priority: low; no functional blocker. Backlog only.

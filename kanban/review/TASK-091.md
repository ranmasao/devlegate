---
"type": "devlegate.ticket"
"title": "Recover accepted integration after safe control descendant advancement"
"depends_on": ["TASK-076"]
---

## Confirmed incident: rslab2 LAB-185 (2026-10-20)

Installed Devlegate 0.5.7.dev0 successfully merged and published an accepted LAB-185 worker checkpoint into product `origin/master`, but after restart it repeatedly blocked the control `accepted -> done` transition:

```
workflow blocked: control HEAD changed during accepted integration
```

Durable state (`accepted_integration`) from read-only SQLite inspection:
- `ticket_id`: `LAB-185`
- historical `control_head`: `06789d89fdf470a9d57d11258a147d5b4897bf80`
- `checkpoint`: `0687826c712464e4e35f6f79fc2fe8cc8b2ad558`
- `product_transition.status`: `published`
- `product_transition.admitted_base`: `c5600043629a2b72f727f1d3ee0c08f20d91e25c`
- `product_transition.predecessor`: `d8be2c5898b4973477c7305f1f7549b300011783`
- `product_transition.integration`: `9cba7038105f1fe1498a12e7a9ae030c6e9704d2`

Read-only Git observations establish:
- Current clean, local == remote control `bd818a5aea71e02aeeb33f136099269c6747a606`, proven descendant of saved control `06789d...` (`merge-base --is-ancestor` exit 0).
- `git diff --name-status 06789d..bd818a5 -- kanban/accepted/LAB-185.md executions/LAB-185` is empty. Since saved control, only unrelated `kanban/backlog/DRAFT-007.md` and `kanban/todo/LAB-186.md` changed.
- LAB-185 is still in `kanban/accepted/`; published execution evidence remains present.
- Product remote `refs/heads/master` already equals `9cba7038105f1fe1498a12e7a9ae030c6e9704d2`; exact parents `(d8be2c5898b4973477c7305f1f7549b300011783, 0687826c712464e4e35f6f79fc2fe8cc8b2ad558)`. **Do not repeat the product merge or worker.**

Source of failure: `_complete_accepted` requires literal current control HEAD == stored `accepted_integration.control_head`, even when unrelated proven descendant commits were appended. This is distinct from the product-descendant integration already covered by TASK-076.

## Required change

1. Allow **service-owned** accepted integration recovery when current control HEAD is a proven descendant of historical expected HEAD, but only after validating the immutable accepted ticket identity/content, execution evidence, checkpoint, dependency/admission-relevant invariants and current control state. The observed empty targeted diff is evidence for this case, **not** a universal sufficient safety rule. Reject ticket edits, changes of ticket lane, invalid ancestry, ambiguous provenance and eligibility changes.
2. Preserve historical `accepted_integration.control_head` / execution admission identity as immutable provenance. Introduce an explicitly validated, durable *current control predecessor* for the `accepted -> done` commit, rather than blindly replacing historical SHA.
3. Publish with exact compare-and-swap / appropriate lease against the actual observed remote control predecessor. Handle races, nonzero/ambiguous push results and restarts by re-observing remote, refusing to overwrite foreign changes. Replay existing local exact transition commits without duplicating or misidentifying another owner's commit.
4. Support replay from **legacy already-persisted** accepted integration with `product_transition.status=published`, including the case where control advanced while the service was stopped. Complete the control move to `done` exactly once, retain the verified product merge and checkpoint, do not rerun worker or re-merge product.
5. Provide deterministic real-Git and restart regressions: LAB-185-shaped benign descendant, changed accepted ticket text/evidence, dependency/eligibility changes, unrelated/diverged control, local/remote control race, interrupted control transition before/after commit and push, ambiguous publication. Assert no loss of other control edits (e.g. LAB-186/DRAFT-007), no duplicated done transition, preserved provenance.
6. Confirm full checkpoint pytest/Ruff and document state/effect ordering; test fresh current state and migration/replay of old state. Keep scope bounded to accepted-integration control progression; no broad TASK-084–086 Git API/recovery redesign.

## Operational safety

The real rslab2 SQLite state, product merge `9cba703...`, accepted ticket and work refs must remain untouched until a reviewed, packaged update can recover them. Do not instruct the executor to manipulate the user's production SQLite or force Git refs. Ticket is ready for ordinary todo admission.

Priority: **high**, blocks LAB-186 and the rslab2 queue.

## Fifth-pass targeted correction (2026-10-20)

The fourth checkpoint `6d89d432694ba0a65357d9b17a149098ba876c38` passed GitHub Actions `check`: https://github.com/ranmasao/devlegate/actions/runs/39390901529 . **Do not repeat full-suite runs solely because the worker's local ten-minute command timed out, and do not fix unrelated PEX/Ruff issues.** Preserve implemented predecessor recovery, zero-delta route, dependency identity validation and existing negative regressions.

This pass is **limited to closing provable accepted-control-recovery test gaps**:

1. First inventory existing tests by exact name and assertion for each requested case. Only add demonstrably missing deterministic regressions; report covered vs missing without duplicating existing tests.
2. Fix `test_real_service_rejects_changed_accepted_integration_descendant`: after restart, wait for **specific accepted-integration rejection evidence**, not just any nonempty status JSON; assert no done transition or remote rewrite and checkpoint retained. Cover changed accepted ticket, execution evidence and completed dependency (already parameterized).
3. Strengthen LAB-185-style successful legacy-published recovery test: immutable saved `control_head`, durable adopted `control_predecessor`, exact done-commit parent / remote control, unchanged published product merge, no duplicate worker or lost unrelated control edits.
4. Establish or add targeted cases for local/remote mismatch or divergent control; competitor changing remote before lease push; ambiguous successful push; restart after saving predecessor, after local done commit and after push. Include accepted **zero-delta** descendant recovery. Where existing tests suffice, cite their names and checks instead of adding duplicates.
5. Provide concise test matrix and exact passing targeted-test commands/results. Existing successful checkpoint CI is accepted as full-suite evidence; rerun CI for the *new* checkpoint as usual. If one safety boundary cannot be tested, disclose the gap explicitly and mark incomplete instead of declaring success.

Do not make broad architecture changes, touch live rslab2 state, or repeat product merge. Source details: `reviews/TASK-091/2f4b248e97fe4d618b575dcef1e2ebba.md`.

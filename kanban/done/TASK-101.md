---
"type": "devlegate.ticket"
"title": "Eliminate locale-dependent Git diagnostic parsing and harden tests"
"depends_on": ["TASK-084"]
---

## Milestone

Git semantic contract hardening after TASK-084; before TASK-080 / TASK-081 where practical.

## Goal

Make Git absence, negative observations and failures independent of the language of human-readable Git diagnostics. Audit and **rewrite affected tests** so behavior is proven under localized and arbitrary stderr, rather than only matching English Git error strings. Preserve fail-closed behavior, execution identity and Git transaction safety.

## Context

The TASK-084 semantic API audit improved typed ancestry and patch-equivalence but revealed remaining classification based on English `stderr` in `src/devlegate/git.py`:

- `_is_missing_revision_error`, `_is_missing_object_error`, `_is_missing_path_error`;
- `resolve_commit`, `resolve_object`, `commit_parents`, `read_object`, `path_object`, `read_path`;
- `remote_exists` checks `"no such remote"`.

These assumptions can fail on hosts using Bulgarian or other non-English locales, often blocking recovery incorrectly. Search all Git runner consumers in production, CLI/daemon code and tests for additional dependency on exact/fuzzy diagnostic text; this list is not exhaustive. Do not confuse Git's **machine-readable structured stdout** or explicitly stable porcelain protocols with translated human-facing output.

## Required behavior

1. Inventory every Git `stderr`/human diagnostic substring, regex, equality or string-based exception classification in production modules and relevant tests. Record function, command, expected semantics, currently assumed language, available locale-independent observation and risk. Preserve `stderr` in operator-facing errors without parsing it for branch/ref/path absence.
2. Replace diagnostic-text classification with precise semantic probes using suitable Git exit status, `--quiet`, `show-ref --verify`, `cat-file -e`, appropriately constrained refs/revisions and structured output. Distinguish *proven absent*, *valid negative*, *unreadable/corrupt repo*, *malformed output*, and *ambiguous mutation outcome*. For `rev-parse` status 128, **do not treat an arbitrary failure as absence**. Where no reliable probe can distinguish outcomes, fail closed with an actionable observation error.
3. Audit Git environment control: ensure every supported Git runner path uses a deliberate and documented locale strategy (e.g. `LC_ALL=C` in the subprocess environment) for reproducible diagnostics; this is defense in depth, **not** justification to retain English message parsing. Preserve caller environment where appropriate for unrelated child processes. Avoid altering a shared global environment. Include direct `run_git`/special-purpose subprocesses and test seams; document exceptions.
4. Keep semantic helpers cohesive, avoid gratuitous extra Git subprocesses or network requests and preserve injection points, owner/shutdown authority, evidence-before-mutation, exact CAS/remote lease and crash replay. Record before/after actual command traces for representative affected paths, disclosing necessary extra probes and their effect tier. No cleanup scheduler or broad Git refactor.
5. **Mandatory test redesign, not merely additional tests:** audit existing fixtures, assertions and monkeypatched Git runners for dependency on English diagnostics (`fatal:`, `unknown revision`, `bad object`, `no such remote`, `does not exist`, etc.). Replace test oracles that validate Git semantics by substring-matching diagnostics with assertions on semantic outcomes and error categories. Localized/artificial diagnostic text may remain solely in tests of forwarding or human-facing messages, clearly separated from semantic classification.
6. Add parametrized consumer-level negative cases with at least English, Bulgarian and opaque/unrelated diagnostics, including exit statuses that previously signaled false absence. Demonstrate **the same outcome for the same Git state/status regardless of locale**, including present-but-corrupt, missing ref/object/path, invalid revision, malformed result, unreadable repository and missing remote. Use real temporary Git repositories plus injected runners as appropriate; do not claim a localized host is needed for deterministic tests.
7. Add a locale-matrix integration smoke test, using explicitly controlled Git subprocess locale (`C` and available alternate locale) without assuming `bg_BG.UTF-8` is installed in CI. Simulated Bulgarian diagnostics must test message independence, not imply that Git supports Bulgarian translation on every platform. Document host prerequisites for running true Bulgarian-locale validation; do not skip the deterministic locale-independent tests if an optional locale is absent.
8. Run focused Git/workspace/recovery tests and Ruff; require full exact-checkpoint GitHub Actions pytest/coverage and Ruff to pass. Report complete inventory, cases rewritten, failures distinguished and subprocess delta.

## Required regressions

- Missing exact ref versus unreadable repository, each with English/Bulgarian/arbitrary `stderr`: only proven absence returns `None`/false.
- Existing invalid revision, object or path: negative observation never inferred solely from status 128 plus English wording; no false absence on corruption.
- Missing remote versus remote configuration failure: classification does not rely on `"no such remote"`.
- Rework/recovery under localized Git errors: cannot silently treat failed ancestry or object resolution as a safe conflict/absence permitting mutation.
- Real Git absent/present/corrupt object/ref/path and root/merge cases retain expected semantics.
- Any new semantic probe does not silently fetch or change Git state; every additional local read is accounted for.
- Tests that used to pass only because a fake runner returned an English phrase now pass with Bulgarian and opaque diagnostics for the same observed state.

## Acceptance criteria

- No production decision about Git state depends on a human-facing Git diagnostic substring, regex or exact text. Structured documented machine-readable output is allowed; raw stderr may appear in human diagnostics.
- Core ref/object/path/remote helpers distinguish proven absence, normal negative and observation failure consistently across diagnostic languages.
- Existing English-message-dependent tests have been **rewritten**, not merely supplemented; parametrized, real-Git and optional locale smoke coverage support the invariant.
- Owner authorization, recovery and publication safety remain unchanged and fail closed; no hidden network/remote mutation is added.
- Full exact-checkpoint CI and focused tests green, with an auditable call-site and test-migration report.

---
"type": "devlegate.ticket"
"title": "Correct native service identification during package updates and Arch installation provenance"
---

## Milestone

Devlegate 0.5.8 — correct the native update workflow during self-hosted dogfooding.

## Goal

Make the already implemented `./dev package native-install` work for an ordinary running native-packaged service, without weakening the existing stop/install/restart safety contract. Also correct the small, independent Arch installation classification defect in `devlegate version`.

## Context

This is a fresh implementation cycle of TASK-204 following its prior integration at product bfa9cfebb2baeb35bb5ef50aa097d9e527f5e3df. The previous specification and immutable execution/review evidence remain in control history. This is the first real curator reopening after TASK-205; do not implement or extend reopening itself.

### Primary defect — false native service identification refusal

Observed on self-hosted Arch, 2026-11-26: `./dev package native-install` refused with `@devlegate: cannot verify native service and hosting for PID 899052`. A manual Arch build, same-version pacman reinstall and graceful service restart succeeded, but the helper repeated the refusal for PID 979005.

Actual Linux process layout (user-specific paths generalized):
- /proc/PID/exe and argv[0]: cached bundled CPython 3.12.16 under the scie/nce cache.
- argv[1]: cached unzipped PEX directory, not /usr/bin/devlegate.
- remaining argv: `--env <project-env> foreground`.
- filtered initial /proc/PID/environ: DEVLEGATE_HOST_MODE=external, DEVLEGATE_REQUIRE_NOTIFY=1, SCIE=/usr/bin/devlegate; no PEX entry.
- registered project has a valid managed systemd unit and healthy IPC.

In tools/native_install.py, process_host requires the outer executable in argv[0], Python with the outer executable in argv[1], or PEX and SCIE both equal to the outer executable. None matches this real packaged process. The initial /proc environment is not Python's live os.environ. The previous service regression used a shell shim and injected PEX/SCIE; it did not prove this actual launch layout.

### Small companion defect — incomplete version identity

After the successful pacman reinstall, /usr/bin/devlegate version reported Distribution: standalone, Installation: unknown, CPython 3.12.16 on Arch. src/devlegate/runtime_identity.py identity() only recognizes Debian provenance although the Arch builder emits its own payload-bound native marker. This is independent of native-install's actual package-manager ownership preflight; it is not evidence that pacman installation failed.

Both observations were transferred from TASK-157 (former UX-006 and UX-007) because they are functional defects, not presentation issues.

## Required behavior

- For a registered active systemd service, use existing sufficient service/installation identity: verified managed unit and loaded native launcher, matching MainPID and IPC instance/runtime authority, process identity and correct owner. Do not additionally require a particular PEX/SCIE internal argv or environment layout. Preserve active-unit/IPC disagreement, foreign/unverified unit, uncertain owner, changed PID/instance and unsafe stop refusals. Do not infer a running service merely from registration or unit existence. Construct the existing stop/restoration selection with the actual supported hosting mode, not guessed launch internals.
- Handle the observed SCIE-only cached-interpreter/PEX layout appropriately for registered detached and foreground/once services and best-effort exceptional host discovery. Reuse existing IPC, hosting and runtime-lock evidence as applicable. A SCIE variable alone is not proof of ownership or an authorization to stop an arbitrary process. Continue distinguishing service hosts from workers, CLI clients and unrelated installations; report inaccessible metadata honestly. Other-user/unregistered instances remain warning/consent-only; attached/once hosts are not automatically restored.
- Preserve the previously integrated package-manager ownership, exact validated artifact, concurrent graceful drain/build, proven shutdown before installation, changed-owner recheck, failure restoration and exact persistent restart-set contracts. No new supervisor, identity protocol, receipts, global lock or persistent transaction framework. Remove the redundant assumption rather than layering an additional proof system over it.
- Recognize the existing Arch native installation marker in runtime_identity.py using valid package/distribution identity and the matching actual executable SHA-256, consistently with Debian. Preserve Distribution: standalone for native artifacts and existing Python package identity. Return Installation: arch for genuine matching Arch provenance in both human and structured output; retain unknown for standalone without valid matching native provenance, malformed, stale or mismatching markers. No OS-family inference, package manager subprocess requirement for version, or migration.
- Keep this correction bounded to the two defects. Do not modify TASK-157, TASK-191, TASK-205 or unrelated reconciliation machinery in this implementation. No live-host package install, service mutation, control publication or Git integration by the worker.

## Acceptance criteria

The observed normal packaged systemd service is admitted by native-install and follows the existing safe update workflow; a genuine uncertain or foreign owner is still refused. The real cached CPython/PEX process layout without initial PEX does not produce a false refusal. Genuine Arch installation reports arch with matching provenance; Debian and Python-package behavior remain unchanged. Existing package/update safety guarantees and historical TASK-204 evidence remain intact. Explain the minimal actual changes and any remaining test limitations.

## Required regressions

1. Reproduce the observed process layout with realistic disposable process/IPC/unit fixtures: cached interpreter argv[0], cached PEX directory argv[1], explicit project selector and foreground invocation, SCIE-only initial environment, verified loaded systemd native launcher and matching MainPID/IPC/owner. Assert the registered service is selected for graceful stop and persistent restoration, not refused by bootstrap representation. Include a wrong PID, wrong user, foreign/changed unit or uncertain IPC authority case; retain no unsafe stop/install effects. Exercise the existing drain/build/install/restart regression with this layout rather than duplicating the whole update matrix.
2. Exercise appropriate SCIE-only detached, attached/once and exceptional-discovery paths; prove no arbitrary SCIE-only CLI/worker/unrelated process gains stop authority. Preserve incomplete-observation warnings, consent for exceptions and no automatic attached restart. Reuse existing fixtures for unchanged failure and restoration guarantees.
3. Test real generated native artifact launch behavior in a disposable non-host-mutating setting where practical; do not claim a shim or injected PEX environment proves the real scie launch. If unavailable, explicitly report why and the realistic regression used.
4. Test matching Arch and Debian provenance using existing marker layout, actual executable digest, human version banner and structured identity; reject unknown distribution, wrong package, malformed/missing/stale/mismatching digest or marker; preserve standalone and Python package cases. Reuse existing packaging and runtime identity tests.
5. Focused tests, lint, diff check and exact-checkpoint hosted CI. Do not install on the real host.

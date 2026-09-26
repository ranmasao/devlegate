---
"type": "devlegate.ticket"
"title": "Report external service hosting mode correctly"
---

## Milestone

Devlegate 0.5.5 self-hosting diagnostics hardening.

## Goal

Make service startup diagnostics report the actual hosting mode when Devlegate is
run under the managed systemd user unit.

## Context

The first self-hosted systemd service started with the expected external-host
environment and `Type=notify`, but its startup banner printed:

```text
mode    : direct
```

The current startup-report call derives its display label only from whether an
internal startup file descriptor exists, so an externally supervised foreground
process is mislabeled as direct.

## Required behavior

- Startup diagnostics derive the displayed mode from the actual hosting mode, not
  merely from presence/absence of the internal startup FD.
- A Devlegate process hosted by the managed systemd unit is clearly identified as
  externally/systemd supervised and is never reported as direct.
- Existing direct foreground and internally hosted/background modes remain
  distinguishable and truthful.
- This change is diagnostic only and must not alter supervision authority,
  readiness, restart, or lifecycle semantics.

## Acceptance criteria

- A service launched with external/systemd host ownership prints an external/systemd
  hosting label in the startup banner.
- A genuinely direct attached foreground run still prints direct.
- Internal/background hosting continues to report its actual mode.
- Full tests and lint remain green.

## Required regressions

- Given `DEVLEGATE_HOST_MODE=external` under the managed systemd launch path, when
  startup diagnostics are rendered, then the mode is not `direct` and identifies
  external/systemd hosting.
- Given direct foreground hosting, when startup diagnostics are rendered, then the
  mode remains `direct`.
- Given internal/background hosting, when startup diagnostics are rendered, then it
  remains distinguishable from both direct and external hosting.

# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""CLI tests not owned by syntax, IPC routing, or production topology."""

import test_cli as source
from domain_collectors import export, inverse_tokens

_groups = (
    (
        "help",
        "parser",
        "version",
        "error",
        "render",
        "init",
        "check",
        "offline",
        "bootstrap",
    ),
    (
        "daemon",
        "ipc",
        "retry",
        "reconcile",
        "drop",
        "receipt",
        "socket",
        "authority",
        "candidate",
    ),
    ("real_service", "foreground", "lifecycle", "restart", "start", "stop"),
)
export(source, globals(), exclude=inverse_tokens(*_groups))

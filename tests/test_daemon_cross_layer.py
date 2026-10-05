# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Daemon tests outside host, lifecycle, and worker-loss ownership."""

import test_daemon as source
from domain_collectors import export, inverse_tokens

_groups = (
    ("host", "ready", "readiness", "diagnostic", "signal"),
    ("lifecycle", "drain", "stop", "restart", "shutdown"),
    ("loss", "recover", "recovery", "worker", "process"),
)
export(source, globals(), exclude=inverse_tokens(*_groups))

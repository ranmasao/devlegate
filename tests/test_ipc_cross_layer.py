# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""IPC recovery/topology bridge tests not owned by dispatch, transport, or handoff."""

import test_ipc_server as source
from domain_collectors import export, inverse_tokens

_groups = (
    ("dispatch", "payload", "validation", "view", "privacy"),
    (
        "socket",
        "frame",
        "malformed",
        "incomplete",
        "idle",
        "endpoint",
        "connection",
        "disconnect",
        "cleanup",
        "permission",
    ),
    ("owner", "thread", "retry", "reconcile", "shutdown", "receipt", "mutation"),
)
export(source, globals(), exclude=inverse_tokens(*_groups))

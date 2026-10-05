# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Unix socket transport, framing, endpoint ownership, and cleanup."""

import test_ipc_server as source
from domain_collectors import export

export(
    source,
    globals(),
    include=(
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
)

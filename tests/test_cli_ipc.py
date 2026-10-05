# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""CLI client IPC routing and fail-closed authority behavior."""

import test_cli as source
from domain_collectors import export

export(
    source,
    globals(),
    include=(
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
    exclude=("real_service",),
)

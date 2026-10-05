# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real owner-thread handoff, mutation admission, and receipt semantics."""

import test_ipc_server as source
from domain_collectors import export

export(
    source,
    globals(),
    include=(
        "owner",
        "thread",
        "retry",
        "reconcile",
        "shutdown",
        "receipt",
        "mutation",
    ),
)

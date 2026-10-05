# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Cross-layer control probes; stronger service companions remain authoritative."""

import test_control_plane as source
from domain_collectors import export, inverse_tokens

_groups = (
    (
        "control",
        "diagnostic",
        "dependency",
        "frontier",
        "serial",
        "admission",
        "validation",
        "barrier",
        "once",
    ),
    ("execution", "workspace", "checkpoint", "publication", "worktree", "branch"),
    (
        "reconcile",
        "recovery",
        "resume",
        "retry",
        "zero_delta",
        "worker_identity",
        "agent_running",
    ),
)
export(source, globals(), exclude=inverse_tokens(*_groups))

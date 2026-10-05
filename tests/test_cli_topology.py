# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Independent CLI/service process, lifecycle, and recovery topology proof."""

import test_cli as source
from domain_collectors import export

export(
    source,
    globals(),
    include=("real_service", "foreground", "lifecycle", "restart", "start", "stop"),
)

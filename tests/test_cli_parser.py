# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""CLI syntax, bootstrap, and presentation; no production service topology."""

import test_cli as source
from domain_collectors import export

export(
    source,
    globals(),
    include=(
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
)

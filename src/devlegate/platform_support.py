# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Platform boundary for the hosted Devlegate runtime."""

from __future__ import annotations

import os
import sys

HOSTED_RUNTIME_ERROR = "Devlegate hosted execution requires Linux"


def hosted_runtime_supported() -> bool:
    return os.name == "posix" and sys.platform.startswith("linux")

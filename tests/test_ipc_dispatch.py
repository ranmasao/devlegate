# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Fake dispatch and validation; this layer does not prove owner serialization."""

import test_ipc_server as source
from domain_collectors import export

export(
    source, globals(), include=("dispatch", "payload", "validation", "view", "privacy")
)

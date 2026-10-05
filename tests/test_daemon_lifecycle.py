# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Daemon owner-loop lifecycle, drain, stop, and restart semantics."""

import test_daemon as source
from domain_collectors import export

export(source, globals(), include=("lifecycle", "drain", "stop", "restart", "shutdown"))

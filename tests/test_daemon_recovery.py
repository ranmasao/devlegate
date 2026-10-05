# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Daemon worker-loss and recovery supervision semantics."""

import test_daemon as source
from domain_collectors import export

export(source, globals(), include=("loss", "recover", "recovery", "worker", "process"))

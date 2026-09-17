# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Allow Devlegate to run with ``python -m devlegate``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

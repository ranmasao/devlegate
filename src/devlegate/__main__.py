"""Allow Devlegate to run with ``python -m devlegate``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

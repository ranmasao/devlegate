"""Command-line interface for Devlegate."""

import argparse

from devlegate import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the Devlegate argument parser."""
    parser = argparse.ArgumentParser(
        prog="devlegate",
        description="Workflow orchestrator for software-development repositories.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    """Run the command-line interface."""
    build_parser().parse_args()
    return 0

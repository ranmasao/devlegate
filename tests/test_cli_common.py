# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import argparse

import pytest

from devlegate.cli_common import ConciseArgumentParser


def make_parser() -> argparse.ArgumentParser:
    parser = ConciseArgumentParser(prog="tool")
    parser.add_subparsers(dest="command", required=True).add_parser("run")
    return parser


def test_subparser_defaults_are_command_oriented():
    parser = make_parser()
    help_text = parser.format_help()
    assert "Commands:" in help_text
    assert "COMMAND" in help_text


def test_unknown_command_is_concise(capsys):
    with pytest.raises(SystemExit) as error:
        make_parser().parse_args(["missing"])

    assert error.value.code == 2
    assert capsys.readouterr().err == (
        "tool: unknown command 'missing'\nTry 'tool --help' for usage.\n"
    )


def test_missing_command_is_concise(capsys):
    with pytest.raises(SystemExit) as error:
        make_parser().parse_args([])

    assert error.value.code == 2
    assert capsys.readouterr().err == (
        "tool: a command is required\nTry 'tool --help' for usage.\n"
    )


def test_nested_parser_uses_nested_help_program(capsys):
    parser = ConciseArgumentParser(prog="tool parent")
    parser.add_argument("--value")
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["--unknown"])

    assert error.value.code == 2
    assert capsys.readouterr().err == (
        "tool parent: unrecognized argument: --unknown\n"
        "Try 'tool parent --help' for usage.\n"
    )

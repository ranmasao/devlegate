# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small argparse helpers shared by product and source-tree CLIs."""

from __future__ import annotations

import argparse
import sys
from typing import NoReturn


class ConciseArgumentParser(argparse.ArgumentParser):
    """Keep argparse parsing while presenting short command-line errors."""

    def __init__(
        self,
        *args,
        error_prefix: str | None = None,
        quote_unknown_commands: bool = True,
        help_program: str | None = None,
        **kwargs,
    ):
        self.error_prefix = error_prefix
        self.quote_unknown_commands = quote_unknown_commands
        self.help_program = help_program
        super().__init__(*args, **kwargs)

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("title", "Commands")
        kwargs.setdefault("metavar", "COMMAND")
        return super().add_subparsers(**kwargs)

    def parse_known_args(self, args=None, namespace=None):
        values = list(sys.argv[1:] if args is None else args)
        choices = self._subparser_choices()
        self._command_context = values[0] if values and values[0] in choices else None
        return super().parse_known_args(args, namespace)

    def _subparser_choices(self):
        for action in self._subparsers_actions():
            return action.choices
        return {}

    def _subparsers_actions(self):
        if self._subparsers is None:
            return ()
        return (
            action
            for action in self._subparsers._group_actions
            if isinstance(action, argparse._SubParsersAction)
        )

    def error(self, message: str) -> NoReturn:
        if ": invalid choice: " in message:
            choice = message.split(": invalid choice: ", 1)[1]
            choice = choice.split(" (choose from", 1)[0]
            if not self.quote_unknown_commands:
                choice = choice.strip("'")
            message = f"unknown command {choice}"
        elif message.startswith("the following arguments are required: "):
            required = message.removeprefix("the following arguments are required: ")
            if (
                required in self._subparser_names()
                or required == self._subparser_metavar()
            ):
                message = "a command is required"
        elif message.startswith("unrecognized arguments: "):
            message = (
                "unrecognized argument: " + message[len("unrecognized arguments: ") :]
            )

        program = self.error_prefix or self.prog
        command = getattr(self, "_command_context", None)
        if self.error_prefix is None and command is not None:
            program = f"{program} {command}"
        print(f"{program}: {message}", file=sys.stderr)
        help_program = self.help_program or program
        print(f"Try '{help_program} --help' for usage.", file=sys.stderr)
        raise SystemExit(2)

    def _subparser_names(self) -> set[str]:
        return {
            name for action in self._subparsers_actions() for name in action.choices
        }

    def _subparser_metavar(self) -> str | None:
        for action in self._subparsers_actions():
            return action.metavar
        return None

# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import inspect

from devlegate.cli import _render_status_text
from devlegate.output import render_table


def test_status_renderer_requires_explicit_service_state():
    parameter = inspect.signature(_render_status_text).parameters["service_state"]

    assert parameter.default is inspect.Parameter.empty


def test_one_row_table_has_no_middle_separator():
    output = render_table("One", (("A", "B"),))

    assert output.splitlines() == [
        "One",
        "┌───┬───┐",
        "│ A │ B │",
        "└───┴───┘",
    ]


def test_multi_row_table_separates_rows():
    output = render_table("Many", (("A", "B"), ("C", "D")))

    assert output.splitlines() == [
        "Many",
        "┌───┬───┐",
        "│ A │ B │",
        "├───┼───┤",
        "│ C │ D │",
        "└───┴───┘",
    ]


def test_table_escapes_control_whitespace_in_cells():
    output = render_table("Safe", (("key\n", "value\r\t"),))

    assert "key\\n" in output
    assert "value\\r\\t" in output
    assert output.count("\n") == 3

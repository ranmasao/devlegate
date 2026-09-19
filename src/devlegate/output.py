# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Deterministic presentation for finite CLI results."""

from __future__ import annotations

import json
from typing import Any, Iterable

from devlegate._vendor import nanoyaml

OutputFormat = str


def render_machine(value: dict[str, Any], output_format: OutputFormat) -> str:
    if output_format == "yaml":
        return nanoyaml.dumps(value).rstrip("\n")
    if output_format == "json":
        return json.dumps(value, indent=2, sort_keys=True)
    raise ValueError(f"unsupported output format: {output_format}")


def render_table(title: str, rows: Iterable[tuple[str, Any]]) -> str:
    values = [(_display(key), _display(value)) for key, value in rows]
    width = max([len(key) for key, _value in values] + [1])
    value_width = max([len(value) for _key, value in values] + [1])
    top = f"┌{'─' * (width + 2)}┬{'─' * (value_width + 2)}┐"
    middle = f"├{'─' * (width + 2)}┼{'─' * (value_width + 2)}┤"
    bottom = f"└{'─' * (width + 2)}┴{'─' * (value_width + 2)}┘"
    lines = [title, top]
    for index, (key, value) in enumerate(values):
        if index:
            lines.append(middle)
        lines.append(f"│ {key:<{width}} │ {value:<{value_width}} │")
    lines.append(bottom)
    return "\n".join(lines)


def render_grid(
    title: str, headers: Iterable[str], rows: Iterable[Iterable[Any]]
) -> str:
    header_values = [_display(value) for value in headers]
    row_values = [[_display(value) for value in row] for row in rows]
    if not header_values:
        raise ValueError("table requires at least one column")
    if any(len(row) != len(header_values) for row in row_values):
        raise ValueError("table rows must match the header width")
    columns = [header_values, *row_values]
    widths = [
        max(len(row[index]) for row in columns)
        for index in range(len(header_values))
    ]
    top = "┌" + "┬".join("─" * (width + 2) for width in widths) + "┐"
    middle = "├" + "┼".join("─" * (width + 2) for width in widths) + "┤"
    bottom = "└" + "┴".join("─" * (width + 2) for width in widths) + "┘"

    def row(values: list[str]) -> str:
        return "│" + "│".join(
            f" {value:<{width}} " for value, width in zip(values, widths)
        ) + "│"

    lines = [title, top, row(header_values)]
    for values in row_values:
        lines.extend([middle, row(values)])
    lines.append(bottom)
    return "\n".join(lines)


def _display(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def emit(value: dict[str, Any], output_format: OutputFormat, table: str) -> None:
    print(table if output_format == "table" else render_machine(value, output_format))


def add_output_arguments(parser: Any) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--yaml",
        dest="output_format",
        action="store_const",
        const="yaml",
        help="emit canonical NanoYAML",
    )
    group.add_argument(
        "--json",
        dest="output_format",
        action="store_const",
        const="json",
        help="emit machine-readable JSON",
    )
    parser.set_defaults(output_format="table")

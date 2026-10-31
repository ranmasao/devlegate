# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Deterministic presentation for finite CLI results."""

from __future__ import annotations

import json
import shutil
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from devlegate._vendor import nanoyaml

OutputFormat = str


def render_machine(value: dict[str, Any], output_format: OutputFormat) -> str:
    if output_format == "yaml":
        return nanoyaml.dumps(value).rstrip("\n")
    if output_format == "json":
        return json.dumps(value, indent=2, sort_keys=True)
    raise ValueError(f"unsupported output format: {output_format}")


@dataclass(frozen=True)
class ColumnPolicy:
    """Word wrapping and shrink penalty are independent; tokens are never split."""

    wrap: bool = False
    minimum: int = 1
    priority: int = 1


_FIXED = ColumnPolicy()
_TEXT = ColumnPolicy(wrap=True, minimum=12)


def render_table(title: str, rows: Iterable[tuple[str, Any]]) -> str:
    values = [[_display(key), _display(value)] for key, value in rows]
    return _render(title, None, values, [_FIXED, _TEXT])


def render_grid(
    title: str,
    headers: Iterable[str],
    rows: Iterable[Iterable[Any]],
    *,
    policies: Iterable[ColumnPolicy] | None = None,
) -> str:
    header_values = [_display(value) for value in headers]
    row_values = [[_display(value) for value in row] for row in rows]
    if not header_values:
        raise ValueError("table requires at least one column")
    if any(len(row) != len(header_values) for row in row_values):
        raise ValueError("table rows must match the header width")
    # Other columns include refs, execution IDs, states and counters.
    selected = (
        list(policies)
        if policies is not None
        else [
            _TEXT
            if header.lower() in {"title", "reason", "detail", "description"}
            else _FIXED
            for header in header_values
        ]
    )
    if len(selected) != len(header_values):
        raise ValueError("table policies must match the header width")
    return _render(title, header_values, row_values, selected)


def _cell_width(value: str) -> int:
    # Combining marks are zero cells, East Asian wide/fullwidth are two.
    # Emoji ZWJ sequences are not modeled by this stdlib approximation.
    return sum(
        0
        if unicodedata.combining(c)
        else 2
        if unicodedata.east_asian_width(c) in "WF"
        else 1
        for c in value
    )


def _display(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    result = []
    for char in str(value):
        if char == "\n":
            result.append(char)
        elif char in "\r\t":
            result.append("\\r" if char == "\r" else "\\t")
        elif unicodedata.category(char).startswith("C"):
            code = ord(char)
            result.append(f"\\x{code:02x}" if code <= 255 else f"\\u{code:04x}")
        else:
            result.append(char)
    return "".join(result)


def _wrap(value: str, width: int, enabled: bool) -> list[str]:
    result = []
    for line in value.split("\n"):
        if not enabled or _cell_width(line) <= width:
            result.append(line)
            continue
        words = line.split(" ")
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if current and _cell_width(candidate) > width:
                result.append(current)
                current = word
            else:
                current = candidate
        result.append(current)
    return result


def _render(
    title: str,
    header: list[str] | None,
    rows: list[list[str]],
    policies: list[ColumnPolicy],
) -> str:
    columns = ([header] if header is not None else []) + rows
    count = len(policies)
    natural = [
        max([_cell_width(line) for row in columns for line in row[i].split("\n")] + [1])
        for i in range(count)
    ]
    minimum = []
    for i, policy in enumerate(policies):
        if not policy.wrap:
            minimum.append(natural[i])
        else:
            tokens = [
                word
                for row in columns
                for line in row[i].split("\n")
                for word in line.split(" ")
            ]
            minimum.append(
                min(
                    natural[i],
                    max([policy.minimum, 1] + [_cell_width(w) for w in tokens]),
                )
            )
    widths = natural[:]
    tty = sys.stdout.isatty()
    if tty:
        budget = max(
            0, shutil.get_terminal_size(fallback=(80, 24)).columns - 3 * count - 1
        )
        if sum(widths) > budget:
            target = max(budget, sum(minimum))
            deficit = sum(widths) - target
            if deficit > 256:
                # Coarse proportional reduction bounds work on huge inputs.
                capacity = sum(n - m for n, m in zip(natural, minimum))
                for i in range(count):
                    widths[i] -= (deficit - 256) * (natural[i] - minimum[i]) // capacity
            if sum(widths) > target:
                heights: dict[tuple[int, int], list[int]] = {}

                def score(candidate: list[int]) -> tuple[int, int, tuple[int, ...]]:
                    for i, width in enumerate(candidate):
                        if (i, width) not in heights:
                            heights[i, width] = [
                                len(_wrap(row[i], width, policies[i].wrap))
                                for row in columns
                            ]
                    height = sum(
                        max(heights[i, candidate[i]][r] for i in range(count))
                        for r in range(len(columns))
                    )
                    penalty = sum(
                        (natural[i] - candidate[i]) * policies[i].priority
                        for i in range(count)
                    )
                    return height, penalty, tuple(-w for w in candidate)

                while sum(widths) > target:
                    choices = []
                    for i in range(count):
                        if widths[i] > minimum[i]:
                            candidate = widths[:]
                            candidate[i] -= 1
                            choices.append(candidate)
                    widths = min(choices, key=score)
                for _ in range(256):
                    best = widths
                    best_score = score(widths)
                    for i in range(count):
                        for j in range(count):
                            if widths[i] > minimum[i] and widths[j] < natural[j]:
                                candidate = widths[:]
                                candidate[i] -= 1
                                candidate[j] += 1
                                candidate_score = score(candidate)
                                if candidate_score < best_score:
                                    best, best_score = candidate, candidate_score
                    if best is widths:
                        break
                    widths = best

    def border(left: str, joint: str, right: str) -> str:
        return left + joint.join("─" * (width + 2) for width in widths) + right

    def draw(values: list[str]) -> list[str]:
        cells = [
            _wrap(value, widths[i], policies[i].wrap and tty)
            for i, value in enumerate(values)
        ]
        physical = []
        for line in range(max(map(len, cells))):
            parts = [cell[line] if line < len(cell) else "" for cell in cells]
            physical.append(
                "│"
                + "│".join(
                    " " + value + " " * (widths[i] - _cell_width(value) + 1)
                    for i, value in enumerate(parts)
                )
                + "│"
            )
        return physical

    lines = [_display(title), border("┌", "┬", "┐")]
    for index, values in enumerate(columns):
        if index:
            lines.append(border("├", "┼", "┤"))
        lines.extend(draw(values))
    lines.append(border("└", "┴", "┘"))
    return "\n".join(lines)


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

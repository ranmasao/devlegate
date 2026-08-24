"""Vendored from ranmasao/rslab2 NanoYAML N0.

Changes to the accepted language subset must be intentional.
"""

from __future__ import annotations

import json
import re
from typing import Any


class NanoYAMLError(ValueError):
    """Raised when a value is outside the NanoYAML N0 data model."""


def _quoted(value: str, path: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise NanoYAMLError(f"unsupported surrogate in string at {path}")
    return json.dumps(value, ensure_ascii=False)


def _validate(value: Any, path: str, active: set[int]) -> None:
    if isinstance(value, str):
        _quoted(value, path)
        return
    if isinstance(value, bool):
        raise NanoYAMLError(f"unsupported bool at {path}")
    if isinstance(value, int):
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise NanoYAMLError(f"cyclic container at {path}")
        if not value:
            raise NanoYAMLError(f"empty mapping at {path}")
        active.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise NanoYAMLError(f"non-string mapping key at {path}")
                _quoted(key, f"{path}.<key>")
                _validate(child, f"{path}.{key}", active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise NanoYAMLError(f"cyclic container at {path}")
        if not value:
            raise NanoYAMLError(f"empty sequence at {path}")
        active.add(identity)
        try:
            for index, child in enumerate(value):
                _validate(child, f"{path}[{index}]", active)
        finally:
            active.remove(identity)
        return
    raise NanoYAMLError(f"unsupported {type(value).__name__} at {path}")


def _render(value: Any, indent: int, sequence_item: bool = False) -> list[str]:
    spaces = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for index, (key, child) in enumerate(value.items()):
            key_indent = indent + (2 if sequence_item and index else 0)
            prefix = "- " if sequence_item and index == 0 else ""
            line = f'{" " * key_indent}{prefix}{_quoted(key, "<key>")}:'
            if isinstance(child, (dict, list)):
                lines.append(line)
                child_indent = key_indent + (4 if sequence_item and index == 0 else 2)
                lines.extend(_render(child, child_indent))
            else:
                scalar = (
                    str(child)
                    if isinstance(child, int)
                    else _quoted(child, "<value>")
                )
                lines.append(f"{line} {scalar}")
        return lines
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, (dict, list)):
                if isinstance(child, dict):
                    lines.extend(_render(child, indent, True))
                else:
                    lines.append(f"{spaces}-")
                    lines.extend(_render(child, indent + 2))
            else:
                scalar = (
                    str(child)
                    if isinstance(child, int)
                    else _quoted(child, "<value>")
                )
                lines.append(f"{spaces}- {scalar}")
        return lines
    raise AssertionError("validated value is not a collection")


def dumps(value: dict[str, Any]) -> str:
    """Return canonical NanoYAML text for a non-empty mapping document."""
    if not isinstance(value, dict):
        raise NanoYAMLError("document root must be a mapping")
    _validate(value, "$", set())
    return "\n".join(_render(value, 0)) + "\n"


class _Parser:
    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("nanoyaml.loads() expects str")
        raw_lines = text.splitlines()
        if not raw_lines or all(not line.strip() for line in raw_lines):
            raise NanoYAMLError("line 1: empty document")
        self.lines: list[tuple[int, int, str]] = []
        for number, line in enumerate(raw_lines, 1):
            if not line.strip():
                raise NanoYAMLError(f"line {number}: blank lines are unsupported")
            if "\t" in line[: len(line) - len(line.lstrip(" \t"))]:
                raise NanoYAMLError(f"line {number}: tabs are invalid indentation")
            indent = len(line) - len(line.lstrip(" "))
            self.lines.append((number, indent, line[indent:]))
        self.index = 0

    def fail(self, number: int, reason: str) -> None:
        raise NanoYAMLError(f"line {number}: {reason}")

    def parse(self) -> dict[str, Any]:
        if self.lines[0][1] != 0 or self.lines[0][2].startswith("-"):
            self.fail(self.lines[0][0], "document root must be a mapping")
        value = self.mapping(0)
        if self.index != len(self.lines):
            number, indent, _ = self.lines[self.index]
            self.fail(number, f"unexpected indentation ({indent} spaces)")
        return value

    def block(self, indent: int) -> Any:
        if self.index >= len(self.lines):
            self.fail(self.lines[-1][0], "missing nested value")
        number, actual, content = self.lines[self.index]
        if actual != indent:
            self.fail(number, f"invalid indentation; expected {indent} spaces")
        if content == "-" or content.startswith("- "):
            return self.sequence(indent)
        if content.startswith("-"):
            self.fail(number, "malformed sequence marker")
        return self.mapping(indent)

    def mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self.index < len(self.lines):
            number, actual, content = self.lines[self.index]
            if actual < indent:
                break
            if actual != indent:
                self.fail(number, f"invalid indentation; expected {indent} spaces")
            if content.startswith("-"):
                self.fail(number, "sequence item is not valid in a mapping")
            key, rest = self.entry(content, number)
            if key in result:
                self.fail(number, f"duplicate key {key!r}")
            result[key] = self.value(rest, number, indent + 2)
        if not result:
            self.fail(self.lines[self.index][0], "empty mapping")
        return result

    def sequence(self, indent: int) -> list[Any]:
        result: list[Any] = []
        while self.index < len(self.lines):
            number, actual, content = self.lines[self.index]
            if actual < indent:
                break
            if actual != indent:
                self.fail(number, f"invalid indentation; expected {indent} spaces")
            if content == "-":
                self.index += 1
                result.append(self.block(indent + 2))
                continue
            if not content.startswith("- "):
                break
            first = content[2:]
            if first.startswith('"') and self._has_mapping_colon(first):
                result.append(self.sequence_mapping(indent, number, first))
            else:
                self.index += 1
                result.append(self.scalar(first, number))
        if not result:
            self.fail(self.lines[self.index][0], "empty sequence")
        return result

    def _has_mapping_colon(self, value: str) -> bool:
        try:
            _, end = json.JSONDecoder().raw_decode(value)
        except json.JSONDecodeError:
            return False
        return end < len(value) and value[end] == ":"

    def sequence_mapping(
        self, sequence_indent: int, number: int, first: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        key, rest = self.entry(first, number)
        result[key] = self.value(rest, number, sequence_indent + 4)
        continuation_indent = sequence_indent + 2
        while self.index < len(self.lines):
            line_number, indent, content = self.lines[self.index]
            if indent < continuation_indent:
                break
            if indent != continuation_indent:
                self.fail(
                    line_number,
                    f"invalid indentation; expected {continuation_indent} spaces",
                )
            if content.startswith("-"):
                self.fail(line_number, "malformed sequence mapping continuation")
            key, rest = self.entry(content, line_number)
            if key in result:
                self.fail(line_number, f"duplicate key {key!r}")
            result[key] = self.value(rest, line_number, continuation_indent + 2)
        return result

    def entry(self, content: str, number: int) -> tuple[str, str]:
        if not content.startswith('"'):
            self.fail(number, "mapping keys must be double-quoted")
        key, end = self.quoted(content, number)
        if end >= len(content) or content[end] != ":":
            self.fail(number, "malformed mapping entry")
        return key, content[end + 1 :].lstrip() if content[end + 1 :].strip() else ""

    def value(self, rest: str, number: int, child_indent: int) -> Any:
        self.index += 1
        if rest:
            return self.scalar(rest, number)
        if self.index >= len(self.lines):
            self.fail(number, "missing nested value")
        if self.lines[self.index][1] != child_indent:
            self.fail(
                self.lines[self.index][0],
                f"invalid indentation; expected {child_indent} spaces",
            )
        return self.block(child_indent)

    def scalar(self, value: str, number: int) -> str | int:
        if value.startswith('"'):
            parsed, end = self.quoted(value, number)
            if end != len(value):
                self.fail(number, "trailing content after quoted scalar")
            return parsed
        if re.fullmatch(r"0|-?[1-9][0-9]*", value):
            return int(value)
        self.fail(number, "unsupported or invalid scalar")

    def quoted(self, value: str, number: int) -> tuple[str, int]:
        try:
            decoder = json.JSONDecoder()
            parsed, end = decoder.raw_decode(value)
        except (json.JSONDecodeError, TypeError):
            self.fail(number, "malformed quoted string or escape")
        if not isinstance(parsed, str):
            self.fail(number, "quoted mapping key/value must be a string")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in parsed):
            self.fail(number, "unsupported surrogate in quoted string")
        return parsed, end


def loads(text: str) -> dict[str, Any]:
    """Parse a canonical NanoYAML document into ordinary Python values."""
    return _Parser(text).parse()


__all__ = ["NanoYAMLError", "dumps", "loads"]

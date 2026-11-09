#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Render the packaging-owned logical Linux runtime inventory; build-time only."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

try:
    from package_standalone import PackageError
except ModuleNotFoundError:
    from tools.package_standalone import PackageError


INVENTORY = Path(__file__).resolve().parents[1] / "packaging/linux-runtime.toml"


def dependencies(family: str, inventory: Path | None = None) -> tuple[str, ...]:
    """Fail closed for missing providers or ABI translations; sort by logical ID."""
    try:
        with (inventory or INVENTORY).open("rb") as stream:
            data = tomllib.load(stream)
        if data["schema"] != 1 or family not in {"arch", "debian"}:
            raise ValueError("unsupported dependency schema or family")
        requirements = data["requirements"]
        mappings = data["families"][family]
        if not requirements:
            raise ValueError("empty mandatory runtime inventory")
        rendered = []
        for identity, requirement in sorted(requirements.items()):
            if identity not in mappings:
                raise ValueError(f"unmapped mandatory runtime requirement: {identity}")
            mapping = mappings[identity]
            if set(mapping) - {"package", "minimum", "abi"}:
                raise ValueError(f"unsupported provider constraint: {identity}")
            package = mapping["package"]
            minimum = mapping.get("minimum")
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", package):
                raise ValueError(f"invalid native provider for {identity}")
            if requirement["kind"] not in {"application", "elf"}:
                raise ValueError(f"invalid mandatory requirement kind: {identity}")
            if set(requirement) - {"kind", "abi", "objects", "capabilities"}:
                raise ValueError(f"unsupported mandatory constraint: {identity}")
            if (requirement["kind"] == "elf" or "abi" in requirement) and (
                not requirement.get("abi")
                or requirement["abi"] != mapping.get("abi")
                or not minimum
            ):
                raise ValueError(f"unrepresentable required ABI: {identity}")
            if minimum is not None:
                if not re.fullmatch(r"(?:[0-9]+:)?[0-9]+(?:\.[0-9]+)*", minimum):
                    raise ValueError(f"invalid provider minimum for {identity}")
                package += (
                    f">={minimum}" if family == "arch" else f" (>= {minimum})"
                )
            rendered.append(package)
        if len(rendered) != len(set(rendered)):
            raise ValueError("duplicate native runtime provider")
        return tuple(rendered)
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise PackageError(f"{family} runtime dependencies: {error}") from error

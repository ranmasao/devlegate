# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small collection-only adapter used by semantic test modules."""

_EXPORTED = set()


def export(source, namespace, *, include=(), exclude=()):
    """Expose source tests matching one semantic ownership predicate."""
    for name in dir(source):
        if not name.startswith("test_"):
            continue
        if include and not any(token in name for token in include):
            continue
        if any(token in name for token in exclude):
            continue
        key = (source.__name__, name)
        if key in _EXPORTED:
            continue
        _EXPORTED.add(key)
        namespace[name] = getattr(source, name)


def inverse_tokens(*groups):
    return tuple(token for group in groups for token in group)

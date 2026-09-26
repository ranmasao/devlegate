# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small shared protocol for component-owned distribution progress."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

ProgressAction = Literal["start", "complete", "skip", "fail"]


@dataclass(frozen=True)
class ComponentStep:
    """A semantic operation owned by one component builder."""

    name: str
    optional: bool = False
    key: str | None = None

    @property
    def identity(self) -> str:
        return self.key or self.name


@dataclass(frozen=True)
class ComponentEvent:
    action: ProgressAction
    step: ComponentStep
    leaf_id: str | None = None


@dataclass(frozen=True)
class ComponentPlan:
    """A deterministic component tree; only leaves are executable work."""

    name: str
    children: tuple[ComponentPlan, ...] = ()
    step: ComponentStep | None = None
    key: str | None = None

    @classmethod
    def leaf(cls, step: ComponentStep) -> ComponentPlan:
        return cls(step.name, step=step, key=step.identity)

    @property
    def executable(self) -> bool:
        return self.step is not None


class Component(Protocol):
    def plan(self) -> ComponentPlan: ...

    def run(self, emit: Callable[[ComponentEvent], None]) -> object: ...


@dataclass(frozen=True)
class LeafComponent:
    step: ComponentStep
    action: Callable[[], object]

    def plan(self) -> ComponentPlan:
        return ComponentPlan.leaf(self.step)

    def run(self, emit: Callable[[ComponentEvent], None]) -> object:
        emit(ComponentEvent("start", self.step))
        try:
            result = self.action()
        except Exception:
            emit(ComponentEvent("fail", self.step))
            raise
        emit(ComponentEvent("complete", self.step))
        return result


@dataclass(frozen=True)
class CompositeComponent:
    name: str
    children: tuple[Component, ...]

    def plan(self) -> ComponentPlan:
        return ComponentPlan(self.name, tuple(child.plan() for child in self.children))

    def run(self, emit: Callable[[ComponentEvent], None]) -> tuple[object, ...]:
        return tuple(child.run(emit) for child in self.children)


def freeze_plan(
    plan: ComponentPlan, prefix: str = ""
) -> tuple[tuple[str, ComponentStep], ...]:
    """Flatten executable leaves into stable hierarchical IDs."""
    leaves: list[tuple[str, ComponentStep]] = []
    if plan.step is not None:
        leaf_id = prefix or plan.step.identity
        leaves.append((leaf_id, plan.step))
        return tuple(leaves)
    seen: set[str] = set()
    for child in plan.children:
        segment = child.key or (child.step.identity if child.step else child.name)
        if not segment or "/" in segment or segment in seen:
            raise ValueError(
                f"component tree has an invalid or duplicate key: {segment!r}"
            )
        seen.add(segment)
        child_prefix = f"{prefix}/{segment}" if prefix else segment
        leaves.extend(freeze_plan(child, child_prefix))
    return tuple(leaves)

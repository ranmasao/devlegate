"""Small shared protocol for component-owned distribution progress."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Iterable
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
    children: tuple["ComponentPlan", ...] = ()
    step: ComponentStep | None = None

    @classmethod
    def leaf(cls, step: ComponentStep) -> "ComponentPlan":
        return cls(step.name, step=step)

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
        return ComponentPlan(
            self.name, tuple(child.plan() for child in self.children)
        )

    def run(self, emit: Callable[[ComponentEvent], None]) -> tuple[object, ...]:
        return tuple(child.run(emit) for child in self.children)


def freeze_plan(plan: ComponentPlan, prefix: str = "") -> tuple[tuple[str, ComponentStep], ...]:
    """Flatten executable leaves into stable hierarchical IDs."""
    leaves: list[tuple[str, ComponentStep]] = []
    if plan.step is not None:
        leaves.append((prefix or plan.step.identity, plan.step))
        return tuple(leaves)
    for index, child in enumerate(plan.children):
        child_prefix = f"{prefix}/{index}" if prefix else str(index)
        leaves.extend(freeze_plan(child, child_prefix))
    return tuple(leaves)

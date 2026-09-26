"""Small shared protocol for component-owned distribution progress."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProgressAction = Literal["start", "complete", "skip", "fail"]


@dataclass(frozen=True)
class ComponentStep:
    """A semantic operation owned by one component builder."""

    name: str
    optional: bool = False


@dataclass(frozen=True)
class ComponentEvent:
    action: ProgressAction
    step: ComponentStep

"""Typed extension points shared by every Plugin lifecycle scope."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class PluginScope(str, Enum):
    APPLICATION = "application"
    SESSION = "session"
    RUN = "run"


@dataclass(frozen=True, slots=True)
class ExtensionPoint(Generic[T]):
    """A stable, typed contract consumed by a host adapter."""

    id: str
    scope: PluginScope
    validator: Callable[[Any], bool] | None = None

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValueError("Extension point id cannot be empty")

    def validate(self, value: Any) -> T:
        if self.validator is not None and not self.validator(value):
            raise TypeError(f"Invalid contribution for extension point {self.id}")
        return value


@dataclass(frozen=True, slots=True)
class ExtensionContribution(Generic[T]):
    point: ExtensionPoint[T]
    value: T

    def __post_init__(self) -> None:
        self.point.validate(self.value)


class ExtensionRegistry:
    """Immutable-by-convention index of contributions for one Plugin package."""

    def __init__(self, contributions: Iterable[ExtensionContribution[Any]] = ()) -> None:
        self._values: dict[str, list[ExtensionContribution[Any]]] = {}
        for contribution in contributions:
            self.add(contribution)

    def add(self, contribution: ExtensionContribution[Any]) -> None:
        self._values.setdefault(contribution.point.id, []).append(contribution)

    def contributions(self, point: ExtensionPoint[T]) -> tuple[ExtensionContribution[T], ...]:
        values = self._values.get(point.id, ())
        for contribution in values:
            if contribution.point.scope is not point.scope:
                raise ValueError(f"Extension point scope mismatch for {point.id}")
        return tuple(values)

    def values(self, point: ExtensionPoint[T]) -> tuple[T, ...]:
        return tuple(item.value for item in self.contributions(point))


APPLICATION_SETUP = ExtensionPoint[Callable[[Any], None]](
    "cyrene.application.setup", PluginScope.APPLICATION, callable
)
SESSION_SETUP = ExtensionPoint[Callable[[Any], None]](
    "cyrene.session.setup", PluginScope.SESSION, callable
)
RUN_SERVICE = ExtensionPoint[Any]("cyrene.run.service", PluginScope.RUN)


__all__ = [
    "APPLICATION_SETUP",
    "ExtensionContribution",
    "ExtensionPoint",
    "ExtensionRegistry",
    "PluginScope",
    "RUN_SERVICE",
    "SESSION_SETUP",
]

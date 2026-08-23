"""Stable module facade for the behavior-learning orchestrator.

Attribute writes are forwarded as well as reads so existing test and extension
monkeypatches still affect the canonical implementation.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from cyrene.learning import orchestrator as _orchestrator


class _EngineFacade(ModuleType):
    def __getattr__(self, name: str) -> Any:
        return getattr(_orchestrator, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if not name.startswith("__") and name != "_orchestrator":
            setattr(_orchestrator, name, value)
        super().__setattr__(name, value)

    def __dir__(self) -> list[str]:
        return sorted({*super().__dir__(), *dir(_orchestrator)})


sys.modules[__name__].__class__ = _EngineFacade

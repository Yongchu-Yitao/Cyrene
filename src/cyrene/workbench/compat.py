"""Explicit lazy seams for services still backed by legacy Workbench modules."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def runtime_service() -> ModuleType:
    """Return the legacy Workbench composition module on demand."""
    return import_module("cyrene.workbench.runtime_implementation")


def chat_service() -> ModuleType:
    """Return the Workbench chat persistence service on demand."""
    return import_module("cyrene.workbench.chat")

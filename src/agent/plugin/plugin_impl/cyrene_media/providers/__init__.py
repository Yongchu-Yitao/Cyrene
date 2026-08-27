"""Independent media provider collection."""

from .base import MediaProvider, ProgressCallback
from .registry import PROVIDERS, available_providers, resolve_provider

__all__ = [
    "MediaProvider",
    "PROVIDERS",
    "ProgressCallback",
    "available_providers",
    "resolve_provider",
]

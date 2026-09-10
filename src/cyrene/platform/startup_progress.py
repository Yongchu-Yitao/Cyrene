"""Compatibility export for startup telemetry now owned by the core layer."""

from cyrene.core.startup_progress import report_startup_progress

__all__ = ["report_startup_progress"]

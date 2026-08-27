"""Structured operation logging shared by the new Agent runtime.

The Agent package deliberately emits through Python's logging hierarchy so the
host decides where records are stored.  Cyrene's normal entry points attach the
rotating ``data/logs/cyrene.log`` handler to the root logger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

LOG_PREFIX = "agent.operation"
MAX_STRING_LENGTH = 4_000
MAX_COLLECTION_ITEMS = 100
MAX_DEPTH = 8

_SENSITIVE_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized == "tokens" or normalized.endswith("_tokens"):
        return False
    if normalized in {"token_count", "token_limit", "node_token_count"}:
        return False
    return any(part in normalized for part in _SENSITIVE_PARTS)


def _trim(text: str) -> str:
    if len(text) <= MAX_STRING_LENGTH:
        return text
    omitted = len(text) - MAX_STRING_LENGTH
    return f"{text[:MAX_STRING_LENGTH]}…<truncated {omitted} chars>"


def safe_log_value(value: Any, *, _depth: int = 0) -> Any:
    """Return a bounded, JSON-compatible representation for operation logs."""

    if _depth >= MAX_DEPTH:
        return f"<{type(value).__name__}:max-depth>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        return _trim(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": _trim(str(value))}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{type(value).__name__}:{len(value)} bytes>"
    if is_dataclass(value) and not isinstance(value, type):
        try:
            value = {field.name: getattr(value, field.name) for field in fields(value)}
        except Exception:
            return _trim(repr(value))
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                break
            normalized_key = str(key)
            output[normalized_key] = (
                "<redacted>"
                if _sensitive_key(normalized_key)
                else safe_log_value(item, _depth=_depth + 1)
            )
        if len(value) > MAX_COLLECTION_ITEMS:
            output["<truncated>"] = len(value) - MAX_COLLECTION_ITEMS
        return output
    if isinstance(value, (list, tuple, set, frozenset)):
        if isinstance(value, (list, tuple)):
            selected = value[:MAX_COLLECTION_ITEMS]
        else:
            selected = []
            for index, item in enumerate(value):
                if index >= MAX_COLLECTION_ITEMS:
                    break
                selected.append(item)
        output = [safe_log_value(item, _depth=_depth + 1) for item in selected]
        if len(value) > MAX_COLLECTION_ITEMS:
            output.append(f"<truncated {len(value) - MAX_COLLECTION_ITEMS} items>")
        return output
    try:
        return _trim(repr(value))
    except Exception:
        return f"<{type(value).__name__}:unrepresentable>"


def log_operation(
    logger: logging.Logger,
    component: str,
    action: str,
    *,
    level: int = logging.INFO,
    exc_info: Any = None,
    **fields: Any,
) -> None:
    """Emit one searchable JSON operation record."""

    payload = {
        "component": str(component),
        "action": str(action),
        **fields,
    }
    try:
        encoded = json.dumps(
            safe_log_value(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception as exc:
        encoded = json.dumps(
            {
                "component": str(component),
                "action": str(action),
                "log_serialization_error": f"{type(exc).__name__}: {exc}",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    try:
        logger.log(level, "%s %s", LOG_PREFIX, encoded, exc_info=exc_info)
    except Exception:
        # Observability must never alter Agent, Hook, Plugin, or Context behavior.
        pass


class OperationLog:
    """Log the start and terminal outcome of one synchronous or async operation."""

    def __init__(
        self,
        logger: logging.Logger,
        component: str,
        action: str,
        **fields: Any,
    ) -> None:
        self.logger = logger
        self.component = str(component)
        self.action = str(action)
        self.fields = dict(fields)
        self.result_fields: dict[str, Any] = {}
        self._started = 0.0

    def __enter__(self) -> OperationLog:
        self._started = time.perf_counter()
        fields = {**self.fields, "phase": "started"}
        log_operation(
            self.logger,
            self.component,
            self.action,
            **fields,
        )
        return self

    def finish(self, **fields: Any) -> None:
        """Attach fields that are only known after successful execution."""

        self.result_fields.update(fields)

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        duration_ms = round((time.perf_counter() - self._started) * 1_000, 3)
        fields = {**self.fields, **self.result_fields, "duration_ms": duration_ms}
        if exc is None:
            fields["phase"] = "completed"
            log_operation(
                self.logger,
                self.component,
                self.action,
                **fields,
            )
            return False
        cancelled = isinstance(exc, asyncio.CancelledError)
        fields.update(
            phase="cancelled" if cancelled else "failed",
            error=exc,
        )
        log_operation(
            self.logger,
            self.component,
            self.action,
            level=logging.WARNING if cancelled else logging.ERROR,
            exc_info=(exc_type, exc, traceback),
            **fields,
        )
        return False


def operation(
    logger: logging.Logger,
    component: str,
    action: str,
    **fields: Any,
) -> OperationLog:
    return OperationLog(logger, component, action, **fields)


__all__ = [
    "LOG_PREFIX",
    "MAX_COLLECTION_ITEMS",
    "MAX_DEPTH",
    "MAX_STRING_LENGTH",
    "OperationLog",
    "log_operation",
    "operation",
    "safe_log_value",
]

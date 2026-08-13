"""Stable failure kinds for the unified Agent Runtime.

Every protocol/transport failure is normalized to one of these kinds so the
Workbench UI can offer deterministic recovery actions instead of guessing from
free-form error text. See docs/external-agent-phase-1-handoff.zh-CN.md §15.
"""

from __future__ import annotations

from typing import Any, Literal

FAILURE_KINDS = (
    "dependency_missing",
    "agent_disabled",
    "auth_required",
    "auth_expired",
    "protocol_mismatch",
    "capability_missing",
    "model_binding_unsupported",
    "model_gateway_unavailable",
    "agent_crashed",
    "session_not_loadable",
    "request_expired",
)

FailureKind = Literal[
    "dependency_missing",
    "agent_disabled",
    "auth_required",
    "auth_expired",
    "protocol_mismatch",
    "capability_missing",
    "model_binding_unsupported",
    "model_gateway_unavailable",
    "agent_crashed",
    "session_not_loadable",
    "request_expired",
]


def is_failure_kind(value: Any) -> bool:
    return isinstance(value, str) and value in FAILURE_KINDS


def failure_kind(value: Any) -> str:
    """Coerce an arbitrary error marker to a stable failure kind.

    Unknown markers normalize to ``"unknown"`` so callers never branch on
    driver-specific free-form strings.
    """
    if is_failure_kind(value):
        return value
    return "unknown"


class AgentRuntimeError(Exception):
    """Carries a stable ``failureKind`` across the Agent Runtime boundary."""

    def __init__(
        self,
        kind: str,
        message: str = "",
        *,
        detail: Any = None,
        retryable: bool = False,
    ) -> None:
        normalized = failure_kind(kind)
        super().__init__(message or normalized)
        self.kind = normalized
        self.detail = detail
        self.retryable = bool(retryable)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "failureKind": self.kind,
            "message": str(self),
            "detail": self.detail,
            "retryable": self.retryable,
        }

"""ACP (Agent Client Protocol) JSON-RPC surface for the Cyrene Agent Runtime.

Defines the exact JSON-RPC 2.0 framing used by the ``acp_stdio`` driver:
method names, notification names, error codes, and small builders that keep
frame construction in one place.  Protocol variants differ across agents
(OpenCode ACP, Codex ACP, Pi ACP), so every method listed here may be retried
through a tolerant fallback by the transport/runtime layer; product-specific
branches never reach the chat UI (handoff §5/§20).
"""

from __future__ import annotations

import json
from typing import Any

JSONRPC_VERSION = "2.0"

# Server methods the client may invoke (JSON-RPC requests).
ACP_METHOD_INITIALIZE = "initialize"
ACP_METHOD_SESSION_NEW = "session/new"
ACP_METHOD_SESSION_LOAD = "session/load"
ACP_METHOD_SESSION_PROMPT = "session/prompt"
ACP_METHOD_SESSION_SET_CONFIG_OPTION = "session/set_config_option"
ACP_METHOD_SESSION_CANCEL = "session/cancel"
ACP_METHOD_SESSION_INTERRUPT = "session/interrupt"  # tolerant fallback for cancel
ACP_METHOD_SESSION_UPDATE = "session/update"
ACP_METHOD_PERMISSIONS_RESPONSE = "permissions/response"
ACP_METHOD_REQUEST_PERMISSION = "session/request_permission"
ACP_METHOD_ELICITATION_CREATE = "elicitation/create"

# Server -> client notifications.  ``run/*`` names are tolerated variants used
# by some ACP implementations; the canonical ACP signal for run termination is
# ``session/prompt_updated`` with a terminal prompt status.
ACP_NOTIFY_SESSION_UPDATED = "session/updated"
ACP_NOTIFY_SESSION_UPDATE = "session/update"
ACP_NOTIFY_SESSION_PROMPT_UPDATED = "session/prompt_updated"
ACP_NOTIFY_MESSAGE_UPDATED = "message/updated"
ACP_NOTIFY_TOOL_UPDATED = "tool/updated"
ACP_NOTIFY_PERMISSION_REQUESTED = "permission/requested"
ACP_NOTIFY_PERMISSION_RESOLVED = "permission/resolved"
ACP_NOTIFY_ELICITATION_REQUESTED = "elicitation/requested"
ACP_NOTIFY_ARTIFACT_UPDATED = "artifact/updated"
ACP_NOTIFY_USAGE_UPDATED = "usage/updated"
ACP_NOTIFY_RUN_STARTED = "run/started"
ACP_NOTIFY_RUN_COMPLETED = "run/completed"
ACP_NOTIFY_RUN_FAILED = "run/failed"
ACP_NOTIFY_RUN_CANCELLED = "run/cancelled"

ACP_SERVER_METHODS = frozenset({
    ACP_METHOD_INITIALIZE,
    ACP_METHOD_SESSION_NEW,
    ACP_METHOD_SESSION_LOAD,
    ACP_METHOD_SESSION_PROMPT,
    ACP_METHOD_SESSION_SET_CONFIG_OPTION,
    ACP_METHOD_SESSION_CANCEL,
    ACP_METHOD_SESSION_INTERRUPT,
    ACP_METHOD_SESSION_UPDATE,
    ACP_METHOD_PERMISSIONS_RESPONSE,
    ACP_METHOD_REQUEST_PERMISSION,
    ACP_METHOD_ELICITATION_CREATE,
})

ACP_NOTIFICATIONS = frozenset({
    ACP_NOTIFY_SESSION_UPDATED,
    ACP_NOTIFY_SESSION_UPDATE,
    ACP_NOTIFY_SESSION_PROMPT_UPDATED,
    ACP_NOTIFY_MESSAGE_UPDATED,
    ACP_NOTIFY_TOOL_UPDATED,
    ACP_NOTIFY_PERMISSION_REQUESTED,
    ACP_NOTIFY_PERMISSION_RESOLVED,
    ACP_NOTIFY_ELICITATION_REQUESTED,
    ACP_NOTIFY_ARTIFACT_UPDATED,
    ACP_NOTIFY_USAGE_UPDATED,
    ACP_NOTIFY_RUN_STARTED,
    ACP_NOTIFY_RUN_COMPLETED,
    ACP_NOTIFY_RUN_FAILED,
    ACP_NOTIFY_RUN_CANCELLED,
})

# JSON-RPC 2.0 error codes (subset relevant to ACP).
ERROR_PARSE_ERROR = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL_ERROR = -32603

# ACP protocol version negotiated on ``initialize``.  The transport is tolerant:
# a server that answers ``method not found`` to ``initialize`` is treated as
# protocol version 1 with conservative capabilities (never a hard failure).
ACP_PROTOCOL_VERSION = 1


class JsonRpcError(Exception):
    """Structured JSON-RPC error surfaced by a remote agent or the transport."""

    def __init__(self, code: int, message: str = "", *, data: Any = None) -> None:
        super().__init__(message or f"jsonrpc error {code}")
        self.code = int(code)
        self.data = data

    @property
    def is_method_not_found(self) -> bool:
        return self.code == ERROR_METHOD_NOT_FOUND

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.data is not None:
            result["data"] = self.data
        return result


def build_request(method: str, params: dict[str, Any] | None, request_id: int) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
    }
    if params is not None:
        frame["params"] = params
    return frame


def build_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def build_response(result: Any, request_id: int | str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def build_error(
    code: int,
    message: str,
    request_id: int | str | None,
    *,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": int(code), "message": message}
    if data is not None:
        error["data"] = data
    frame: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "error": error}
    if request_id is not None:
        frame["id"] = request_id
    return frame


def parse_frame(line: str) -> Any:
    """Parse one newline-delimited JSON-RPC frame.

    Returns ``None`` for blank/whitespace-only lines.  Raises ``ValueError``
    for invalid JSON so the transport can record a protocol error.
    """
    if not line or not line.strip():
        return None
    return json.loads(line)


def frame_kind(frame: Any) -> str:
    """Classify a parsed frame: ``request``, ``response``, ``error``, or ``notification``."""
    if not isinstance(frame, dict):
        return "invalid"
    if "id" in frame and "method" in frame:
        return "request"
    if "method" in frame:
        return "notification"
    if "error" in frame:
        return "error"
    if "result" in frame:
        return "response"
    return "invalid"


def frame_id(frame: dict[str, Any]) -> int | str | None:
    value = frame.get("id")
    return value if isinstance(value, (int, str)) and not isinstance(value, bool) else None


def error_from_frame(frame: dict[str, Any]) -> JsonRpcError:
    error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
    return JsonRpcError(
        int(error.get("code", ERROR_INTERNAL_ERROR)),
        str(error.get("message") or "jsonrpc error"),
        data=error.get("data"),
    )

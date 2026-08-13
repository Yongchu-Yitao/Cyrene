"""ACP notification -> unified AgentEvent envelope normalization.

Every ACP notification is normalized into the unified event envelope before it
can reach the Workbench UI (handoff §12).  The mapping is deliberately
tolerant: ACP implementations (OpenCode ACP, Codex ACP, Pi ACP) differ in
whether fields live at the notification root or under ``params``, and in how
they report run termination (``session/prompt_updated`` status vs. ``run/*``
notifications).  Payloads are recursively redacted for secrets before they
leave this module.
"""

from __future__ import annotations

import re
from typing import Any

from cyrene.agent_runtime.acp_protocol import (
    ACP_NOTIFY_ARTIFACT_UPDATED,
    ACP_NOTIFY_ELICITATION_REQUESTED,
    ACP_NOTIFY_MESSAGE_UPDATED,
    ACP_NOTIFY_PERMISSION_REQUESTED,
    ACP_NOTIFY_PERMISSION_RESOLVED,
    ACP_NOTIFY_RUN_CANCELLED,
    ACP_NOTIFY_RUN_COMPLETED,
    ACP_NOTIFY_RUN_FAILED,
    ACP_NOTIFY_RUN_STARTED,
    ACP_NOTIFY_SESSION_PROMPT_UPDATED,
    ACP_NOTIFY_SESSION_UPDATED,
    ACP_NOTIFY_SESSION_UPDATE,
    ACP_NOTIFY_TOOL_UPDATED,
    ACP_NOTIFY_USAGE_UPDATED,
)
from cyrene.agent_runtime.events import event_envelope

# Same secret markers as the core envelope sanitizer, applied recursively so
# nested tool inputs / options can never leak credentials (handoff §19.3).
_SECRET_KEY_PATTERNS = (
    "token",
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "secret",
    "password",
    "credential",
    "cookie",
    "oauth",
    "private_key",
    "privatekey",
)
_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+|authorization\s*[:=]\s*|api[_-]?key\s*[:=]\s*|"
    r"secret\s*[:=]\s*|token\s*[:=]\s*|password\s*[:=]\s*)\S+"
)
_REDACTED_VALUE = "[redacted]"

_TERMINAL_RUN_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
_ACTIVE_TOOL_STATUSES = frozenset({"running", "in_progress", "pending"})
_TOOL_TERMINAL_STATUSES = frozenset({"completed", "failed", "error", "expired", "cancelled"})
_PROMPT_TERMINAL_STATUS = {
    "completed": "run.completed",
    "succeeded": "run.completed",
    "failed": "run.failed",
    "error": "run.failed",
    "cancelled": "run.cancelled",
    "canceled": "run.cancelled",
}


def redact_secrets(value: Any) -> Any:
    """Recursively drop credential-like keys and redact credential-like values.

    Unlike the shallow core sanitizer, this walks nested dicts/lists so ACP
    tool inputs, permission metadata, and artifacts are covered as well.
    """
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
            if (
                not normalized.endswith("tokens")
                and any(re.sub(r"[^a-z0-9]+", "", marker) in normalized for marker in _SECRET_KEY_PATTERNS)
            ):
                continue
            result[str(key)] = redact_secrets(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        if _VALUE_SECRET_PATTERN.search(value):
            return _REDACTED_VALUE
        return value
    return value


def _params(frame: dict[str, Any]) -> dict[str, Any]:
    params = frame.get("params")
    return params if isinstance(params, dict) else {}


def _pick(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _text_of(content: Any) -> str:
    """Join ACP message content parts into plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def _without_inline_media(value: Any) -> Any:
    """Remove inline binary payloads before an ACP event enters run storage.

    Media is materialized by :class:`AcpConnection` and emitted as an Artifact.
    Keeping the original base64 here would duplicate megabytes into the stream,
    durable event store, and browser state.
    """
    if isinstance(value, list):
        return [_without_inline_media(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = _as_str(value.get("type")).lower()
    mime_type = _as_str(
        _pick(value.get("mimeType"), value.get("mime"), value.get("content_type"))
    ).lower()
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"data", "blob"} and (
            bool(mime_type)
            or kind in {"image", "file", "resource", "blob", "audio", "artifact"}
        ):
            continue
        if key in {"url", "uri"} and isinstance(item, str) and item.lower().startswith("data:"):
            continue
        cleaned[str(key)] = _without_inline_media(item)
    return cleaned


def _coerce_options(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    options: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        option_id = _as_str(_pick(item.get("optionId"), item.get("id")))
        if not option_id:
            continue
        option: dict[str, Any] = {
            "id": option_id,
            "label": _as_str(item.get("name") or item.get("label") or item.get("title") or option_id),
        }
        description = _as_str(item.get("description"))
        if description:
            option["description"] = description
        kind = _as_str(item.get("kind"))
        if kind:
            option["kind"] = kind
        options.append(option)
    return options


class AcpEventMapper:
    """Stateful ACP -> unified event normalization for one connection/run.

    Tracks first-seen message/tool ids (to emit ``*_started`` exactly once),
    prompt lifecycle (to emit one terminal ``run.*`` event), and run ids so a
    replayed or duplicate notification cannot duplicate terminal events.
    """

    def __init__(self) -> None:
        self._seen_messages: set[str] = set()
        self._seen_tools: set[str] = set()
        self._completed_tools: set[str] = set()
        self._seen_artifacts: set[str] = set()
        self._seen_prompts: set[str] = set()
        self._terminal_prompts: set[str] = set()
        self._run_started = False
        self._run_terminal = False
        self._run_id = ""

    def normalize(
        self,
        frame: dict[str, Any],
        *,
        agent_id: str = "",
        installation_id: str = "",
        chat_id: str = "",
        run_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        """Normalize one ACP notification frame into zero or more envelopes."""
        if not isinstance(frame, dict):
            return []
        method = _as_str(frame.get("method"))
        params = _params(frame)
        run_id = _as_str(params.get("runId") or params.get("run_id") or run_id)
        context = {
            "agent_id": agent_id,
            "installation_id": installation_id,
            "chat_id": chat_id,
            "run_id": run_id,
            "session_id": session_id,
        }
        handler = {
            ACP_NOTIFY_SESSION_UPDATED: self._session_updated,
            ACP_NOTIFY_SESSION_UPDATE: self._session_update,
            ACP_NOTIFY_SESSION_PROMPT_UPDATED: self._session_prompt_updated,
            ACP_NOTIFY_MESSAGE_UPDATED: self._message_updated,
            ACP_NOTIFY_TOOL_UPDATED: self._tool_updated,
            ACP_NOTIFY_PERMISSION_REQUESTED: self._permission_requested,
            ACP_NOTIFY_ELICITATION_REQUESTED: self._elicitation_requested,
            ACP_NOTIFY_PERMISSION_RESOLVED: self._permission_resolved,
            ACP_NOTIFY_ARTIFACT_UPDATED: self._artifact_updated,
            ACP_NOTIFY_USAGE_UPDATED: self._usage_updated,
            ACP_NOTIFY_RUN_STARTED: self._run_started_notification,
            ACP_NOTIFY_RUN_COMPLETED: lambda p, **c: self._run_terminal_notification(p, method=ACP_NOTIFY_RUN_COMPLETED, **c),
            ACP_NOTIFY_RUN_FAILED: lambda p, **c: self._run_terminal_notification(p, method=ACP_NOTIFY_RUN_FAILED, **c),
            ACP_NOTIFY_RUN_CANCELLED: lambda p, **c: self._run_terminal_notification(p, method=ACP_NOTIFY_RUN_CANCELLED, **c),
        }.get(method)
        if handler is None:
            return []
        events = handler(params, **context)
        if method in {ACP_NOTIFY_RUN_STARTED, ACP_NOTIFY_SESSION_PROMPT_UPDATED}:
            self._run_id = run_id or self._run_id
        return events

    # ------------------------------------------------------------------
    # Per-method handlers
    # ------------------------------------------------------------------

    def _session_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        session = params.get("session") if isinstance(params.get("session"), dict) else {}
        payload: dict[str, Any] = {
            "sessionId": _as_str(_pick(params.get("sessionId"), session.get("id"))),
        }
        metadata = _pick(params.get("metadata"), session.get("metadata"))
        if isinstance(metadata, dict):
            payload["metadata"] = metadata
        return [self._envelope("session.updated", payload, **ctx)]

    def _session_update(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        """Map the official ACP v1 ``session/update`` union."""
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = _as_str(update.get("sessionUpdate"))
        if kind == "agent_message_chunk":
            text = _text_of([update.get("content")])
            return [self._envelope("message.delta", {"delta": text}, **ctx)] if text else []
        if kind == "agent_thought_chunk":
            text = _text_of([update.get("content")])
            return [self._envelope("reasoning.delta", {"delta": text}, **ctx)] if text else []
        if kind in {"tool_call", "tool_call_update"}:
            return self._tool_update(
                update,
                tool_call_id=update.get("toolCallId"),
                **ctx,
            )
        if kind == "usage_update":
            return self._usage_updated({"usage": update}, **ctx)
        if kind in {"session_info_update", "available_commands_update", "current_mode_update", "config_option_update", "plan"}:
            payload: dict[str, Any] = {"updateKind": kind, "update": update}
            if kind == "available_commands_update":
                commands = _pick(update.get("availableCommands"), update.get("commands"))
                payload["commands"] = commands if isinstance(commands, list) else []
            elif kind == "current_mode_update":
                payload["mode"] = _pick(
                    update.get("currentModeId"), update.get("currentMode"), update.get("mode")
                )
            elif kind == "config_option_update":
                options = _pick(update.get("configOptions"), update.get("options"))
                if isinstance(options, list):
                    payload["configOptions"] = [item for item in options if isinstance(item, dict)]
                else:
                    option = _pick(update.get("configOption"), update.get("option"))
                    payload["configOption"] = option if isinstance(option, dict) else update
            elif kind == "plan":
                plan = update.get("plan") if isinstance(update.get("plan"), dict) else update
                plan = dict(plan)
                plan.setdefault("status", "active")
                payload["plan"] = plan
            else:
                info = update.get("sessionInfo") if isinstance(update.get("sessionInfo"), dict) else update
                payload["sessionInfo"] = info
                session_id = _as_str(_pick(info.get("sessionId"), info.get("id"), params.get("sessionId")))
                if session_id:
                    payload["sessionId"] = session_id
            return [self._envelope("session.updated", payload, **ctx)]
        return []

    def permission_request(self, frame: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        """Map the official Agent→Client ``session/request_permission`` request."""
        params = _params(frame)
        tool = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        request_id = frame.get("id")
        payload = {
            "requestId": str(request_id if request_id is not None else ""),
            "title": _as_str(tool.get("title")),
            "description": _as_str(tool.get("title")),
            "toolCallId": _as_str(tool.get("toolCallId")),
            "options": _coerce_options(params.get("options")),
        }
        return [self._envelope("permission.requested", payload, **ctx)]

    def elicitation_request(self, frame: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        """Map the official Agent→Client ``elicitation/create`` request."""
        params = _params(frame)
        request_id = frame.get("id")
        payload: dict[str, Any] = {
            "requestId": str(request_id if request_id is not None else ""),
            "title": _as_str(params.get("message")),
            "description": _as_str(params.get("message")),
        }
        schema = params.get("requestedSchema")
        if isinstance(schema, dict):
            payload["schema"] = schema
        fields = params.get("fields")
        if isinstance(fields, list):
            payload["fields"] = fields
        return [self._envelope("elicitation.requested", payload, **ctx)]

    def _session_prompt_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        prompt = params.get("prompt") if isinstance(params.get("prompt"), dict) else {}
        prompt_id = _as_str(_pick(prompt.get("id"), prompt.get("promptId"), params.get("promptId")))
        status = _as_str(_pick(prompt.get("status"), params.get("status"))).lower()
        events: list[dict[str, Any]] = []
        if not self._run_started:
            self._run_started = True
            events.append(self._envelope("run.started", {"promptId": prompt_id}, **ctx))
        message_text = _pick(prompt.get("message"), prompt.get("partialMessage"), params.get("message"))
        if isinstance(message_text, str) and message_text.strip() and prompt_id:
            events.append(
                self._envelope(
                    "message.delta",
                    {"messageId": prompt_id, "delta": message_text},
                    **ctx,
                )
            )
        tool_calls = prompt.get("toolCalls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if isinstance(tool_call, dict):
                    events.extend(self._tool_update(
                        tool_call,
                        tool_call_id=tool_call.get("toolCallId") or tool_call.get("id"),
                        **ctx,
                    ))
        if status in _PROMPT_TERMINAL_STATUS and prompt_id not in self._terminal_prompts:
            self._terminal_prompts.add(prompt_id)
            terminal_type = _PROMPT_TERMINAL_STATUS[status]
            events.append(self._terminal_run(terminal_type, params, **ctx))
        return events

    def _message_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        message = params.get("message") if isinstance(params.get("message"), dict) else {}
        message_id = _as_str(
            _pick(
                message.get("id"),
                message.get("messageId"),
                params.get("messageId"),
                params.get("id"),
            )
        )
        events: list[dict[str, Any]] = []
        if message_id and message_id not in self._seen_messages:
            self._seen_messages.add(message_id)
            events.append(
                self._envelope(
                    "message.started",
                    {"messageId": message_id, "role": _as_str(message.get("role"))},
                    **ctx,
                )
            )
        delta = _pick(
            params.get("delta"),
            message.get("delta"),
            message.get("partialMessage"),
        )
        if isinstance(delta, dict):
            delta = delta.get("text")
        if isinstance(delta, str) and delta:
            events.append(
                self._envelope(
                    "message.delta",
                    {"messageId": message_id, "delta": delta},
                    **ctx,
                )
            )
            return events
        content = message.get("content")
        text = _text_of(content)
        if isinstance(content, list) and message_id:
            events.append(
                self._envelope(
                    "message.completed",
                    {
                        "messageId": message_id,
                        "role": _as_str(message.get("role")),
                        "text": text,
                    },
                    **ctx,
                )
            )
        return events

    def _tool_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        return self._tool_update(params, tool_call_id=params.get("toolCallId"), **ctx)

    def _tool_update(
        self,
        raw: dict[str, Any],
        *,
        tool_call_id: Any,
        **ctx: Any,
    ) -> list[dict[str, Any]]:
        tool_id = _as_str(_pick(tool_call_id, raw.get("id")))
        status = _as_str(
            _pick(raw.get("toolStatus"), raw.get("status"), raw.get("state"))
        ).lower()
        name = _as_str(_pick(raw.get("toolName"), raw.get("name"), raw.get("title")))
        events: list[dict[str, Any]] = []
        if tool_id and tool_id not in self._seen_tools:
            self._seen_tools.add(tool_id)
            events.append(
                self._envelope(
                    "tool.started",
                    {"toolCallId": tool_id, "name": name, "status": "running"},
                    **ctx,
                )
            )
        payload: dict[str, Any] = {
            "toolCallId": tool_id,
            "name": name,
            "title": _as_str(raw.get("title")),
        }
        presentation = raw.get("presentation") if isinstance(raw.get("presentation"), dict) else {}
        if not presentation and _as_str(raw.get("kind")):
            presentation = {"kind": _as_str(raw.get("kind"))}
        if isinstance(raw.get("locations"), list):
            presentation = {**presentation, "locations": raw.get("locations")}
        if presentation:
            payload["presentation"] = presentation
        tool_input = raw.get("toolInput")
        if tool_input is not None:
            payload["inputSummary"] = tool_input
        progress = raw.get("progress")
        if isinstance(progress, dict):
            payload["progress"] = progress
        output = _pick(raw.get("toolResult"), raw.get("output"), raw.get("content"))
        # ACP Agents may stream partial output while a tool is still running.
        # Output alone is terminal only for legacy adapters that omit status;
        # otherwise an in-progress frame would be frozen as a false failure and
        # the later real ``completed`` update ignored by ``_completed_tools``.
        terminal = status in _TOOL_TERMINAL_STATUSES or (not status and output is not None)
        if terminal:
            if tool_id and tool_id in self._completed_tools:
                return events
            if tool_id:
                self._completed_tools.add(tool_id)
            payload["status"] = "completed" if status in {"completed", "succeeded", ""} else "failed"
            payload["failed"] = status in {"failed", "error", "expired", "cancelled"}
            if output is not None:
                payload["outputSummary"] = _without_inline_media(output)
            events.append(self._envelope("tool.completed", payload, **ctx))
        else:
            payload["status"] = "running" if not status or status in _ACTIVE_TOOL_STATUSES else status
            events.append(self._envelope("tool.updated", payload, **ctx))
        return events

    def _permission_requested(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        request = (
            params.get("permissionRequest")
            if isinstance(params.get("permissionRequest"), dict)
            else {}
        )
        request_id = _as_str(
            _pick(request.get("id"), request.get("requestId"), params.get("requestId"))
        )
        payload: dict[str, Any] = {
            "requestId": request_id,
            "title": _as_str(_pick(request.get("title"), params.get("title"))),
            "description": _as_str(
                _pick(request.get("description"), request.get("message"), params.get("description"))
            ),
            "toolCallId": _as_str(
                _pick(request.get("toolCallId"), params.get("toolCallId"))
            ),
            "options": _coerce_options(
                _pick(request.get("options"), params.get("options"))
            ),
        }
        return [self._envelope("permission.requested", payload, **ctx)]

    def _elicitation_requested(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        request = (
            params.get("elicitationRequest")
            if isinstance(params.get("elicitationRequest"), dict)
            else {}
        )
        payload: dict[str, Any] = {
            "requestId": _as_str(
                _pick(request.get("id"), request.get("requestId"), params.get("requestId"))
            ),
            "title": _as_str(_pick(request.get("title"), params.get("title"))),
            "description": _as_str(
                _pick(request.get("description"), request.get("message"), params.get("description"))
            ),
        }
        fields = request.get("fields")
        if isinstance(fields, list):
            payload["fields"] = fields
        schema = _pick(request.get("requestedSchema"), request.get("schema"), params.get("requestedSchema"))
        if isinstance(schema, dict):
            payload["schema"] = schema
        return [self._envelope("elicitation.requested", payload, **ctx)]

    def _permission_resolved(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        resolution = (
            params.get("permissionResolution")
            if isinstance(params.get("permissionResolution"), dict)
            else {}
        )
        request_id = _as_str(
            _pick(resolution.get("requestId"), params.get("requestId"))
        )
        response = _pick(resolution.get("response"), params.get("response"))
        return [
            self._envelope(
                "permission.resolved",
                {"requestId": request_id, "response": response if isinstance(response, dict) else {}},
                **ctx,
            )
        ]

    def _artifact_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        artifact = params.get("artifact") if isinstance(params.get("artifact"), dict) else {}
        artifact_id = _as_str(_pick(artifact.get("id"), params.get("artifactId"), params.get("id")))
        first = artifact_id not in self._seen_artifacts
        if first:
            self._seen_artifacts.add(artifact_id)
        payload: dict[str, Any] = {"artifactId": artifact_id}
        for key in ("kind", "mimeType", "title", "uri", "state"):
            value = _pick(artifact.get(key), params.get(key))
            if value is not None:
                payload[key] = value
        return [
            self._envelope(
                "artifact.created" if first else "artifact.updated",
                payload,
                **ctx,
            )
        ]

    def _usage_updated(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        usage = params.get("usage") if isinstance(params.get("usage"), dict) else {}
        payload: dict[str, Any] = {}
        for key in (
            "inputTokens", "outputTokens", "totalTokens",
            "inputCacheCreationTokens", "inputCacheReadTokens",
            "used", "size", "cost",
            # Optional capability extension. ACP's standard usage_update gives
            # us used/size; Agents that know more may additionally report a
            # bounded context breakdown using one of these shapes.
            "segments", "context", "contextWindow", "contextComposition",
        ):
            if key in usage:
                payload[key] = usage[key]
        return [self._envelope("usage.updated", payload, **ctx)]

    def _run_started_notification(self, params: dict[str, Any], **ctx: Any) -> list[dict[str, Any]]:
        if self._run_started:
            return []
        self._run_started = True
        return [self._envelope("run.started", {"runId": ctx["run_id"]}, **ctx)]

    def _run_terminal_notification(self, params: dict[str, Any], *, method: str, **ctx: Any) -> list[dict[str, Any]]:
        if self._run_terminal:
            return []
        type_map = {
            ACP_NOTIFY_RUN_COMPLETED: "run.completed",
            ACP_NOTIFY_RUN_FAILED: "run.failed",
            ACP_NOTIFY_RUN_CANCELLED: "run.cancelled",
        }
        event_type = type_map.get(method, "run.completed")
        self._run_terminal = True
        payload: dict[str, Any] = {"runId": ctx["run_id"]}
        error = _pick(params.get("error"), params.get("message"))
        if event_type == "run.failed" and error is not None:
            payload["error"] = error
        return [self._envelope(event_type, payload, **ctx)]

    def _terminal_run(self, event_type: str, params: dict[str, Any], **ctx: Any) -> dict[str, Any]:
        self._run_terminal = True
        payload: dict[str, Any] = {"runId": ctx["run_id"]}
        if event_type == "run.failed":
            error = _pick(params.get("error"), params.get("message"))
            if error is not None:
                payload["error"] = error
        return self._envelope(event_type, payload, **ctx)

    # ------------------------------------------------------------------

    def _envelope(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        agent_id: str,
        installation_id: str,
        chat_id: str,
        run_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        clean_payload = {
            key: value
            for key, value in redact_secrets(payload).items()
            if value is not None and value != ""
        }
        return event_envelope(
            type=event_type,
            payload=clean_payload,
            agent_id=agent_id,
            installation_id=installation_id,
            chat_id=chat_id,
            run_id=run_id,
            session_id=session_id,
            extensions={"acp": {}},
        )

    @property
    def run_terminal(self) -> bool:
        """True once a terminal ``run.*`` event has been emitted."""
        return self._run_terminal


def is_terminal_run_event(event_type: str) -> bool:
    return event_type in _TERMINAL_RUN_EVENTS

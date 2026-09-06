"""Stable, public error details for model-provider failures.

Provider exceptions are intentionally not shown verbatim: SDK messages may contain
endpoints or request fragments.  This module turns them into a small set of safe,
actionable error codes that can cross the Plugin and Workbench boundaries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


_STATUS_RE = re.compile(r"(?:http(?:statuserror)?[^\d]{0,20}|status(?:_code)?[^\d]{0,8})\b([1-5]\d\d)\b", re.I)


ModelRetryScope: TypeAlias = Literal[
    "never",
    "immediate",
    "different_arguments",
    "after_delay",
    "after_config_change",
    "new_run",
]


@dataclass(frozen=True, slots=True)
class ModelErrorDetails:
    code: str
    message_en: str
    message_zh: str
    retryable: bool
    retry_scope: ModelRetryScope = "never"
    status_code: int = 0

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "detail_key": f"workbenchChat.error.{_DETAIL_KEYS[self.code]}",
            "message_en": self.message_en,
            "message_zh": self.message_zh,
            "retryable": self.retryable,
            "retry_scope": self.retry_scope,
        }
        if self.status_code:
            result["status_code"] = self.status_code
        return result


class ModelCallError(RuntimeError):
    """A model failure with safe details for callers outside the Provider."""

    def __init__(
        self,
        details: ModelErrorDetails,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(details.message_en)
        self.details = details
        self.diagnostics = public_stream_diagnostics(diagnostics)

    def as_error_details(self) -> dict[str, Any]:
        result = self.details.as_dict()
        if self.diagnostics:
            result["stream_diagnostics"] = dict(self.diagnostics)
        return result


_PUBLIC_STREAM_FIELDS = frozenset({
    "adapter",
    "provider_error_code",
    "data_chunk_count",
    "event_count",
    "finish_reason",
    "http_status",
    "invalid_json_line_count",
    "last_event_type",
    "line_count",
    "saw_done_marker",
    "stream_completed",
    "terminal_event_seen",
    "termination_reason",
    "transport_error_type",
})
_PUBLIC_TOOL_DIAGNOSTIC_FIELDS = frozenset({
    "arguments_length",
    "arguments_sha256",
    "arguments_validation",
    "index",
    "name",
})


def public_stream_diagnostics(value: Any) -> dict[str, Any]:
    """Keep only content-free protocol evidence at public boundaries."""

    if not isinstance(value, Mapping):
        return {}
    result = {
        str(key): item
        for key, item in value.items()
        if str(key) in _PUBLIC_STREAM_FIELDS
        and isinstance(item, (str, int, float, bool))
    }
    calls: list[dict[str, Any]] = []
    raw_calls = value.get("tool_calls")
    for raw_call in raw_calls if isinstance(raw_calls, list) else ():
        if not isinstance(raw_call, Mapping):
            continue
        calls.append({
            str(key): item
            for key, item in raw_call.items()
            if str(key) in _PUBLIC_TOOL_DIAGNOSTIC_FIELDS
            and isinstance(item, (str, int, float, bool))
        })
    if calls:
        result["tool_calls"] = calls
    return result


_DETAIL_KEYS = {
    "model_not_configured": "modelNotConfigured",
    "model_credentials_missing": "modelAuthenticationFailed",
    "model_authentication_failed": "modelAuthenticationFailed",
    "model_quota_exhausted": "modelQuotaExhausted",
    "model_rate_limited": "modelRateLimited",
    "model_unavailable": "modelUnavailableGeneric",
    "model_request_too_large": "modelRequestTooLarge",
    "model_request_invalid": "modelRequestInvalid",
    "model_timeout": "modelTimeout",
    "model_tls_failed": "modelTlsFailed",
    "model_connection_failed": "modelConnectionFailed",
    "model_service_unavailable": "modelServiceUnavailable",
    "model_response_invalid": "modelResponseInvalid",
    "model_output_truncated": "modelOutputTruncated",
    "model_response_incomplete": "modelResponseIncomplete",
    "model_call_failed": "modelCallFailed",
}

_ERROR_MESSAGES = {
    "model_not_configured": (
        "No model is configured for this conversation.",
        "当前对话尚未配置可用模型。",
    ),
    "model_credentials_missing": (
        "No API key is configured for the model service.",
        "模型服务尚未配置 API 密钥。",
    ),
    "model_authentication_failed": (
        "The model service rejected the configured credentials.",
        "模型服务拒绝了当前凭据。",
    ),
    "model_quota_exhausted": (
        "The model account has no available quota or credit.",
        "模型账户的额度或余额不足。",
    ),
    "model_rate_limited": (
        "The model service is rate limiting requests.",
        "模型服务当前请求过于频繁。",
    ),
    "model_unavailable": (
        "The configured model or endpoint is unavailable.",
        "配置的模型或接口不可用。",
    ),
    "model_request_too_large": (
        "The conversation is larger than the model context window.",
        "当前对话超过了模型的上下文限制。",
    ),
    "model_request_invalid": (
        "The model service rejected the request format or parameters.",
        "模型服务拒绝了请求格式或参数。",
    ),
    "model_timeout": (
        "The model service did not respond in time.",
        "模型服务响应超时。",
    ),
    "model_tls_failed": (
        "The secure connection to the model service could not be verified.",
        "无法验证模型服务的安全连接。",
    ),
    "model_connection_failed": (
        "Cyrene could not connect to the model service.",
        "Cyrene 无法连接到模型服务。",
    ),
    "model_service_unavailable": (
        "The model service is temporarily unavailable.",
        "模型服务暂时不可用。",
    ),
    "model_response_incomplete": (
        "The model response was not fully received. Please retry.",
        "模型响应未完整接收，请重试。",
    ),
    "model_output_truncated": (
        "The model reached its output limit before completing the response. Split the output into smaller calls and retry.",
        "模型输出达到上限，响应未完成。请拆分为更小的调用后重试。",
    ),
    "model_response_invalid": (
        "The model service returned an invalid or empty response.",
        "模型服务返回了无效或空响应。",
    ),
    "model_call_failed": (
        "The model call failed for an unclassified reason.",
        "模型调用因未分类原因失败。",
    ),
}


def _status_code(exc: BaseException, text: str) -> int:
    response = getattr(exc, "response", None)
    try:
        direct = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        direct = 0
    if direct:
        return direct
    match = _STATUS_RE.search(text)
    return int(match.group(1)) if match else 0


def _retry_scope(
    value: Any,
    *,
    retryable: bool,
    default: ModelRetryScope,
) -> ModelRetryScope:
    if not retryable:
        return "never"
    normalized = str(value or "").strip()
    if normalized in {
        "never",
        "immediate",
        "different_arguments",
        "after_delay",
        "after_config_change",
        "new_run",
    }:
        return normalized  # type: ignore[return-value]
    return default


def _details(
    code: str,
    retryable: bool,
    status_code: int,
    *,
    retry_scope: ModelRetryScope | None = None,
) -> ModelErrorDetails:
    message_en, message_zh = _ERROR_MESSAGES[code]
    scope = retry_scope or (
        "immediate"
        if retryable and code == "model_response_invalid"
        else "new_run" if retryable else "never"
    )
    return ModelErrorDetails(
        code,
        message_en,
        message_zh,
        retryable,
        scope,
        status_code,
    )


def classify_model_error(error: BaseException | str) -> ModelErrorDetails:
    exc = error if isinstance(error, BaseException) else RuntimeError(str(error or ""))
    text = str(error or "")
    signature = f"{type(exc).__name__} {text}".lower()
    status = _status_code(exc, signature)

    if "model output truncated" in signature:
        return _details("model_output_truncated", True, status, retry_scope="different_arguments")

    stream_kind = str(getattr(exc, "kind", "") or "")
    if stream_kind == "upstream_incomplete":
        return _details("model_response_incomplete", True, status, retry_scope="immediate")
    diagnostics = getattr(exc, "diagnostics", {})
    if stream_kind == "provider_failed":
        provider_code = diagnostics.get("provider_error_code") if isinstance(diagnostics, Mapping) else None
        code, retryable = {
            "server_error": ("model_service_unavailable", True),
            "rate_limit_exceeded": ("model_rate_limited", True),
            "insufficient_quota": ("model_quota_exhausted", False),
            "invalid_api_key": ("model_authentication_failed", False),
            "authentication_error": ("model_authentication_failed", False),
            "context_length_exceeded": ("model_request_too_large", False),
            "invalid_prompt": ("model_request_invalid", False),
            "invalid_request_error": ("model_request_invalid", False),
            "model_not_found": ("model_unavailable", False),
        }.get(provider_code, ("model_call_failed", True))
        return _details(code, retryable, 0)
    if stream_kind == "output_limit" or (
        stream_kind == "invalid_tool_arguments"
        and isinstance(diagnostics, Mapping)
        and str(diagnostics.get("finish_reason") or "").lower()
        in {"length", "max_tokens", "max_tokens_reached", "max_output_tokens"}
    ):
        return _details("model_output_truncated", True, status, retry_scope="different_arguments")
    if stream_kind == "transport_interrupted":
        return _details("model_connection_failed", True, status)
    if stream_kind in {
        "protocol_invalid_json",
        "protocol_invalid_event",
    }:
        return _details("model_response_invalid", True, status)
    if stream_kind == "invalid_tool_arguments":
        return _details(
            "model_response_invalid",
            True,
            status,
            retry_scope="different_arguments",
        )

    if re.search(r"no model is configured|model is not configured|model configuration is missing", signature):
        return _details("model_not_configured", False, status)

    if re.search(
        r"api[-_ ]?key (?:is )?not configured|missing api[-_ ]?key|credentials? (?:are |is )?not configured",
        signature,
    ):
        return _details("model_credentials_missing", False, status)

    if status in {401, 403} or re.search(r"invalid api key|authentication|unauthori[sz]ed|forbidden|credential", signature):
        return _details("model_authentication_failed", False, status)
    if status == 402 or re.search(r"insufficient (?:credit|fund)|billing|quota (?:exhausted|exceeded)|out of credit", signature):
        return _details("model_quota_exhausted", False, status)
    if status == 429 or re.search(r"rate.?limit|too many requests", signature):
        return _details("model_rate_limited", True, status)
    if status == 404 or re.search(r"model .*(?:not found|does not exist|unavailable)|unknown model", signature):
        return _details("model_unavailable", False, status)
    if status == 413 or re.search(r"context length|context window|too many tokens|request (?:is )?too large|maximum context", signature):
        return _details("model_request_too_large", False, status)
    if status in {400, 409, 415, 422} or re.search(r"invalid request|unsupported (?:parameter|request)|malformed request", signature):
        return _details("model_request_invalid", False, status)
    if status in {408, 504} or re.search(r"timed?\s*out|timeout", signature):
        return _details("model_timeout", True, status)
    if re.search(r"certificate|certificate_verify_failed|tls|ssl", signature):
        return _details("model_tls_failed", False, status)
    if re.search(r"connection refused|connection reset|connecterror|network is unreachable|name or service not known|dns|transporterror", signature):
        return _details("model_connection_failed", True, status)
    if status >= 500 or re.search(r"overloaded|service unavailable|bad gateway|upstream unavailable", signature):
        return _details("model_service_unavailable", True, status)
    if re.search(r"invalid provider plugin result", signature):
        return _details(
            "model_response_invalid",
            True,
            status,
            retry_scope="different_arguments",
        )
    if re.search(r"invalid (?:json|response)|no (?:assistant )?(?:message|response|result)|empty response|decode", signature):
        return _details("model_response_invalid", True, status)
    return _details("model_call_failed", True, status)


def error_details_from_exception(exc: BaseException) -> dict[str, Any]:
    exporter = getattr(exc, "as_error_details", None)
    if callable(exporter):
        exported = exporter()
        if isinstance(exported, Mapping):
            return dict(exported)
    return {}


def details_from_mapping(value: Any) -> ModelErrorDetails | None:
    if not isinstance(value, Mapping):
        return None
    code = str(value.get("code") or value.get("error_code") or "")
    if code == "plugin_timeout":
        retryable = bool(value.get("retryable", True))
        return _details(
            "model_timeout",
            retryable,
            int(value.get("status_code") or 0),
            retry_scope=_retry_scope(
                value.get("retry_scope"),
                retryable=retryable,
                default="after_delay",
            ),
        )
    if code not in _DETAIL_KEYS:
        return None
    retryable = bool(value.get("retryable", True))
    return ModelErrorDetails(
        code,
        str(value.get("message_en") or "The model call failed."),
        str(value.get("message_zh") or "模型调用失败。"),
        retryable,
        _retry_scope(
            value.get("retry_scope"),
            retryable=retryable,
            default=(
                "immediate"
                if code == "model_response_invalid"
                else "new_run"
            ),
        ),
        int(value.get("status_code") or 0),
    )


def preferred_model_error(values: list[ModelErrorDetails]) -> ModelErrorDetails:
    """Choose the most actionable failure after endpoint/model fallbacks."""

    if not values:
        return classify_model_error("")
    priority = {
        "model_not_configured": 0,
        "model_credentials_missing": 0,
        "model_authentication_failed": 1,
        "model_quota_exhausted": 2,
        "model_request_too_large": 2,
        "model_request_invalid": 3,
        "model_unavailable": 4,
        "model_rate_limited": 5,
        "model_tls_failed": 6,
        "model_connection_failed": 7,
        "model_timeout": 8,
        "model_service_unavailable": 9,
        "model_response_invalid": 10,
        "model_call_failed": 11,
    }
    return min(values, key=lambda item: priority.get(item.code, 99))


__all__ = [
    "ModelCallError",
    "ModelErrorDetails",
    "ModelRetryScope",
    "classify_model_error",
    "details_from_mapping",
    "error_details_from_exception",
    "preferred_model_error",
]

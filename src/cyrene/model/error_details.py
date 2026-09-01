"""Stable, public error details for model-provider failures.

Provider exceptions are intentionally not shown verbatim: SDK messages may contain
endpoints or request fragments.  This module turns them into a small set of safe,
actionable error codes that can cross the Plugin and Workbench boundaries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_STATUS_RE = re.compile(r"(?:http(?:statuserror)?[^\d]{0,20}|status(?:_code)?[^\d]{0,8})\b([1-5]\d\d)\b", re.I)


@dataclass(frozen=True, slots=True)
class ModelErrorDetails:
    code: str
    message_en: str
    message_zh: str
    retryable: bool
    status_code: int = 0

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "detail_key": f"workbenchChat.error.{_DETAIL_KEYS[self.code]}",
            "message_en": self.message_en,
            "message_zh": self.message_zh,
            "retryable": self.retryable,
        }
        if self.status_code:
            result["status_code"] = self.status_code
        return result


class ModelCallError(RuntimeError):
    """A model failure with safe details for callers outside the Provider."""

    def __init__(self, details: ModelErrorDetails) -> None:
        super().__init__(details.message_en)
        self.details = details

    def as_error_details(self) -> dict[str, Any]:
        return self.details.as_dict()


_DETAIL_KEYS = {
    "model_not_configured": "modelNotConfigured",
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
    "model_call_failed": "modelCallFailed",
}

_ERROR_MESSAGES = {
    "model_not_configured": (
        "No model is configured for this conversation.",
        "当前对话尚未配置可用模型。",
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


def _details(code: str, retryable: bool, status_code: int) -> ModelErrorDetails:
    message_en, message_zh = _ERROR_MESSAGES[code]
    return ModelErrorDetails(code, message_en, message_zh, retryable, status_code)


def classify_model_error(error: BaseException | str) -> ModelErrorDetails:
    exc = error if isinstance(error, BaseException) else RuntimeError(str(error or ""))
    text = str(error or "")
    signature = f"{type(exc).__name__} {text}".lower()
    status = _status_code(exc, signature)

    if re.search(r"no model is configured|model is not configured|model configuration is missing", signature):
        return _details("model_not_configured", False, status)

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
    if re.search(r"invalid (?:json|response|provider plugin result)|no (?:assistant )?(?:message|response|result)|empty response|decode", signature):
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
        return _details(
            "model_timeout",
            bool(value.get("retryable", True)),
            int(value.get("status_code") or 0),
        )
    if code not in _DETAIL_KEYS:
        return None
    return ModelErrorDetails(
        code,
        str(value.get("message_en") or "The model call failed."),
        str(value.get("message_zh") or "模型调用失败。"),
        bool(value.get("retryable", True)),
        int(value.get("status_code") or 0),
    )


def preferred_model_error(values: list[ModelErrorDetails]) -> ModelErrorDetails:
    """Choose the most actionable failure after endpoint/model fallbacks."""

    if not values:
        return classify_model_error("")
    priority = {
        "model_not_configured": 0,
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
    "classify_model_error",
    "details_from_mapping",
    "error_details_from_exception",
    "preferred_model_error",
]

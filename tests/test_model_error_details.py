from __future__ import annotations

import httpx
import pytest

from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry, PluginRuntime
from cyrene.model.error_details import ModelCallError, classify_model_error


@pytest.mark.parametrize(
    ("message", "code", "retryable"),
    [
        ("HTTP 401 Unauthorized: Invalid API Key", "model_authentication_failed", False),
        ("HTTP 429 Too Many Requests", "model_rate_limited", True),
        ("quota exhausted", "model_quota_exhausted", False),
        ("model not found", "model_unavailable", False),
        ("context length exceeded", "model_request_too_large", False),
        ("connection refused", "model_connection_failed", True),
        ("HTTP 504 gateway timeout", "model_timeout", True),
        ("invalid JSON response", "model_response_invalid", True),
        ("No model is configured", "model_not_configured", False),
    ],
)
def test_model_errors_are_classified_into_actionable_public_codes(
    message: str,
    code: str,
    retryable: bool,
) -> None:
    details = classify_model_error(message)
    assert details.code == code
    assert details.retryable is retryable
    assert details.message_en
    assert details.message_zh


def test_http_status_error_uses_response_status_without_parsing_sdk_text() -> None:
    request = httpx.Request("POST", "https://model.invalid/v1/chat/completions")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("request rejected", request=request, response=response)

    details = classify_model_error(exc)

    assert details.code == "model_authentication_failed"
    assert details.status_code == 401


@pytest.mark.asyncio
async def test_plugin_runtime_preserves_only_explicit_public_error_details() -> None:
    async def failing_model(_arguments, _context):
        raise ModelCallError(classify_model_error("HTTP 401 Unauthorized"))

    registry = PluginRegistry()
    registry.register_plugin(
        Plugin(
            "FailingModel",
            "test",
            {"type": "object"},
            failing_model,
            kind="model",
        ),
        source="test",
    )

    result = await PluginRuntime(registry).call(
        "FailingModel",
        {},
        PluginContext(),
    )

    assert result.success is False
    assert result.error == "Plugin execution failed."
    assert result.error_details["code"] == "model_authentication_failed"
    assert result.error_details["status_code"] == 401

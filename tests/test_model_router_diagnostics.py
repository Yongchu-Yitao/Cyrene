from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from cyrene.core.plugin import Plugin, PluginCallResult, PluginFailure
from cyrene.core.plugin.execution import PluginInvocationError
from cyrene.plugins.model_router import _failed_provider_response
from cyrene.plugins.model_router import _invalid_provider_result_details
from cyrene.plugins.model_router import _normalized_provider_result


def test_failed_provider_response_preserves_malformed_tool_arguments() -> None:
    arguments = '{"operation":"invoke","arguments":{"slideSpecs":['
    provider_result = {
        "content": "Starting the first batch.",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "toolbox",
                "arguments": arguments,
            },
        }],
        "finish_reason": "tool_calls",
        "stream_diagnostics": {
            "adapter": "openai_compatible",
            "event_count": 12,
            "stream_completed": True,
        },
    }

    snapshot = _failed_provider_response(provider_result)

    assert snapshot["normalization_failed"] is True
    assert snapshot["tool_calls"] == provider_result["tool_calls"]
    assert snapshot["finish_reason"] == "tool_calls"
    assert snapshot["stream_diagnostics"] == provider_result["stream_diagnostics"]
    assert snapshot["tool_call_diagnostics"] == [{
        "index": 0,
        "id": "call_1",
        "name": "toolbox",
        "arguments_length": len(arguments),
        "arguments_sha256": hashlib.sha256(arguments.encode("utf-8")).hexdigest(),
    }]


@pytest.mark.asyncio
async def test_truncated_provider_tool_call_is_rejected_before_execution() -> None:
    provider_result = {
        "content": "",
        "tool_calls": [{
            "id": "call-write",
            "name": "Write",
            "arguments": {
                "path": "game.js",
                "content": "const incomplete =",
            },
        }],
        "finish_reason": "length",
        "usage": {"completion_tokens": 4096},
    }
    provider = Plugin(
        name="TestProvider",
        description="test provider",
        input_schema={"type": "object"},
        handler=lambda _arguments, _context: provider_result,
        kind="model",
    )

    with pytest.raises(
        ValueError,
        match="tool calls from a truncated response",
    ):
        await _normalized_provider_result(
            provider_result,
            {"id": "candidate", "model": "test-model"},
            provider,
            [],
        )


def test_provider_parser_retry_scope_survives_router_boundary() -> None:
    failure = PluginFailure(
        error_code="provider_tool_call_invalid",
        message="invalid tool call",
        retryable=True,
        retry_scope="different_arguments",
        circuit_scope="none",
    )
    error = PluginInvocationError(PluginCallResult(
        "parser-call",
        "GenericToolCallParser",
        False,
        None,
        failure.message,
        datetime.now(timezone.utc),
        failure,
    ))

    details = _invalid_provider_result_details(error)

    assert details.code == "model_response_invalid"
    assert details.retryable is True
    assert details.retry_scope == "different_arguments"

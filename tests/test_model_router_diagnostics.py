from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from cyrene.core.plugin import Plugin, PluginCallResult, PluginFailure, PluginRegistry, PluginRuntime
from cyrene.core.plugin.core_impl.write import WRITE_PLUGIN
from cyrene.plugins import ensure_model_router
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
@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "max_tokens_reached", "max_output_tokens"])
@pytest.mark.parametrize("invalid", [False, True])
async def test_length_stop_validates_complete_tool_batch(finish_reason, invalid, tmp_path):
    content = "const value = 1;\n" * 1000
    arguments = json.dumps({"path": str(tmp_path / "game.js"), "content": content})
    calls = [{"id": "write-1", "function": {"name": "Write", "arguments": arguments}}]
    if invalid:
        calls.append({"id": "write-2", "function": {"name": "Write", "arguments": '{"path":'}})
    result = {"content": "", "tool_calls": calls, "finish_reason": finish_reason,
              "usage": {"completion_tokens": 4096},
              "stream_diagnostics": {"finish_reason": finish_reason, "http_status": 200}}
    provider = Plugin(name="TestProvider", description="test provider",
                      input_schema={"type": "object"}, handler=lambda _a, _c: result, kind="model")
    async def normalize(_arguments, _context):
        return await _normalized_provider_result(result, {"id": "candidate", "model": "test-model"}, provider, [])
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(WRITE_PLUGIN, source="test")
    ensure_model_router(registry)
    registry.register_plugin(Plugin(name="Normalize", description="test boundary",
        input_schema={"type": "object"}, handler=normalize), source="test")
    output = await PluginRuntime(registry).call("Normalize", {})
    if invalid:
        assert output.success is False
        assert output.error_details["code"] == "model_output_truncated"
        assert output.error_details["retry_scope"] == "different_arguments"
        assert output.error_details["stream_diagnostics"]["finish_reason"] == finish_reason
    else:
        assert output.success is True, output.error
        assert output.value["tool_calls"][0]["arguments"]["content"] == content
        assert output.value["finish_reason"] == finish_reason
    # Normalization never executes even the valid first call of a failed batch.
    assert not (tmp_path / "game.js").exists()


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

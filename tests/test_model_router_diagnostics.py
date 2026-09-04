from __future__ import annotations

import hashlib

from cyrene.plugins.model_router import _failed_provider_response


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

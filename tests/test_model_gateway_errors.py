from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.core.plugin import Plugin, PluginRegistry, PluginRuntime
from cyrene.core.plugin.model import ModelGatewayError, RuntimeModelGateway
from cyrene.core.plugin.plugin import PluginCallResult
from cyrene.model.error_details import ModelCallError, classify_model_error
from cyrene.plugins.model_gateway import PluginModelGateway


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_type", [RuntimeModelGateway, PluginModelGateway])
async def test_gateway_failure_survives_runtime_and_reaches_consumers(gateway_type):
    from cyrene.plugins.builtin.cyrene_memory.project_memory import _error_type
    from cyrene.workbench.chat.chat_application import chat_error_metadata, chat_run_error_message

    details = ModelCallError(classify_model_error("HTTP status 401"), diagnostics={
        "http_status": 401, "authorization": "secret",
    }).as_error_details()
    result = PluginCallResult("call-1", "provider", False, None, "Provider failed",
                              datetime.now(timezone.utc), error_details=details)
    runtime = SimpleNamespace(call=AsyncMock(return_value=result))
    registry = PluginRegistry(include_core=False)
    gateway = (gateway_type(runtime, "provider") if gateway_type is RuntimeModelGateway
               else gateway_type(registry, runtime))
    with pytest.raises(ModelGatewayError) as caught:
        await gateway.complete([{"role": "user", "content": "test"}])
    error = caught.value
    assert error.call_id == "call-1"
    assert error.as_error_details() == details
    exported = error.as_error_details()
    exported["stream_diagnostics"]["http_status"] = 500
    assert error.as_error_details()["stream_diagnostics"]["http_status"] == 401

    async def fail(_arguments, _context):
        raise error

    registry.register_plugin(Plugin(
        name="outer", description="nested model failure", kind="model",
        input_schema={"type": "object"}, handler=fail,
    ), source="test")
    nested = await PluginRuntime(registry).call("outer", {})
    assert nested.error_details == details
    wrapped = RuntimeError("outer failure")
    wrapped.__cause__ = error
    assert _error_type(wrapped) == "model_authentication_failed"
    assert chat_error_metadata(wrapped)["retry_scope"] == details["retry_scope"]
    assert chat_error_metadata(wrapped)["stream_diagnostics"] == {"http_status": 401}
    assert chat_run_error_message(wrapped, "zh") == details["message_zh"]


@pytest.mark.asyncio
async def test_plain_gateway_failure_keeps_runtime_error_compatibility():
    result = PluginCallResult("id", "provider", False, None, "old message",
                              datetime.now(timezone.utc))
    gateway = RuntimeModelGateway(SimpleNamespace(call=AsyncMock(return_value=result)), "provider")
    with pytest.raises(RuntimeError, match="old message") as caught:
        await gateway.complete([])
    assert caught.value.as_error_details()["code"] == "model_call_failed"

import json

import httpx
import pytest

from cyrene.model.error_details import ModelCallError, classify_model_error
from cyrene.model.protocol_adapters import PreparedRequest, handle_stream


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b"not JSON: secret",
    b"x" * 20000,
    json.dumps({"error": {"code": "secret", "param": "secret", "message": "secret"}}).encode(),
])
async def test_http_error_retains_status_without_leaking_body(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, content=body)
    )) as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await handle_stream("openai_responses", client, "https://provider.test/responses",
                                PreparedRequest({}, {}), None)
    assert caught.value.response.status_code == 400
    assert caught.value.diagnostics["termination_reason"] == "http_rejected"
    assert "secret" not in str(caught.value.diagnostics)


@pytest.mark.asyncio
async def test_schema_rejection_survives_public_error_boundary_without_provider_prose():
    body = {"error": {
        "code": "invalid_function_parameters", "param": "tools[15].parameters",
        "message": "private prompt/key: 'uniqueItems' is not permitted.",
    }}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json=body)
    )) as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await handle_stream("openai_responses", client, "https://provider.test/responses",
                                PreparedRequest({}, {}), None)
    error = ModelCallError(classify_model_error(caught.value), diagnostics=caught.value.diagnostics)
    details = error.as_error_details()
    assert details["code"] == "model_request_invalid"
    assert details["stream_diagnostics"]["provider_error_code"] == "invalid_function_parameters"
    assert details["stream_diagnostics"]["provider_error_param"] == "tools[15].parameters"
    assert details["stream_diagnostics"]["provider_error_reason"] == "unsupported_schema_keyword:uniqueItems"
    assert "private" not in str(details)

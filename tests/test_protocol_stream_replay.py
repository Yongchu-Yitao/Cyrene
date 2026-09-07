"""Golden wire events and results captured from the pre-refactor implementation."""
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest

from cyrene.model.protocol_adapters import ModelStreamError, PreparedRequest, handle_stream

CASES = json.loads((Path(__file__).parent / "fixtures/protocol-stream-refactor.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[f'{i}-{case["adapter"]}' for i, case in enumerate(CASES)])
async def test_protocol_replay_matches_pre_refactor_callback_order_and_result(case, monkeypatch):
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex="0" * 32))
    events = []

    async def callback(event):
        events.append(event)

    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in case["chunks"]) + "data: [DONE]\n\n"

    async def handler(_request):
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            result = await handle_stream(case["adapter"], client, "https://fixture.invalid/generateContent",
                                         PreparedRequest({}, {}), callback if case["callback"] else None)
        except ModelStreamError as exc:
            result = {"error": exc.kind, "message": str(exc), "diagnostics": exc.diagnostics}
    assert {"result": result, "events": events} == case["expected"]

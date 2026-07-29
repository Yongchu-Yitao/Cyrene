from __future__ import annotations

import base64
from pathlib import Path

import pytest


def _image_attachment(path: Path) -> dict:
    return {
        "id": "image-1",
        "name": "sample.png",
        "path": str(path),
        "content_type": "image/png",
        "size": path.stat().st_size,
        "kind": "image",
    }


@pytest.mark.asyncio
async def test_workbench_sends_images_directly_to_vision_capable_primary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cyrene.model_runtime import client as model_client
    from cyrene.workbench import runtime

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"image-bytes")
    captured: dict = {}

    async def no_budget_gate(_session_id: str) -> str:
        return ""

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(runtime, "_check_budget_gate", no_budget_gate)
    monkeypatch.setattr(runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        model_client,
        "primary_candidate_supports_vision",
        lambda _session_id="": True,
    )

    result = await runtime._workbench_agent_reply(
        "What is shown?",
        {"id": "chat-vision"},
        [],
        attachments=[_image_attachment(image_path)],
    )

    assert result == "done"
    assert "AnalyzeAttachment" not in captured["user_message"]
    assert captured["llm_user_content"][0] == {
        "type": "text",
        "text": "What is shown?",
    }
    assert captured["llm_user_content"][1]["type"] == "image_url"
    encoded = captured["llm_user_content"][1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"image-bytes"


@pytest.mark.asyncio
async def test_workbench_uses_analyze_attachment_when_primary_lacks_vision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cyrene.model_runtime import client as model_client
    from cyrene.workbench import runtime

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"image-bytes")
    captured: dict = {}

    async def no_budget_gate(_session_id: str) -> str:
        return ""

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(runtime, "_check_budget_gate", no_budget_gate)
    monkeypatch.setattr(runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        model_client,
        "primary_candidate_supports_vision",
        lambda _session_id="": False,
    )

    result = await runtime._workbench_agent_reply(
        "What is shown?",
        {"id": "chat-text-only"},
        [],
        attachments=[_image_attachment(image_path)],
    )

    assert result == "done"
    assert "AnalyzeAttachment" in captured["user_message"]
    assert captured["llm_user_content"] is None

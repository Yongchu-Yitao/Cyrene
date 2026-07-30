from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cyrene.model_runtime import image_generation
from cyrene.model_runtime.image_generation import (
    GeneratedImage,
    ImageGenerationError,
)
from cyrene.tooling import catalog, wire
from cyrene.tool_impl.image import generate_image as generate_image_tool


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
        "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )


def test_generate_image_tool_is_visible_only_for_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.runtime import settings_store

    monkeypatch.setattr(
        settings_store,
        "get_models",
        lambda: [{"provider": "openai_compatible", "model": "gpt-5.6"}],
    )
    custom_wire_names = {
        item["function"]["name"] for item in wire.get_main_wire_tool_defs()
    }
    custom_catalog_names = {
        item["function"]["name"]
        for item in catalog.get_active_tool_defs_for_actor("main")
    }
    assert "GenerateImage" not in custom_wire_names
    assert "GenerateImage" not in custom_catalog_names

    monkeypatch.setattr(
        settings_store,
        "get_models",
        lambda: [{"provider": "codex_oauth", "model": "gpt-5.6-sol"}],
    )
    oauth_wire_names = {
        item["function"]["name"] for item in wire.get_main_wire_tool_defs()
    }
    oauth_catalog_names = {
        item["function"]["name"]
        for item in catalog.get_active_tool_defs_for_actor("main")
    }
    assert "GenerateImage" in oauth_wire_names
    assert "GenerateImage" in oauth_catalog_names


@pytest.mark.asyncio
async def test_custom_openai_api_path_is_rejected_without_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def should_not_generate(*args: Any, **kwargs: Any):
        raise AssertionError("custom OpenAI API path must not generate")

    monkeypatch.setattr(
        image_generation,
        "_generate_with_codex",
        should_not_generate,
    )
    monkeypatch.setattr(
        image_generation,
        "_primary_candidate",
        lambda: {
            "provider": "openai_compatible",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "key",
        },
    )

    with pytest.raises(
        ImageGenerationError,
        match="only when the primary model uses OpenAI OAuth",
    ):
        await image_generation.generate_image(prompt="Draw a cat")


@pytest.mark.asyncio
async def test_codex_oauth_image_generation_needs_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_generate_with_codex(
        candidate: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[bytes, str, str]:
        assert candidate["provider"] == "codex_oauth"
        assert candidate.get("api_key", "") == ""
        assert kwargs["prompt"] == "Draw an otter"
        return _png_bytes(), "gpt-5.6-sol", ""

    monkeypatch.setattr(
        image_generation,
        "_generate_with_codex",
        fake_generate_with_codex,
    )
    monkeypatch.setattr(
        image_generation,
        "_validated_image_format",
        lambda image_bytes: "png" if image_bytes == _png_bytes() else "invalid",
    )
    monkeypatch.setattr(image_generation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        image_generation,
        "_primary_candidate",
        lambda: {
            "provider": "codex_oauth",
            "model": "gpt-5.6-sol",
            "base_url": "codex://oauth",
            "api_key": "",
        },
    )

    generated = await image_generation.generate_image(prompt="Draw an otter")

    assert generated.provider == "codex_oauth"
    assert generated.model == "gpt-5.6-sol"
    assert generated.path.read_bytes() == _png_bytes()


@pytest.mark.asyncio
async def test_generate_image_tool_delivers_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temporary = tmp_path / "temporary.png"
    temporary.write_bytes(_png_bytes())

    async def fake_generate_image(**kwargs: Any) -> GeneratedImage:
        assert kwargs["prompt"] == "Draw an otter"
        return GeneratedImage(
            path=temporary,
            provider="codex_oauth",
            model="gpt-5.6-sol",
            revised_prompt="An otter",
        )

    async def fake_send_file(
        args: dict[str, Any],
        _bot: Any,
        _chat_id: int,
        _db_path: str,
        _notify_state: dict[str, bool] | None,
    ) -> str:
        assert args["path"] == str(temporary)
        assert args["name"] == "otter.png"
        return json.dumps(
            {
                "status": "sent",
                "attachment": {"name": "otter.png", "kind": "image"},
            }
        )

    monkeypatch.setattr(generate_image_tool, "generate_image", fake_generate_image)
    monkeypatch.setitem(
        catalog.TOOL_HANDLERS,
        "send_file",
        fake_send_file,
    )

    result = await generate_image_tool._tool_generate_image(
        {"prompt": "Draw an otter", "name": "otter.png"},
        None,
        0,
        "",
        {},
    )
    payload = json.loads(result)

    assert payload["status"] == "sent"
    assert payload["generation"] == {
        "provider": "codex_oauth",
        "model": "gpt-5.6-sol",
        "revised_prompt": "An otter",
    }
    assert not temporary.exists()

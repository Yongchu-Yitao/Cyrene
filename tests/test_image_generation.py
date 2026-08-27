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
from agent.plugin.plugin_impl.cyrene_image import generate_image as generate_image_tool


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
        "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )


def test_generate_image_plugin_is_hidden_from_the_model_catalog() -> None:
    assert generate_image_tool.plugin.metadata["model_visible"] is False


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
@pytest.mark.parametrize(
    ("quality", "expected_timeout"),
    [
        ("high", 300.0),
        ("medium", 180.0),
        ("low", 180.0),
        ("auto", 180.0),
    ],
)
async def test_codex_oauth_image_generation_timeout_depends_on_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    quality: str,
    expected_timeout: float,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_generate_with_codex(
        candidate: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[bytes, str, str]:
        seen.update(candidate=candidate, **kwargs)
        return _png_bytes(), "gpt-5.6-sol", ""

    monkeypatch.setattr(
        image_generation,
        "_generate_with_codex",
        fake_generate_with_codex,
    )
    monkeypatch.setattr(
        image_generation,
        "_validated_image_format",
        lambda _image_bytes: "png",
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

    generated = await image_generation.generate_image(
        prompt="Draw an otter",
        quality=quality,
    )

    assert seen["candidate"]["provider"] == "codex_oauth"
    assert seen["timeout"] == expected_timeout
    generated.path.unlink()


def test_generate_image_plugin_has_an_extended_timeout() -> None:
    assert generate_image_tool.plugin.timeout_seconds == 420.0


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

    async def fake_invoke_plugin(
        name: str,
        args: dict[str, Any],
        *,
        review: bool,
    ) -> str:
        assert name == "send_file"
        assert review is False
        assert args["path"] == str(temporary)
        assert args["name"] == "otter.png"
        return json.dumps(
            {
                "status": "sent",
                "attachment": {"name": "otter.png", "kind": "image"},
            }
        )

    monkeypatch.setattr(generate_image_tool, "generate_image", fake_generate_image)
    monkeypatch.setattr(generate_image_tool, "invoke_plugin", fake_invoke_plugin)

    result = await generate_image_tool._tool_generate_image(
        {"prompt": "Draw an otter", "name": "otter.png"},
        None,
    )
    payload = json.loads(result)

    assert payload["status"] == "sent"
    assert payload["generation"] == {
        "provider": "codex_oauth",
        "model": "gpt-5.6-sol",
        "revised_prompt": "An otter",
    }
    assert not temporary.exists()

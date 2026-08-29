from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cyrene.plugins.builtin.cyrene_media.models import MediaArtifact, MediaProviderError


async def _progress(
    _message: str,
    _provider_job_id: str,
    _state: dict[str, Any] | None,
) -> None:
    return None


def _install_openai_client(monkeypatch, payload: dict[str, Any]):
    from cyrene.plugins.builtin.cyrene_media.providers import openai_image

    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            calls.append((url, kwargs))
            return httpx.Response(200, json=payload)

    monkeypatch.setattr(openai_image.httpx, "AsyncClient", Client)
    return calls


def test_resolve_provider_explicit_auto_disabled_and_kind_contracts():
    from cyrene.plugins.builtin.cyrene_media.providers.registry import resolve_provider

    settings = {
        "default_providers": {"image": "auto", "video": "auto", "music": "auto"},
        "providers": {
            "openai": {"enabled": True, "api_key": "openai-key"},
            "google": {
                "enabled": True,
                "api_key": "google-key",
                "video_model": "gemini-omni-flash-preview",
            },
            "minimax": {"enabled": True, "api_key": "minimax-key"},
            "seedream": {"enabled": False},
            "seedance": {"enabled": False},
            "comfyui": {"enabled": False},
        },
    }

    name, provider = resolve_provider("gpt-image", "image", settings)
    assert name == "openai"
    assert provider.name == "openai"
    assert resolve_provider("auto", "image", settings)[0] == "openai"
    assert resolve_provider("auto", "video", settings)[0] == "google"
    assert resolve_provider("auto", "music", settings)[0] == "minimax"

    with pytest.raises(MediaProviderError) as disabled:
        resolve_provider("seedream", "image", settings)
    assert disabled.value.code == "media_provider_disabled"

    with pytest.raises(MediaProviderError) as wrong_kind:
        resolve_provider("openai", "video", settings)
    assert wrong_kind.value.code == "unsupported_provider_kind"

    with pytest.raises(MediaProviderError) as invalid_kind:
        resolve_provider("auto", "document", settings)
    assert invalid_kind.value.code == "unsupported_kind"

    settings["default_providers"]["image"] = "google"
    assert resolve_provider("auto", "image", settings)[0] == "google"


@pytest.mark.asyncio
async def test_openai_gpt_image_generation_decodes_b64_and_uses_generation_endpoint(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.openai_image import OpenAIImageProvider

    image_bytes = b"generated-png"
    calls = _install_openai_client(
        monkeypatch,
        {
            "id": "image-request-1",
            "data": [
                {
                    "b64_json": base64.b64encode(image_bytes).decode("ascii"),
                    "revised_prompt": "a revised prompt",
                }
            ],
            "usage": {"total_tokens": 12},
        },
    )

    result = await OpenAIImageProvider().generate(
        {
            "kind": "image",
            "prompt": "a luminous paper crane",
            "number_of_outputs": 1,
            "size": "1536x1024",
        },
        {
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "image_model": "gpt-image-2",
        },
        _progress,
    )

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/images/generations"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"] == {
        "model": "gpt-image-2",
        "prompt": "a luminous paper crane",
        "n": 1,
        "size": "1536x1024",
        "quality": "auto",
        "output_format": "png",
    }
    assert result.provider_job_id == "image-request-1"
    assert result.artifacts[0].data == image_bytes
    assert result.artifacts[0].content_type == "image/png"
    assert result.metadata["model"] == "gpt-image-2"
    assert result.metadata["revised_prompts"] == ["a revised prompt"]


@pytest.mark.asyncio
async def test_openai_gpt_image_edit_uses_multipart_reference_and_mask_fields(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.openai_image import OpenAIImageProvider

    first_reference = tmp_path / "first.png"
    second_reference = tmp_path / "second.webp"
    mask = tmp_path / "mask.png"
    first_reference.write_bytes(b"first-image")
    second_reference.write_bytes(b"second-image")
    mask.write_bytes(b"mask-image")
    calls = _install_openai_client(
        monkeypatch,
        {"data": [{"b64_json": base64.b64encode(b"edited-image").decode("ascii")}]},
    )

    result = await OpenAIImageProvider().generate(
        {
            "kind": "image",
            "prompt": "put both cranes over a lake",
            "reference_paths": [str(first_reference), str(second_reference)],
            "mask_path": str(mask),
        },
        {
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "image_model": "gpt-image-2",
        },
        _progress,
    )

    assert result.artifacts[0].data == b"edited-image"
    url, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/images/edits"
    assert kwargs["data"]["model"] == "gpt-image-2"
    assert "input_fidelity" not in kwargs["data"]
    assert [field for field, _file in kwargs["files"]] == [
        "image[]",
        "image[]",
        "mask",
    ]
    assert [file[0] for _field, file in kwargs["files"]] == [
        "first.png",
        "second.webp",
        "mask.png",
    ]
    assert [file[1] for _field, file in kwargs["files"]] == [
        b"first-image",
        b"second-image",
        b"mask-image",
    ]


@pytest.mark.asyncio
async def test_openai_gpt_image_2_rejects_explicit_input_fidelity(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.openai_image import OpenAIImageProvider

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference-image")
    calls = _install_openai_client(monkeypatch, {"data": []})

    with pytest.raises(MediaProviderError) as rejected:
        await OpenAIImageProvider().generate(
            {
                "kind": "image",
                "prompt": "preserve the crane exactly",
                "reference_paths": [str(reference)],
                "input_fidelity": "high",
            },
            {
                "api_key": "test-key",
                "base_url": "https://api.openai.com/v1",
                "image_model": "gpt-image-2",
            },
            _progress,
        )

    assert rejected.value.code == "openai_unsupported_parameter"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_values", "expected_code"),
    [
        ({"mask_path": "missing-mask.png"}, "openai_invalid_mask"),
        ({"output_format": "gif"}, "openai_invalid_output_format"),
    ],
)
async def test_openai_rejects_invalid_mask_and_output_format_before_submission(
    request_values,
    expected_code,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.openai_image import OpenAIImageProvider

    calls = _install_openai_client(monkeypatch, {"data": []})

    with pytest.raises(MediaProviderError) as rejected:
        await OpenAIImageProvider().generate(
            {
                "kind": "image",
                "prompt": "a paper crane",
                **request_values,
            },
            {"api_key": "test-key", "image_model": "gpt-image-2"},
            _progress,
        )

    assert rejected.value.code == expected_code
    assert calls == []


@pytest.mark.asyncio
async def test_minimax_music_accepts_inline_hex_and_remote_url_outputs(monkeypatch):
    from cyrene.plugins.builtin.cyrene_media.providers import minimax
    from cyrene.plugins.builtin.cyrene_media.providers.minimax import MiniMaxProvider

    responses = [
        {
            "trace_id": "trace-hex",
            "data": {"audio": b"inline-audio".hex()},
        },
        {
            "trace_id": "trace-url",
            "data": {"audio_url": "https://media.example.test/song.mp3"},
        },
    ]
    requests: list[dict[str, Any]] = []
    downloaded: list[str] = []

    async def request_json(_method: str, url: str, **kwargs: Any):
        requests.append({"url": url, **kwargs})
        return responses.pop(0)

    async def artifact_from_url(url: str, **_kwargs: Any):
        downloaded.append(url)
        return MediaArtifact(
            filename="song.mp3",
            content_type="audio/mpeg",
            data=b"downloaded-audio",
        )

    monkeypatch.setattr(minimax, "request_json", request_json)
    monkeypatch.setattr(minimax, "artifact_from_url", artifact_from_url)
    provider = MiniMaxProvider()
    settings = {
        "api_key": "test-key",
        "base_url": "https://api.minimax.io",
        "music_model": "music-3.0",
    }

    inline = await provider.generate(
        {"kind": "music", "prompt": "quiet piano", "output_format": "wav"},
        settings,
        _progress,
    )
    remote = await provider.generate(
        {"kind": "music", "lyrics": "hello from the lake"},
        settings,
        _progress,
    )

    assert inline.provider_job_id == "trace-hex"
    assert inline.artifacts[0].data == b"inline-audio"
    assert inline.artifacts[0].content_type == "audio/wav"
    assert remote.provider_job_id == "trace-url"
    assert remote.artifacts[0].data == b"downloaded-audio"
    assert downloaded == ["https://media.example.test/song.mp3"]
    assert [request["url"] for request in requests] == [
        "https://api.minimax.io/v1/music_generation",
        "https://api.minimax.io/v1/music_generation",
    ]
    assert all(request["payload"]["model"] == "music-3.0" for request in requests)
    assert all(request["payload"]["output_format"] == "url" for request in requests)


@pytest.mark.asyncio
async def test_google_image_provider_uses_sdk_and_normalizes_inline_bytes(monkeypatch):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    captured: dict[str, Any] = {}

    class FakePart:
        @staticmethod
        def from_text(*, text: str):
            return {"text": text}

        @staticmethod
        def from_bytes(*, data: bytes, mime_type: str):
            return {"data": data, "mime_type": mime_type}

    class Types:
        Part = FakePart

        @staticmethod
        def Content(**kwargs: Any):
            return kwargs

    class Models:
        def generate_content(self, **kwargs: Any):
            captured.update(kwargs)
            return SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        inline_data=SimpleNamespace(
                            data=b"google-image",
                            mime_type="image/webp",
                        )
                    )
                ],
                response_id="google-response-1",
                model_version="gemini-image-version",
                usage_metadata={"total_token_count": 7},
                text="",
            )

    class Client:
        def __init__(self) -> None:
            self.models = Models()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, Types))

    result = await provider.generate(
        {
            "kind": "image",
            "prompt": "a watercolor lighthouse",
            "aspect_ratio": "16:9",
            "resolution": "2K",
        },
        {
            "api_key": "test-google-key",
            "image_model": "gemini-3.1-flash-image",
        },
        _progress,
    )

    assert captured["model"] == "gemini-3.1-flash-image"
    assert captured["contents"] == [{"role": "user", "parts": [{"text": "a watercolor lighthouse"}]}]
    assert captured["config"] == {
        "response_modalities": ["TEXT", "IMAGE"],
        "image_config": {"aspect_ratio": "16:9", "image_size": "2K"},
    }
    assert result.provider_job_id == "google-response-1"
    assert result.artifacts[0].data == b"google-image"
    assert result.artifacts[0].content_type == "image/webp"
    assert result.metadata["usage"] == {"total_token_count": 7}
    assert client.closed is True


def _completed_omni_interaction(
    interaction_id: str,
    video: bytes = b"omni-video",
):
    return SimpleNamespace(
        id=interaction_id,
        status="completed",
        output_video=SimpleNamespace(
            data=base64.b64encode(video).decode("ascii"),
            mime_type="video/mp4",
        ),
        usage={"generated_videos": 1},
    )


@pytest.mark.asyncio
async def test_google_omni_submits_local_reference_image_offline(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"omni-reference-image")
    created: list[dict[str, Any]] = []

    class Interactions:
        def create(self, **kwargs: Any):
            created.append(kwargs)
            return _completed_omni_interaction("interaction-image")

        def get(self, _interaction_id: str):
            raise AssertionError("completed interaction must not be polled")

    class Files:
        def upload(self, **_kwargs: Any):
            raise AssertionError("image references must not use the Files API")

    class Client:
        def __init__(self) -> None:
            self.interactions = Interactions()
            self.files = Files()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, object()))

    result = await provider.generate(
        {
            "kind": "video",
            "prompt": "animate the crane",
            "reference_paths": [str(reference)],
            "reference_roles": ["reference_image"],
            "aspect_ratio": "16:9",
            "resolution": "1080p",
            "duration": 12,
        },
        {
            "api_key": "test-google-key",
            "video_model": "gemini-omni-flash-preview",
        },
        _progress,
    )

    assert len(created) == 1
    assert created[0]["model"] == "gemini-omni-flash-preview"
    assert created[0]["input"] == [
        {
            "type": "image",
            "data": base64.b64encode(b"omni-reference-image").decode("ascii"),
            "mime_type": "image/png",
        },
        {"type": "text", "text": "animate the crane"},
    ]
    assert created[0]["response_format"] == {
        "type": "video",
        "delivery": "uri",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "duration": "12s",
    }
    assert created[0]["generation_config"] == {"video_config": {"task": "reference_to_video"}}
    assert result.provider_job_id == "interaction-image"
    assert result.artifacts[0].data == b"omni-video"
    assert client.closed is True


@pytest.mark.asyncio
async def test_google_omni_uploads_one_local_reference_video_offline(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"omni-reference-video")
    uploads: list[str] = []
    created: list[dict[str, Any]] = []

    class Files:
        def upload(self, *, file: str):
            uploads.append(file)
            return SimpleNamespace(
                name="files/reference-video",
                uri="https://files.example.test/reference-video",
                state="ACTIVE",
            )

        def get(self, **_kwargs: Any):
            raise AssertionError("an active upload must not be polled")

    class Interactions:
        def create(self, **kwargs: Any):
            created.append(kwargs)
            return _completed_omni_interaction("interaction-video")

        def get(self, _interaction_id: str):
            raise AssertionError("completed interaction must not be polled")

    class Client:
        def __init__(self) -> None:
            self.files = Files()
            self.interactions = Interactions()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, object()))

    result = await provider.generate(
        {
            "kind": "video",
            "prompt": "restyle this shot",
            "reference_paths": [str(reference)],
            "reference_roles": ["reference_video"],
        },
        {
            "api_key": "test-google-key",
            "video_model": "gemini-omni-flash-preview",
        },
        _progress,
    )

    assert uploads == [str(reference.resolve())]
    assert created[0]["input"] == [
        {
            "type": "document",
            "uri": "https://files.example.test/reference-video",
        },
        {"type": "text", "text": "restyle this shot"},
    ]
    assert created[0]["generation_config"] == {"video_config": {"task": "edit"}}
    assert result.provider_job_id == "interaction-video"
    assert result.artifacts[0].data == b"omni-video"
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("remote_video", "google_unsupported_reference"),
        ("mixed_image_video", "google_invalid_references"),
        ("two_videos", "google_unsupported_reference"),
        ("audio", "google_unsupported_reference"),
    ],
)
async def test_google_omni_rejects_unsupported_reference_combinations_offline(
    case,
    expected_code,
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    image = tmp_path / "reference.png"
    first_video = tmp_path / "first.mp4"
    second_video = tmp_path / "second.mp4"
    audio = tmp_path / "sound.mp3"
    image.write_bytes(b"image")
    first_video.write_bytes(b"video-one")
    second_video.write_bytes(b"video-two")
    audio.write_bytes(b"audio")
    cases = {
        "remote_video": (
            ["https://media.example.test/reference.mp4"],
            ["reference_video"],
        ),
        "mixed_image_video": (
            [str(image), str(first_video)],
            ["reference_image", "reference_video"],
        ),
        "two_videos": (
            [str(first_video), str(second_video)],
            ["reference_video", "reference_video"],
        ),
        "audio": ([str(audio)], ["reference_audio"]),
    }
    references, roles = cases[case]
    submissions: list[dict[str, Any]] = []

    class Interactions:
        def create(self, **kwargs: Any):
            submissions.append(kwargs)
            raise AssertionError("invalid references must not be submitted")

    class Files:
        def upload(self, **_kwargs: Any):
            raise AssertionError("invalid references must not be uploaded")

    class Client:
        def __init__(self) -> None:
            self.interactions = Interactions()
            self.files = Files()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, object()))

    with pytest.raises(MediaProviderError) as rejected:
        await provider.generate(
            {
                "kind": "video",
                "prompt": "animate references",
                "reference_paths": references,
                "reference_roles": roles,
            },
            {
                "api_key": "test-google-key",
                "video_model": "gemini-omni-flash-preview",
            },
            _progress,
        )

    assert rejected.value.code == expected_code
    assert submissions == []
    assert client.closed is True


@pytest.mark.asyncio
async def test_google_omni_resume_does_not_reupload_original_reference_video(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"original-video")
    fetched: list[str] = []

    class Files:
        def upload(self, **_kwargs: Any):
            raise AssertionError("resume must not upload the reference again")

    class Interactions:
        def create(self, **_kwargs: Any):
            raise AssertionError("resume must not create a second interaction")

        def get(self, interaction_id: str):
            fetched.append(interaction_id)
            return _completed_omni_interaction(
                interaction_id,
                b"resumed-omni-video",
            )

    class Client:
        def __init__(self) -> None:
            self.files = Files()
            self.interactions = Interactions()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, object()))

    result = await provider.generate(
        {
            "kind": "video",
            "prompt": "continue the recovered interaction",
            "reference_paths": [str(reference)],
            "reference_roles": ["reference_video"],
            "_resume_provider_job_id": "interaction-existing",
            "_resume_provider_state": {
                "api_kind": "interactions",
                "uploaded_file": "files/original-video",
            },
        },
        {
            "api_key": "test-google-key",
            "video_model": "gemini-omni-flash-preview",
        },
        _progress,
    )

    assert fetched == ["interaction-existing"]
    assert result.provider_job_id == "interaction-existing"
    assert result.artifacts[0].data == b"resumed-omni-video"
    assert client.closed is True


def test_comfyui_raw_mcp_structured_image_and_resource_blocks_are_normalized():
    from cyrene.plugins.builtin.cyrene_media.providers.comfyui import (
        _inline_artifacts,
        _job_id,
        _status,
        _unwrap,
    )

    raw = {
        "_cyrene_mcp_raw": True,
        "structured_content": {
            "prompt_id": "comfy-prompt-1",
            "status": "completed",
        },
        "content": [
            {"type": "text", "text": "fallback text"},
            {
                "type": "image",
                "data": base64.b64encode(b"inline-image").decode("ascii"),
                "mimeType": "image/png",
            },
            {
                "type": "resource",
                "resource": {
                    "blob": base64.b64encode(b"resource-image").decode("ascii"),
                    "mimeType": "image/webp",
                    "uri": "resource://comfy/output.webp",
                },
            },
        ],
    }

    structured, blocks = _unwrap(raw)
    assert structured == {"prompt_id": "comfy-prompt-1", "status": "completed"}
    assert blocks == raw["content"]
    assert _job_id(raw) == "comfy-prompt-1"
    assert _status(raw) == "completed"

    artifacts = _inline_artifacts(blocks, kind="image", maximum=1024)
    assert [artifact.data for artifact in artifacts] == [
        b"inline-image",
        b"resource-image",
    ]
    assert [artifact.content_type for artifact in artifacts] == [
        "image/png",
        "image/webp",
    ]

    music = _inline_artifacts(
        [
            {
                "type": "resource",
                "resource": {
                    "blob": base64.b64encode(b"resource-audio").decode("ascii"),
                    "mimeType": "audio/mpeg",
                },
            }
        ],
        kind="music",
        maximum=1024,
    )
    assert music[0].data == b"resource-audio"
    assert music[0].content_type == "audio/mpeg"


def test_comfyui_workflow_template_injects_typed_request_and_staged_inputs():
    from cyrene.plugins.builtin.cyrene_media.providers.comfyui import _render_workflow

    rendered = _render_workflow(
        {
            "positive": {"inputs": {"text": "{{prompt}}"}},
            "negative": {"inputs": {"text": "avoid {{negative_prompt}}"}},
            "sampler": {
                "inputs": {
                    "seed": "{{seed}}",
                    "steps": "{{parameter.steps}}",
                }
            },
            "reference": {"inputs": {"image": "{{reference_1}}"}},
            "mask": {"inputs": {"image": "{{mask}}"}},
        },
        {
            "prompt": "a crane in morning mist",
            "negative_prompt": "low contrast",
            "seed": 12345,
            "parameters": {"steps": 28},
        },
        staged_references=["cyrene-reference.png"],
        staged_mask="cyrene-mask.png",
    )

    assert rendered == {
        "positive": {"inputs": {"text": "a crane in morning mist"}},
        "negative": {"inputs": {"text": "avoid low contrast"}},
        "sampler": {"inputs": {"seed": 12345, "steps": 28}},
        "reference": {"inputs": {"image": "cyrene-reference.png"}},
        "mask": {"inputs": {"image": "cyrene-mask.png"}},
    }


@pytest.mark.asyncio
async def test_seedance_resume_queries_existing_task_without_creating_another(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers import seedance
    from cyrene.plugins.builtin.cyrene_media.providers.seedance import SeedanceProvider

    calls: list[dict[str, Any]] = []

    async def request_json(method: str, url: str, **kwargs: Any):
        calls.append({"method": method, "url": url, **kwargs})
        return {
            "status": "succeeded",
            "video_url": "https://media.example.test/seedance.mp4",
        }

    async def artifact_from_url(url: str, **_kwargs: Any):
        assert url == "https://media.example.test/seedance.mp4"
        return MediaArtifact(
            filename="seedance.mp4",
            content_type="video/mp4",
            data=b"seedance-video",
        )

    monkeypatch.setattr(seedance, "request_json", request_json)
    monkeypatch.setattr(seedance, "artifact_from_url", artifact_from_url)

    result = await SeedanceProvider().generate(
        {
            "kind": "video",
            "prompt": "the crane takes flight",
            "_resume_provider_job_id": "seedance-task-existing",
        },
        {
            "api_key": "test-key",
            "base_url": "https://ark.example.test/api/v3",
            "video_model": "seedance-test",
        },
        _progress,
    )

    assert [(call["method"], call["url"]) for call in calls] == [
        (
            "GET",
            "https://ark.example.test/api/v3/contents/generations/tasks/seedance-task-existing",
        )
    ]
    assert result.provider_job_id == "seedance-task-existing"
    assert result.artifacts[0].data == b"seedance-video"


def test_seedance_output_urls_ignore_thumbnail_and_last_frame_side_outputs():
    from cyrene.plugins.builtin.cyrene_media.providers.seedance import _output_urls

    assert _output_urls(
        {
            "data": {
                "video_url": "https://media.example.test/result.mp4",
                "thumbnail_url": "https://media.example.test/poster.jpg",
                "last_frame_url": "https://media.example.test/last-frame.png",
            }
        }
    ) == [("https://media.example.test/result.mp4", "video")]


@pytest.mark.asyncio
async def test_minimax_video_resume_queries_existing_task_without_new_submission(
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers import minimax
    from cyrene.plugins.builtin.cyrene_media.providers.minimax import MiniMaxProvider

    calls: list[dict[str, Any]] = []

    async def request_json(method: str, url: str, **kwargs: Any):
        calls.append({"method": method, "url": url, **kwargs})
        return {
            "status": "success",
            "video_url": "https://media.example.test/minimax.mp4",
        }

    async def artifact_from_url(url: str, **_kwargs: Any):
        assert url == "https://media.example.test/minimax.mp4"
        return MediaArtifact(
            filename="minimax.mp4",
            content_type="video/mp4",
            data=b"minimax-video",
        )

    monkeypatch.setattr(minimax, "request_json", request_json)
    monkeypatch.setattr(minimax, "artifact_from_url", artifact_from_url)

    result = await MiniMaxProvider().generate(
        {
            "kind": "video",
            "prompt": "the crane crosses the lake",
            "_resume_provider_job_id": "minimax-task-existing",
        },
        {
            "api_key": "test-key",
            "base_url": "https://api.minimax.io",
            "video_model": "minimax-video-test",
        },
        _progress,
    )

    assert [(call["method"], call["url"]) for call in calls] == [("GET", "https://api.minimax.io/v1/query/video_generation")]
    assert calls[0]["params"] == {"task_id": "minimax-task-existing"}
    assert result.provider_job_id == "minimax-task-existing"
    assert result.artifacts[0].data == b"minimax-video"


@pytest.mark.asyncio
async def test_google_veo_resume_polls_operation_without_generate_videos(monkeypatch):
    from cyrene.plugins.builtin.cyrene_media.providers import google_media
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    generated_calls: list[dict[str, Any]] = []
    operation_names: list[str] = []

    class FakeGenerateVideosOperation:
        def __init__(self, *, name: str) -> None:
            self.name = name
            self.done = False

    class FakeGenerateVideosConfig:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class Types:
        GenerateVideosOperation = FakeGenerateVideosOperation
        GenerateVideosConfig = FakeGenerateVideosConfig

    class Models:
        def generate_videos(self, **kwargs: Any):
            generated_calls.append(kwargs)
            raise AssertionError("resume must not create another Veo operation")

    class Operations:
        def get(self, operation: FakeGenerateVideosOperation):
            operation_names.append(operation.name)
            return SimpleNamespace(
                name=operation.name,
                done=True,
                error=None,
                response=SimpleNamespace(
                    generated_videos=[
                        SimpleNamespace(
                            video_bytes=b"veo-video",
                            mime_type="video/mp4",
                        )
                    ]
                ),
            )

    class Client:
        def __init__(self) -> None:
            self.models = Models()
            self.operations = Operations()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    async def no_sleep(_seconds: float) -> None:
        return None

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, Types))
    monkeypatch.setattr(google_media.asyncio, "sleep", no_sleep)

    result = await provider.generate(
        {
            "kind": "video",
            "prompt": "a crane glides over the ocean",
            "_resume_provider_job_id": "operations/veo-existing",
        },
        {
            "api_key": "test-google-key",
            "video_model": "veo-3.1-generate-preview",
        },
        _progress,
    )

    assert generated_calls == []
    assert operation_names == ["operations/veo-existing"]
    assert result.provider_job_id == "operations/veo-existing"
    assert result.artifacts[0].data == b"veo-video"
    assert result.artifacts[0].content_type == "video/mp4"
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "request_values", "expected_code"),
    [
        (
            "veo-3.1-generate-preview",
            {"duration": 5},
            "google_invalid_duration",
        ),
        (
            "veo-3.0-generate-preview",
            {"duration": 6},
            "google_invalid_duration",
        ),
        (
            "veo-2.0-generate-001",
            {"duration": 4},
            "google_invalid_duration",
        ),
        (
            "veo-3.1-generate-preview",
            {"number_of_outputs": 2},
            "google_unsupported_output_count",
        ),
        (
            "veo-2.0-generate-001",
            {"number_of_outputs": 3},
            "google_unsupported_output_count",
        ),
        (
            "veo-2.0-generate-001",
            {"number_of_outputs": 1.5},
            "google_unsupported_output_count",
        ),
    ],
)
async def test_google_veo_rejects_unsupported_duration_and_output_boundaries(
    model,
    request_values,
    expected_code,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    generated_calls: list[dict[str, Any]] = []

    class FakeGenerateVideosConfig:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class Types:
        GenerateVideosConfig = FakeGenerateVideosConfig

    class Models:
        def generate_videos(self, **kwargs: Any):
            generated_calls.append(kwargs)
            raise AssertionError("invalid Veo options must not be submitted")

    class Client:
        def __init__(self) -> None:
            self.models = Models()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, Types))

    with pytest.raises(MediaProviderError) as rejected:
        await provider.generate(
            {
                "kind": "video",
                "prompt": "a crane flies through clouds",
                **request_values,
            },
            {"api_key": "test-google-key", "video_model": model},
            _progress,
        )

    assert rejected.value.code == expected_code
    assert generated_calls == []
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "duration", "number_of_outputs"),
    [
        ("veo-2.0-generate-001", 5, 2),
        ("veo-3.1-generate-preview", 4, 1),
        ("veo-3.0-generate-preview", 8, 1),
    ],
)
async def test_google_veo_accepts_documented_duration_and_output_boundaries(
    model,
    duration,
    number_of_outputs,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    generated_calls: list[dict[str, Any]] = []

    class FakeGenerateVideosConfig:
        def __init__(self, **values: Any) -> None:
            self.values = values

    class Types:
        GenerateVideosConfig = FakeGenerateVideosConfig

    class Models:
        def generate_videos(self, **kwargs: Any):
            generated_calls.append(kwargs)
            count = kwargs["config"].values["number_of_videos"]
            return SimpleNamespace(
                name="operations/veo-boundary",
                done=True,
                error=None,
                response=SimpleNamespace(
                    generated_videos=[
                        SimpleNamespace(
                            video_bytes=f"veo-{index}".encode(),
                            mime_type="video/mp4",
                        )
                        for index in range(count)
                    ]
                ),
            )

    class Client:
        def __init__(self) -> None:
            self.models = Models()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, Types))

    result = await provider.generate(
        {
            "kind": "video",
            "prompt": "a crane flies through clouds",
            "duration": duration,
            "number_of_outputs": number_of_outputs,
        },
        {"api_key": "test-google-key", "video_model": model},
        _progress,
    )

    assert len(generated_calls) == 1
    assert generated_calls[0]["config"].values == {
        "number_of_videos": number_of_outputs,
        "duration_seconds": duration,
    }
    assert len(result.artifacts) == number_of_outputs
    assert result.provider_job_id == "operations/veo-boundary"
    assert client.closed is True


@pytest.mark.asyncio
async def test_seedance_rejects_local_video_reference_before_submission(
    tmp_path,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers import seedance
    from cyrene.plugins.builtin.cyrene_media.providers.seedance import SeedanceProvider

    local_video = tmp_path / "reference.mp4"
    local_video.write_bytes(b"local-video")

    async def unexpected_request(*_args: Any, **_kwargs: Any):
        raise AssertionError("invalid local video must not reach Seedance")

    monkeypatch.setattr(seedance, "request_json", unexpected_request)

    with pytest.raises(MediaProviderError) as rejected:
        await SeedanceProvider().generate(
            {
                "kind": "video",
                "prompt": "continue this shot",
                "reference_paths": [str(local_video)],
                "reference_roles": ["reference_video"],
            },
            {
                "api_key": "test-key",
                "base_url": "https://ark.example.test/api/v3",
                "video_model": "seedance-test",
            },
            _progress,
        )

    assert rejected.value.code == "seedance_unsupported_video_reference"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("references", "roles", "expected_code"),
    [
        (
            [
                "data:image/png;base64," + base64.b64encode(b"first").decode("ascii"),
                "data:image/png;base64," + base64.b64encode(b"subject").decode("ascii"),
            ],
            ["first_frame", "subject"],
            "google_invalid_references",
        ),
        (
            ["data:image/png;base64," + base64.b64encode(b"audio-role").decode("ascii")],
            ["audio"],
            "google_unsupported_reference",
        ),
    ],
)
async def test_google_veo_validates_reference_modes_and_roles_before_submission(
    references,
    roles,
    expected_code,
    monkeypatch,
):
    from cyrene.plugins.builtin.cyrene_media.providers.google_media import GoogleMediaProvider

    generated_calls: list[dict[str, Any]] = []

    class FakeImage:
        def __init__(self, *, image_bytes: bytes, mime_type: str) -> None:
            self.image_bytes = image_bytes
            self.mime_type = mime_type

    class Types:
        Image = FakeImage

    class Models:
        def generate_videos(self, **kwargs: Any):
            generated_calls.append(kwargs)
            raise AssertionError("invalid references must not reach Google Veo")

    class Client:
        def __init__(self) -> None:
            self.models = Models()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    client = Client()
    provider = GoogleMediaProvider()
    monkeypatch.setattr(provider, "_client", lambda _key, _timeout: (client, Types))

    with pytest.raises(MediaProviderError) as rejected:
        await provider.generate(
            {
                "kind": "video",
                "prompt": "animate these references",
                "reference_paths": references,
                "reference_roles": roles,
            },
            {
                "api_key": "test-google-key",
                "video_model": "veo-3.1-generate-preview",
            },
            _progress,
        )

    assert rejected.value.code == expected_code
    assert generated_calls == []
    assert client.closed is True

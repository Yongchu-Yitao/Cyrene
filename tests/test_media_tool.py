from __future__ import annotations

from pathlib import Path

import pytest


def test_media_tool_keeps_reference_sources_and_roles_in_provider_order(
    tmp_path,
    monkeypatch,
):
    from cyrene.tool_impl.media import start_media_generation as media_tool

    local = tmp_path / "local.png"
    attached = tmp_path / "attached.mp4"
    mask = tmp_path / "mask.png"
    for path in (local, attached, mask):
        path.write_bytes(path.name.encode())

    monkeypatch.setattr(media_tool, "resolve_tool_path", lambda value: Path(value))
    monkeypatch.setattr(
        media_tool,
        "_resolve_attachment_reference",
        lambda value, *, chat_attachment_ids: {
            "attachment-video": str(attached.resolve()),
            "attachment-mask": str(mask.resolve()),
        }[value],
    )

    request = media_tool._normalize_request(
        {
            "kind": "image",
            "provider": "openai",
            "prompt": "combine the references",
            "reference_paths": [str(local)],
            "reference_attachment_ids": ["attachment-video"],
            "reference_urls": ["https://media.example.test/reference.webp"],
            "reference_roles": ["first_frame", "reference_video", "subject"],
            "mask_attachment_id": "attachment-mask",
            "duration": -1,
            "number_of_outputs": 2,
            "seed": 42,
        },
        index=0,
        chat_attachment_ids={"attachment-video", "attachment-mask"},
    )

    assert request["reference_paths"] == [
        str(local.resolve()),
        str(attached.resolve()),
    ]
    assert request["reference_attachment_ids"] == ["attachment-video"]
    assert request["reference_urls"] == ["https://media.example.test/reference.webp"]
    assert request["reference_roles"] == [
        "first_frame",
        "reference_video",
        "subject",
    ]
    assert request["mask_path"] == str(mask.resolve())
    assert request["mask_attachment_id"] == "attachment-mask"
    assert request["duration"] == -1
    assert request["number_of_outputs"] == 2
    assert request["seed"] == 42


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"reference_urls": ["http://example.test/reference.png"]}, "public HTTPS"),
        ({"reference_urls": ["https://127.0.0.1/reference.png"]}, "private address"),
        ({"number_of_outputs": 1.5}, "must be an integer"),
        ({"seed": 1.5}, "non-negative integer"),
        ({"duration": float("nan")}, "duration must be"),
        ({"kind": "video", "mask_path": "mask.png"}, "only valid for image jobs"),
    ],
)
def test_media_tool_rejects_unsafe_or_ambiguous_inputs(
    tmp_path,
    monkeypatch,
    updates,
    message,
):
    from cyrene.tool_impl.media import start_media_generation as media_tool

    mask = tmp_path / "mask.png"
    mask.write_bytes(b"mask")
    monkeypatch.setattr(
        media_tool,
        "resolve_tool_path",
        lambda value: mask if value == "mask.png" else Path(value),
    )
    raw = {
        "kind": "image",
        "provider": "auto",
        "prompt": "a safe request",
        **updates,
    }

    with pytest.raises(ValueError, match=message):
        media_tool._normalize_request(
            raw,
            index=0,
            chat_attachment_ids=set(),
        )


def test_media_tool_rejects_attachments_outside_the_current_chat(monkeypatch):
    from cyrene.tool_impl.media import start_media_generation as media_tool

    with pytest.raises(ValueError, match="current conversation"):
        media_tool._resolve_attachment_reference(
            "foreign-attachment",
            chat_attachment_ids={"current-attachment"},
        )

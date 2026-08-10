import base64
import json


def _png_base64() -> str:
    # Fixed valid 3x2 RGB PNG keeps this transport test independent from any
    # image-library state changed by earlier tests in the full release suite.
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAFElEQVR4nGPk"
        "UbJgAAMmCMXAwAAABzwAaryWzqUAAAAASUVORK5CYII="
    )


def test_mcp_image_is_stored_as_artifact_without_persisting_base64(
    tmp_path, monkeypatch, real_pillow_modules
):
    from cyrene.tooling import mcp_content

    monkeypatch.setattr(mcp_content, "MCP_CONTENT_DIR", tmp_path / "mcp-content")
    encoded = _png_base64()

    result = mcp_content.serialize_mcp_content_blocks(
        "take_screenshot",
        [
            {"type": "text", "text": "current page"},
            {"type": "image", "data": encoded, "mimeType": "image/png"},
        ],
    )

    assert encoded not in result
    payload = json.loads(result)
    artifact = payload["artifacts"][0]
    assert artifact["mime_type"] == "image/png"
    assert artifact["width"] == 3
    assert artifact["height"] == 2
    assert (tmp_path / "mcp-content" / artifact["path"].split("/")[-1]).is_file()


def test_mcp_artifact_becomes_ephemeral_image_url_observation_for_next_request(
    tmp_path, monkeypatch, real_pillow_modules
):
    from cyrene.model_runtime.client import sanitize_messages_for_llm
    from cyrene.tooling import mcp_content

    monkeypatch.setattr(mcp_content, "MCP_CONTENT_DIR", tmp_path / "mcp-content")
    encoded = _png_base64()
    result = mcp_content.serialize_mcp_content_blocks(
        "take_screenshot",
        [{"type": "image", "data": encoded, "mimeType": "image/png"}],
    )

    observation = mcp_content.build_mcp_observation_message(
        result, tool_name="take_screenshot"
    )
    assert observation is not None
    assert observation["hidden_from_ui"] is True
    assert observation["ephemeral_model_observation"] is True
    assert "base64," not in json.dumps(observation)
    assert observation["content"][1]["type"] == mcp_content.MCP_IMAGE_BLOCK_TYPE

    durable_snapshot = sanitize_messages_for_llm(
        [observation], materialize_internal_media=False
    )
    prepared = sanitize_messages_for_llm([observation])

    assert "base64," not in json.dumps(durable_snapshot)
    assert (
        durable_snapshot[0]["content"][1]["type"]
        == mcp_content.MCP_IMAGE_BLOCK_TYPE
    )
    assert prepared[0]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }
    # Materialization must not mutate the history/observation object.
    assert observation["content"][1]["type"] == mcp_content.MCP_IMAGE_BLOCK_TYPE

import json
from pathlib import Path


def _png_base64() -> str:
    # Fixed valid 3x2 RGB PNG keeps this transport test independent from any
    # image-library state changed by earlier tests in the full release suite.
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAFElEQVR4nGPk"
        "UbJgAAMmCMXAwAAABzwAaryWzqUAAAAASUVORK5CYII="
    )


def test_mcp_image_is_stored_as_artifact_without_persisting_base64(
    tmp_path, real_pillow_modules
):
    from cyrene.plugins.builtin.cyrene_mcp import content as mcp_content

    content_directory = tmp_path / "mcp-content"
    encoded = _png_base64()

    result = mcp_content.serialize_mcp_result(
        "browser",
        "take_screenshot",
        {
            "content": [
                {"type": "text", "text": "current page"},
                {"type": "image", "data": encoded, "mimeType": "image/png"},
            ]
        },
        content_directory=content_directory,
    )

    assert encoded not in json.dumps(result)
    artifact = result["artifacts"][0]
    assert artifact["mime_type"] == "image/png"
    assert artifact["width"] == 3
    assert artifact["height"] == 2
    assert content_directory in Path(artifact["path"]).parents
    assert Path(artifact["path"]).is_file()


def test_mcp_artifact_becomes_ephemeral_image_url_observation_for_next_request(
    tmp_path, real_pillow_modules
):
    from cyrene.plugins.builtin.cyrene_mcp import content as mcp_content

    encoded = _png_base64()
    result = mcp_content.serialize_mcp_result(
        "browser",
        "take_screenshot",
        {
            "content": [
                {"type": "image", "data": encoded, "mimeType": "image/png"}
            ]
        },
        content_directory=tmp_path / "mcp-content",
    )

    observation = mcp_content.build_mcp_observation_content(
        result,
        tool_name="take_screenshot",
    )
    assert observation is not None
    assert "base64," not in json.dumps(observation)
    assert observation[1]["type"] == mcp_content.MCP_IMAGE_BLOCK_TYPE

    prepared = mcp_content.materialize_model_content_block(observation[1])
    assert prepared == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }
    # Materialization must not mutate the history/observation object.
    assert observation[1]["type"] == mcp_content.MCP_IMAGE_BLOCK_TYPE

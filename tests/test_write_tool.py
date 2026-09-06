from __future__ import annotations

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.core_impl.write import WRITE_PLUGIN, write
from cyrene.core.plugin.validation import (
    validate_plugin_arguments,
)


@pytest.mark.asyncio
async def test_write_overwrites_by_default_and_can_append_chunks(tmp_path):
    path = tmp_path / "large.txt"
    context = PluginContext(workspace=tmp_path)

    first = await write(
        {"path": str(path), "content": "first\n"},
        context,
    )
    second = await write(
        {"path": str(path), "content": "second\n", "mode": "append"},
        context,
    )

    assert first == f"Wrote {path}"
    assert second == f"Appended to {path}"
    assert path.read_text(encoding="utf-8") == "first\nsecond\n"


@pytest.mark.asyncio
async def test_write_accepts_and_preserves_content_over_guidance_limit(tmp_path):
    path = tmp_path / "large.txt"
    content = "长文本\n" * 4000
    arguments = {"path": str(path), "content": content}
    validate_plugin_arguments("Write", arguments, WRITE_PLUGIN.input_schema)
    await write(arguments, PluginContext(workspace=tmp_path))
    assert path.read_text(encoding="utf-8") == content


def test_write_schema_accepts_the_append_mode(tmp_path):
    validate_plugin_arguments(
        "Write",
        {
            "path": str(tmp_path / "large.txt"),
            "content": "next chunk",
            "mode": "append",
        },
        WRITE_PLUGIN.input_schema,
    )

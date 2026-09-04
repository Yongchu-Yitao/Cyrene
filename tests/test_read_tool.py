from __future__ import annotations

import pytest

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.core_impl.read import READ_PLUGIN, read
from cyrene.core.plugin.validation import validate_plugin_arguments


def test_read_schema_accepts_line_range_arguments(tmp_path):
    validate_plugin_arguments(
        "Read",
        {
            "path": str(tmp_path / "example.txt"),
            "start_line": 2,
            "end_line": 4,
        },
        READ_PLUGIN.input_schema,
    )


@pytest.mark.asyncio
async def test_read_returns_the_whole_file_unchanged_by_default(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("first\nsecond\nthird", encoding="utf-8")

    result = await read({"path": str(path)}, PluginContext(workspace=tmp_path))

    assert result == "first\nsecond\nthird"


@pytest.mark.asyncio
async def test_read_can_return_from_a_start_line(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")

    result = await read(
        {"path": str(path), "start_line": 3},
        PluginContext(workspace=tmp_path),
    )

    assert result == "third\nfourth\n"


@pytest.mark.asyncio
async def test_read_line_range_is_one_based_and_inclusive(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("first\nsecond\nthird\nfourth\n", encoding="utf-8")

    result = await read(
        {"path": str(path), "start_line": 2, "end_line": 3},
        PluginContext(workspace=tmp_path),
    )

    assert result == "second\nthird\n"


@pytest.mark.asyncio
async def test_read_rejects_a_reversed_line_range(tmp_path):
    path = tmp_path / "example.txt"
    path.write_text("first\nsecond\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="end_line must be greater than or equal to start_line",
    ):
        await read(
            {"path": str(path), "start_line": 3, "end_line": 2},
            PluginContext(workspace=tmp_path),
        )

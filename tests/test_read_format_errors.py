"""Read format failures must reach the agent with actionable guidance."""

import asyncio

import pytest

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.core.plugin.core_impl.read import READ_PLUGIN


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("filename,payload", [
    ("screenshot.png", b"\x89PNG\r\n\x1a\n"),
    ("photo.jpg", b"\xff\xd8\xff\xe0"),
    ("document.pdf", b"%PDF-1.7\n\xff"),
    ("unknown.bin", b"hello\x00world"),
    ("legacy.txt", b"caf\xe9"),
])
def test_read_format_failure_survives_runtime_without_disabling_read(
    tmp_path, language, filename, payload,
):
    (tmp_path / filename).write_bytes(payload)
    (tmp_path / "valid.txt").write_text("first\n你好\nlast\n", encoding="utf-8")
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(READ_PLUGIN, source="test")
    runtime = PluginRuntime(registry)
    context = PluginContext(workspace=tmp_path, data={
        "language": language, "run_id": "same-run",
    })

    async def exercise():
        failed = await runtime.call("Read", {"path": filename}, context)
        valid = await runtime.call("Read", {
            "path": "valid.txt", "start_line": 2, "end_line": 2,
        }, context)
        return failed, valid

    failed, valid = asyncio.run(exercise())
    assert not failed.success
    assert failed.failure.error_code == "read_unsupported_format"
    assert "AnalyzeAttachment" in failed.error
    assert "toolbox" in failed.error
    assert ("重新上传" if language == "zh" else "re-uploading") in failed.error
    assert failed.failure.circuit_scope == "none"
    assert not failed.failure.retryable
    assert valid.success
    assert valid.value == "你好\n"

import json
import threading
from urllib.parse import quote

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_managed_attachment_path_rebases_after_portable_restore(
    monkeypatch, tmp_path
):
    from cyrene.runtime import attachments

    data = tmp_path / "new-home" / "data"
    uploads = data / "webui_uploads"
    exports = data / "webui_exports"
    uploads.mkdir(parents=True)
    exports.mkdir()
    monkeypatch.setattr(attachments, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(attachments, "EXPORTS_DIR", exports)

    exported = exports / "report_deadbeef.html"
    exported.write_text("<h1>restored</h1>", encoding="utf-8")
    old_posix_path = (
        "/Users/old/Library/Application Support/Cyrene/"
        "data/webui_exports/report_deadbeef.html"
    )
    old_windows_path = (
        r"C:\Users\old\AppData\Roaming\Cyrene\data\webui_exports"
        r"\report_deadbeef.html"
    )

    assert attachments.resolve_managed_attachment_path(old_posix_path) == exported
    assert attachments.resolve_managed_attachment_path(old_windows_path) == exported
    assert attachments.is_exported_attachment_path(old_posix_path) is True
    assert (
        attachments.resolve_managed_attachment_path(
            "/Users/old/Documents/report_deadbeef.html"
        )
        is None
    )
    assert (
        attachments.resolve_managed_attachment_path(
            "/Users/old/data/webui_exports/../../secret.txt"
        )
        is None
    )


@pytest.mark.asyncio
async def test_analyze_attachment_missing_file_returns_terminal_upload_error(tmp_path):
    from agent.plugin import PluginContext
    from agent.plugin.plugin_impl.cyrene_content.analyze_attachment import (
        _tool_analyze_attachment,
    )
    from cyrene.runtime.attachments import UPLOADS_DIR

    result = await _tool_analyze_attachment(
        {"path": str(UPLOADS_DIR / "missing-test-attachment.png")},
        PluginContext(services={"content": object()}),
    )

    payload = json.loads(result)
    assert payload["error"] == "attachment_unavailable"
    assert payload["action"] == "stop_attachment_analysis"
    assert payload["search_elsewhere"] is False

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


class _AllowPermission:
    def request_permission(self, **_kwargs):
        return None


def _context(tmp_path=None, *, language: str = ""):
    from cyrene.core.plugin import PluginContext

    data = {"language": language} if language else {}
    return PluginContext(
        workspace=tmp_path,
        data=data,
        services={"permission": _AllowPermission()},
    )


def _target() -> dict:
    return {
        "id": "upload_target_1",
        "tabId": "tab_1",
        "chooserId": "chooser_1",
        "origin": "https://upload.example",
        "topUrl": "https://upload.example/form",
        "frameUrl": "https://upload.example/form",
        "accept": ".txt",
        "multiple": False,
    }


def _file(*, sha256: str = "a" * 64) -> dict:
    return {
        "path": "/workspace/report.txt",
        "name": "report.txt",
        "size": 12,
        "sha256": sha256,
        "content_type": "text/plain",
    }


async def test_upload_executes_after_central_plugin_review(monkeypatch, tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene.plugins.builtin.cyrene_browser import browser_upload_files as tool

    target = _target()
    files = [_file()]
    calls = []

    async def fake_prepare(**_kwargs):
        return {"ok": True, "target": dict(target)}

    async def fake_resolve(_paths):
        return [dict(item) for item in files], None

    async def fake_set_input_files(bound_target, bound_files):
        calls.append((bound_target, bound_files))
        return {"ok": True, "url": target["topUrl"]}

    monkeypatch.setattr(browser, "prepare_file_upload", fake_prepare)
    monkeypatch.setattr(browser, "set_input_files", fake_set_input_files)
    monkeypatch.setattr(tool, "_resolve_files", fake_resolve)
    monkeypatch.setattr(tool, "_stage_approved_files", lambda items, _root: items)
    monkeypatch.setattr(tool, "_new_upload_snapshot", lambda: tmp_path / "snapshot")
    monkeypatch.setattr(tool, "_retain_upload_snapshot", lambda _root: None)
    (tmp_path / "snapshot").mkdir()
    result = await tool._tool_browser_upload_files(
        {"chooser_id": "chooser_1", "paths": ["report.txt"]},
        _context(tmp_path),
    )

    assert json.loads(result)["status"] == "files_attached"
    assert len(calls) == 1


async def test_changed_file_binding_cancels_after_approval(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene.plugins.builtin.cyrene_browser import browser_upload_files as tool

    target = _target()
    before = [_file(sha256="a" * 64)]
    after = [_file(sha256="b" * 64)]
    resolved = [before, after]
    executed = False

    async def fake_prepare(**_kwargs):
        return {"ok": True, "target": dict(target)}

    async def fake_resolve(_paths):
        return [dict(item) for item in resolved.pop(0)], None

    async def fake_set_input_files(*_args, **_kwargs):
        nonlocal executed
        executed = True
        return {"ok": True}

    monkeypatch.setattr(browser, "prepare_file_upload", fake_prepare)
    monkeypatch.setattr(browser, "set_input_files", fake_set_input_files)
    monkeypatch.setattr(tool, "_resolve_files", fake_resolve)
    result = await tool._tool_browser_upload_files(
        {"chooser_id": "chooser_1", "paths": ["report.txt"]},
        _context(language="en"),
    )

    assert "changed after approval" in result
    assert executed is False


async def test_non_http_destination_is_rejected_before_reading_files(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser
    from cyrene.plugins.builtin.cyrene_browser import browser_upload_files as tool

    async def fake_prepare(**_kwargs):
        target = _target()
        target["origin"] = "null"
        target["topUrl"] = "file:///tmp/upload.html"
        target["frameUrl"] = target["topUrl"]
        return {"ok": True, "target": target}

    async def unexpected_resolve(_paths):
        raise AssertionError("files must not be read for an unverified origin")

    monkeypatch.setattr(browser, "prepare_file_upload", fake_prepare)
    monkeypatch.setattr(tool, "_resolve_files", unexpected_resolve)

    result = await tool._tool_browser_upload_files(
        {"chooser_id": "chooser_1", "paths": ["report.txt"]},
        _context(language="en"),
    )

    assert "verified HTTP(S) origin" in result


async def test_electron_upload_transport_uses_dedicated_rpc(monkeypatch):
    from cyrene.plugins.builtin.cyrene_browser import runtime as browser

    calls = []

    async def fake_rpc(method, args=None, **_kwargs):
        calls.append((method, args or {}))
        if method == "prepareUpload":
            return {"ok": True, "target": _target()}
        return {"ok": True}

    monkeypatch.setattr(browser, "electron_browser_available", lambda: True)
    monkeypatch.setattr(browser, "_electron_browser_rpc", fake_rpc)

    await browser.prepare_file_upload(chooser_id="chooser_1")
    await browser.set_input_files(_target(), [_file()])

    assert calls[0] == ("prepareUpload", {"chooserId": "chooser_1", "ref": ""})
    assert calls[1][0] == "setInputFiles"
    assert calls[1][1]["targetId"] == "upload_target_1"
    assert calls[1][1]["files"][0]["sha256"] == "a" * 64


async def test_intercepted_chooser_message_is_actionable():
    from cyrene.plugins.builtin.cyrene_browser.browser_output import file_chooser_instruction

    from cyrene.core.plugin import PluginContext

    message = file_chooser_instruction(
        {
            "code": "FILE_CHOOSER_INTERCEPTED",
            "chooserId": "chooser_abc",
            "uploadTarget": {
                "origin": "https://upload.example",
                "accept": ".pdf",
                "multiple": True,
            },
        },
        PluginContext(data={"language": "en"}),
    )

    assert "chooser_abc" in message
    assert "browser_upload_files" in message
    assert "single-use" in message


async def test_approved_file_snapshot_preserves_exact_bytes_and_name(tmp_path):
    from cyrene.plugins.builtin.cyrene_browser import browser_upload_files as tool

    source = tmp_path / "report.txt"
    source.write_bytes(b"approved bytes")
    metadata = tool._file_metadata(source)
    staging_root = tmp_path / "private"
    staging_root.mkdir(mode=0o700)

    staged = tool._stage_approved_files([metadata], staging_root)

    snapshot = staged[0]
    assert snapshot["path"] != str(source)
    assert snapshot["name"] == source.name
    assert snapshot["sha256"] == metadata["sha256"]
    assert open(snapshot["path"], "rb").read() == b"approved bytes"


async def test_electron_upload_uses_guarded_cdp_path():
    root = Path(__file__).resolve().parent.parent
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")

    assert "Page.setInterceptFileChooserDialog" in main
    assert "Page.fileChooserOpened" in main
    assert "DOM.setFileInputFiles" in main
    assert "FILE_CHOOSER_INTERCEPTED" in main
    assert "FILE_CHOOSER_GUARD_UNAVAILABLE" in main
    assert "await this._setFileChooserInterception(tab, false)" in main
    assert "frameLoaderId" in main

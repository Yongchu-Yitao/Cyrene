from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import workbench_settings_source


pytestmark = pytest.mark.asyncio


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


async def test_external_upload_requires_human_even_in_full_access(monkeypatch):
    from cyrene.agent import session, state
    from cyrene.tooling.runtime_support import _request_external_upload_confirmation

    captured = {}

    async def fake_publish(event, *args, **kwargs):
        captured.setdefault("events", []).append(event)

    async def fake_upsert(payload):
        captured["question"] = payload
        return {"id": "question_upload_1"}

    monkeypatch.setattr(state, "_publish_runtime_event", fake_publish)
    monkeypatch.setattr(session, "_upsert_pending_question", fake_upsert)
    monkeypatch.setattr(session, "get_session_labels", lambda _round_id: {})

    mode_token = state._permission_mode.set("full_access")
    round_token = state._current_round_id.set("round_upload_1")
    grants_token = state._external_upload_confirmation_fingerprints.set(frozenset())
    try:
        result = await _request_external_upload_confirmation(
            fingerprint="fingerprint_1",
            target=_target(),
            files=[_file()],
            reason="Upload the requested report.",
        )
    finally:
        state._external_upload_confirmation_fingerprints.reset(grants_token)
        state._current_round_id.reset(round_token)
        state._permission_mode.reset(mode_token)

    payload = json.loads(result)
    assert payload["status"] == "awaiting_user"
    assert payload["permission"] == "external_upload_confirmation"
    assert captured["question"]["meta"]["kind"] == "external_upload_confirmation"
    assert captured["question"]["options"] == ["允许这次上传", "拒绝"]
    assert "upload.example" in captured["question"]["text"]


async def test_upload_grant_is_consumed_once(monkeypatch, tmp_path):
    from cyrene import browser
    from cyrene.agent import state
    from cyrene.tool_impl.browser import browser_upload_files as tool

    target = _target()
    files = [_file()]
    calls = []

    async def fake_prepare(**_kwargs):
        return {"ok": True, "target": dict(target)}

    async def fake_resolve(_paths):
        return [dict(item) for item in files], None

    async def fake_approval(**_kwargs):
        return None

    async def fake_set_input_files(bound_target, bound_files):
        calls.append((bound_target, bound_files))
        return {"ok": True, "url": target["topUrl"]}

    async def fake_publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser, "prepare_file_upload", fake_prepare)
    monkeypatch.setattr(browser, "set_input_files", fake_set_input_files)
    monkeypatch.setattr(tool, "_resolve_files", fake_resolve)
    monkeypatch.setattr(tool, "_stage_approved_files", lambda items, _root: items)
    monkeypatch.setattr(tool, "_new_upload_snapshot", lambda: tmp_path / "snapshot")
    monkeypatch.setattr(tool, "_retain_upload_snapshot", lambda _root: None)
    (tmp_path / "snapshot").mkdir()
    monkeypatch.setattr(tool, "_request_external_upload_confirmation", fake_approval)
    monkeypatch.setattr(state, "_publish_runtime_event", fake_publish)

    fingerprint = tool._upload_fingerprint(target, files)
    token = state._external_upload_confirmation_fingerprints.set(frozenset({fingerprint}))
    try:
        result = await tool._tool_browser_upload_files(
            {"chooser_id": "chooser_1", "paths": ["report.txt"]},
            None,
            0,
            "db",
            None,
        )
        remaining = state._external_upload_confirmation_fingerprints.get()
    finally:
        state._external_upload_confirmation_fingerprints.reset(token)

    assert json.loads(result)["status"] == "files_attached"
    assert len(calls) == 1
    assert fingerprint not in remaining


async def test_changed_file_binding_cancels_after_approval(monkeypatch):
    from cyrene import browser
    from cyrene.agent import state
    from cyrene.tool_impl.browser import browser_upload_files as tool

    target = _target()
    before = [_file(sha256="a" * 64)]
    after = [_file(sha256="b" * 64)]
    resolved = [before, after]
    executed = False

    async def fake_prepare(**_kwargs):
        return {"ok": True, "target": dict(target)}

    async def fake_resolve(_paths):
        return [dict(item) for item in resolved.pop(0)], None

    async def fake_approval(**_kwargs):
        return None

    async def fake_set_input_files(*_args, **_kwargs):
        nonlocal executed
        executed = True
        return {"ok": True}

    async def fake_publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser, "prepare_file_upload", fake_prepare)
    monkeypatch.setattr(browser, "set_input_files", fake_set_input_files)
    monkeypatch.setattr(tool, "_resolve_files", fake_resolve)
    monkeypatch.setattr(tool, "_request_external_upload_confirmation", fake_approval)
    monkeypatch.setattr(state, "_publish_runtime_event", fake_publish)

    fingerprint = tool._upload_fingerprint(target, before)
    token = state._external_upload_confirmation_fingerprints.set(frozenset({fingerprint}))
    try:
        result = await tool._tool_browser_upload_files(
            {"chooser_id": "chooser_1", "paths": ["report.txt"]},
            None,
            0,
            "db",
            None,
        )
    finally:
        state._external_upload_confirmation_fingerprints.reset(token)

    assert "changed after approval" in result
    assert executed is False


async def test_non_http_destination_is_rejected_before_reading_files(monkeypatch):
    from cyrene import browser
    from cyrene.tool_impl.browser import browser_upload_files as tool

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
        None,
        0,
        "db",
        None,
    )

    assert "verified HTTP(S) origin" in result


async def test_approval_answer_adds_only_bound_upload_fingerprint(monkeypatch):
    from cyrene.agent import coordinator, guidance, state

    seen = {}

    async def fake_run(*_args, **kwargs):
        seen["grants"] = state._external_upload_confirmation_fingerprints.get()
        seen["full_access"] = state._temporary_full_access.get()
        seen["system"] = kwargs.get("ephemeral_system", "")
        return "continued"

    async def fake_publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(coordinator, "_run_chat_agent", fake_run)
    monkeypatch.setattr(state, "_publish_runtime_event", fake_publish)
    grant_token = state._external_upload_confirmation_fingerprints.set(frozenset())
    full_token = state._temporary_full_access.set(False)
    try:
        result = await guidance._handle_permission_elevation_answer(
            round_id="round_1",
            pending={
                "meta": {
                    "kind": "external_upload_confirmation",
                    "fingerprint": "bound_fp",
                    "tool_name": "browser_upload_files",
                    "target": {"origin": "https://upload.example"},
                    "files": [{"name": "report.txt", "sha256": "a" * 64}],
                }
            },
            answer_text="允许这次上传",
            client_request_id="request_1",
            context={},
        )
    finally:
        state._temporary_full_access.reset(full_token)
        state._external_upload_confirmation_fingerprints.reset(grant_token)

    assert result == "continued"
    assert "bound_fp" in seen["grants"]
    assert seen["full_access"] is False
    assert "exactly one external browser file upload" in seen["system"]


async def test_electron_upload_transport_uses_dedicated_rpc(monkeypatch):
    from cyrene import browser

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
    from cyrene.tool_impl.browser.browser_output import file_chooser_instruction

    message = file_chooser_instruction({
        "code": "FILE_CHOOSER_INTERCEPTED",
        "chooserId": "chooser_abc",
        "uploadTarget": {"origin": "https://upload.example", "accept": ".pdf", "multiple": True},
    })

    assert "chooser_abc" in message
    assert "browser_upload_files" in message
    assert "single-use" in message


async def test_approved_file_snapshot_preserves_exact_bytes_and_name(tmp_path):
    from cyrene.tool_impl.browser import browser_upload_files as tool

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


async def test_browser_upload_is_managed_by_browser_package_and_prompt():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    settings = workbench_settings_source()
    prompt = (root / "src" / "cyrene" / "agent" / "prompts.py").read_text(encoding="utf-8")

    assert '"browser_upload_files"' not in settings
    assert "saveToolGroup(group.id, !packageEnabled)" in settings
    assert 't("toolName." + group.wire_name)' in settings
    assert 't("toolPackageDesc." + group.id)' in settings
    assert "FILE_CHOOSER_INTERCEPTED" in prompt
    assert "do not retry the click" in prompt

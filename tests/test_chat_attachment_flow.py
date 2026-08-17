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


def test_chat_export_route_preserves_legacy_unicode_storage_key(
    monkeypatch, tmp_path
):
    from route.agent import chat as chat_routes

    export_name = "deadbeef_测试摘录.md"
    (tmp_path / export_name).write_text("正文", encoding="utf-8")
    monkeypatch.setattr(chat_routes, "_EXPORTS_DIR", tmp_path)

    app = FastAPI()
    router = APIRouter()
    chat_routes.register_chat_routes(router, bot=None, db_path="")
    app.include_router(router)

    response = TestClient(app).get(
        "/api/chat/export/" + quote(export_name, safe="")
    )
    assert response.status_code == 200
    assert response.text == "正文"


@pytest.mark.asyncio
async def test_analyze_attachment_missing_file_returns_terminal_upload_error(tmp_path):
    from cyrene.runtime.attachments import UPLOADS_DIR
    from cyrene.tool_impl.core.analyze_attachment import _tool_analyze_attachment

    result = await _tool_analyze_attachment(
        {"path": str(UPLOADS_DIR / "missing-test-attachment.png")},
        None,
        0,
        "",
        None,
    )

    payload = json.loads(result)
    assert payload["error"] == "attachment_unavailable"
    assert payload["action"] == "stop_attachment_analysis"
    assert payload["search_elsewhere"] is False


def test_attachment_prompt_forbids_device_scan_after_missing_upload():
    from cyrene.workbench.runtime import _attachment_prompt_block

    prompt = _attachment_prompt_block([
        {
            "name": "photo.png",
            "content_type": "image/png",
            "path": "/tmp/photo.png",
        }
    ])

    assert "ask the user to upload it again" in prompt
    assert "Do NOT use Glob, Grep, Bash, find, or directory scans" in prompt


@pytest.mark.asyncio
async def test_workbench_attachment_only_turn_preserves_empty_public_message(
    monkeypatch,
    tmp_path,
):
    from cyrene.model_runtime import client as model_client
    from cyrene.workbench import runtime as routes

    captured = {}
    image_path = tmp_path / "energy.png"
    image_path.write_bytes(b"test image")

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "done"

    async def fake_check_budget_gate(session_id):
        return None

    monkeypatch.setattr(routes, "_check_budget_gate", fake_check_budget_gate)
    monkeypatch.setattr(routes, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        model_client,
        "primary_candidate_supports_vision",
        lambda _session_id="": True,
    )

    result = await routes._workbench_agent_reply(
        "",
        {"id": "session_attachment_only"},
        [],
        attachments=[{
            "id": "upload_1",
            "name": "energy.png",
            "path": str(image_path),
            "content_type": "image/png",
            "kind": "image",
        }],
    )

    assert result == "done"
    assert captured["public_user_message"] == ""
    assert captured["user_message"] == ""
    assert captured["llm_user_content"][0]["type"] == "text"
    assert captured["llm_user_content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,dGVzdCBpbWFnZQ=="},
    }


@pytest.mark.asyncio
async def test_chat_attachment_kb_registration_runs_hash_off_event_loop(
    tmp_path, monkeypatch
):
    from cyrene import config
    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from cyrene.workbench import runtime as routes

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    config.set_knowledge_db_path_override(db_path)
    target = tmp_path / "upload.txt"
    target.write_bytes(b"threaded hash")
    main_thread = threading.get_ident()
    hash_thread = None
    real_hash = store.content_hash_file

    def tracked_hash(path):
        nonlocal hash_thread
        hash_thread = threading.get_ident()
        return real_hash(path)

    monkeypatch.setattr(store, "content_hash_file", tracked_hash)
    try:
        await routes._workbench_register_attachments_kb(
            "session_test",
            [{
                "id": "upload_1",
                "name": "upload.txt",
                "path": str(target),
                "content_type": "text/plain",
                "kind": "code",
                "size": target.stat().st_size,
            }],
        )
    finally:
        config.set_knowledge_db_path_override(None)

    assert hash_thread is not None
    assert hash_thread != main_thread
    docs = await store.list_documents(db_path, source="chat_upload")
    assert len(docs) == 1
    assert docs[0]["name"] == "upload.txt"
    assert docs[0]["source"] == "chat_upload"

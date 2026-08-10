from __future__ import annotations

import importlib
import re

import pytest


def _service(tmp_path):
    from cyrene.workbench import pinned_resources

    service = importlib.reload(pinned_resources)
    service.configure(str(tmp_path / "workbench.sqlite3"))
    return service


def test_pinned_file_is_persistent_and_global_agent_context(tmp_path):
    service = _service(tmp_path)
    file_path = tmp_path / "guide.pptx"
    file_path.write_bytes(b"pptx")

    item = service.upsert_resource(
        {
            "kind": "file",
            "ownerSessionId": "chat-a",
            "name": "guide.pptx",
            "path": str(file_path),
        }
    )

    assert service.get_resource(item["id"])["path"] == str(file_path.resolve())
    assert service.pinned_file_paths()["guide.pptx"] == str(file_path.resolve())
    context = service.global_agent_context("chat-b")
    assert item["id"] in context
    assert str(file_path.resolve()) in context


def test_pinned_browser_resolves_other_session_as_read_only(tmp_path):
    service = _service(tmp_path)
    item = service.upsert_resource(
        {
            "kind": "browser",
            "ownerSessionId": "chat-owner",
            "title": "Docs",
            "url": "https://example.com/docs",
        }
    )

    other = service.browser_snapshot_target(item["id"], "chat-other")
    owner = service.browser_snapshot_target(item["id"], "chat-owner")
    assert other["ownerSessionId"] == "chat-owner"
    assert other["readOnly"] is True
    assert owner["readOnly"] is False
    assert 'access="read-only"' in service.global_agent_context("chat-other")


def test_pinned_resource_upsert_deduplicates_and_remove(tmp_path):
    service = _service(tmp_path)
    first = service.upsert_resource(
        {
            "kind": "browser",
            "ownerSessionId": "chat-owner",
            "stableRef": "chat-owner",
            "title": "First",
        }
    )
    second = service.upsert_resource(
        {
            "kind": "browser",
            "ownerSessionId": "chat-owner",
            "stableRef": "chat-owner",
            "title": "Updated",
        }
    )
    assert first["id"] == second["id"]
    assert len(service.list_resources()) == 1
    assert service.list_resources()[0]["title"] == "Updated"
    assert service.remove_resource(first["id"]) is True
    assert service.list_resources() == []


def test_pinned_library_file_keeps_resolvable_source_metadata(tmp_path):
    service = _service(tmp_path)
    file_path = tmp_path / "paper.pdf"
    file_path.write_bytes(b"%PDF")

    item = service.upsert_resource(
        {
            "kind": "file",
            "sourceKind": "library",
            "libraryItemId": "item-1",
            "ownerSessionId": "library:project-1",
            "ownerProjectId": "project-1",
            "name": "paper.pdf",
            "path": str(file_path),
            "file": {
                "name": "paper.pdf",
                "sourceKind": "library",
                "libraryItemId": "item-1",
                "ownerProjectId": "project-1",
            },
        }
    )

    assert item["sourceKind"] == "library"
    assert item["libraryItemId"] == "item-1"
    assert item["file"]["libraryItemId"] == "item-1"
    assert str(file_path.resolve()) in service.global_agent_context("chat-a")


def test_selected_text_is_materialized_as_pinned_markdown_file(tmp_path, monkeypatch):
    service = _service(tmp_path)
    from cyrene.runtime import attachments

    export_dir = tmp_path / "exports"
    monkeypatch.setattr(attachments, "EXPORTS_DIR", export_dir)
    item = service.upsert_resource(
        {
            "kind": "snippet",
            "ownerSessionId": "chat-a",
            "stableRef": "snippet:chat-a:1",
            "title": "设计说明",
            "text": "第一段\n\n第二段",
        }
    )

    assert item["kind"] == "file"
    assert item["sourceKind"] == "snippet"
    assert item["name"] == "设计说明.md"
    assert item["content_type"] == "text/markdown"
    assert item["url"].startswith("/api/chat/export/")
    assert item["path"].endswith(".md")
    assert re.fullmatch(r"[0-9a-f]{32}\.md", item["file"]["id"])
    assert (export_dir / item["file"]["id"]).read_text(encoding="utf-8") == "第一段\n\n第二段\n"
    assert "设计说明.md" in service.global_agent_context("chat-b")


@pytest.mark.asyncio
async def test_pinned_browser_screenshot_uses_owner_session_read_only(monkeypatch):
    import cyrene.browser
    from cyrene.tool_impl.browser.browser_screenshot import _tool_browser_screenshot
    from cyrene.workbench import pinned_resources

    calls = []

    async def fake_screenshot(url="", **kwargs):
        calls.append((url, kwargs))
        return {"ok": True, "path": "", "title": "Owner page"}

    monkeypatch.setattr(cyrene.browser, "screenshot", fake_screenshot)
    monkeypatch.setattr(
        pinned_resources,
        "browser_snapshot_target",
        lambda _resource_id, _caller: {
            "ownerSessionId": "chat-owner",
            "readOnly": True,
        },
    )

    result = await _tool_browser_screenshot(
        {"resource_id": "pin-browser"},
        None,
        0,
        ":memory:",
        None,
    )

    assert calls == [("", {"session_id": "chat-owner", "read_only": True})]
    assert "read-only pinned browser screenshot" in result

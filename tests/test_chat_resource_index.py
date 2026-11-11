"""Sidebar file reads must not hydrate conversation bodies or context history."""

from types import SimpleNamespace

import pytest
from fastapi import APIRouter

from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.http.workbench.chat_routes.files import register_file_routes


def resources_route(repo, migrate=None):
    def forbidden(*args, **kwargs):
        pytest.fail("Resource reads must not hydrate chat details or project contexts")

    service = SimpleNamespace(
        repository=repo,
        sync_chat_generated_files=migrate or forbidden,
        resolve_chat_workspace_dir=forbidden,
    )
    context = SimpleNamespace(
        service=service, runtime=forbidden, project_data_key=forbidden,
        resolve_library_file_payload=forbidden, public_pinned_resource=forbidden,
    )
    router = APIRouter()
    register_file_routes(router, context)
    return next(route.endpoint for route in router.routes if route.path.endswith('/resources'))


@pytest.mark.asyncio
async def test_resource_index_returns_only_public_attachments_and_tracked_files(tmp_path, monkeypatch):
    repo = ChatRepository(str(tmp_path / "app.db"))
    upload = {"id": "upload", "url": "/uploads/input.pdf", "name": "input.pdf"}
    delivered = {"id": "out", "url": "/exports/result.pdf", "name": "result.pdf"}
    tracked = {"path": "result.md", "source": "agent", "name": "result.md"}
    body = "TRANSCRIPT_BODY_MUST_NOT_BE_DECODED" * 10000
    repo.insert({
        "id": "task", "generatedFiles": [tracked], "messages": [
            {"role": "assistant", "createdAt": "2026-11-11T12:00:00Z", "content": body,
             "trace": [{"result": body}], "attachments": [delivered]},
            {"role": "user", "createdAt": "2026-11-11T11:00:00Z", "content": body,
             "attachments": [upload]},
            {"role": "assistant", "hidden_from_ui": True, "attachments": [{"id": "secret"}]},
            {"role": "assistant", "record_kind": "execution_outcome", "attachments": [{"id": "internal"}]},
            {"role": "assistant", "content": body, "agentAttachments": [{"id": "agent-only"}]},
        ],
    })
    # SQL extracts small JSON fields. Python must never decode content or traces.
    from cyrene.workbench.persistence import chat_repository as persistence
    original_loads = persistence.json.loads

    def small_loads(value, *args, **kwargs):
        assert "TRANSCRIPT_BODY_MUST_NOT_BE_DECODED" not in value
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(persistence.json, "loads", small_loads)
    monkeypatch.setattr(repo, "get", lambda *_: pytest.fail("No full chat reads"))
    monkeypatch.setattr(repo, "read", lambda *_: pytest.fail("No whole-store reads"))
    raw = repo.get_resources("task")
    assert raw is not None and "content" not in str(raw) and "trace" not in str(raw)
    endpoint = resources_route(repo)
    payload = await endpoint("task")
    chat = payload["chat"]
    assert chat["files"] == [tracked]
    assert {attachment["id"] for message in chat["messages"] for attachment in message["attachments"]} == {"upload", "out"}
    assert all(set(message) == {"role", "attachments"} for message in chat["messages"])
    assert await endpoint("task") == payload
    assert (await endpoint("missing")).status_code == 404


@pytest.mark.asyncio
async def test_resource_index_preserves_chronological_attachment_order(tmp_path):
    repo = ChatRepository(str(tmp_path / "app.db"))
    repo.insert({"id": "task", "generatedFiles": [], "messages": [
        {"role": "assistant", "createdAt": "2026-11-11T12:00:00Z", "attachments": [{"id": "later"}]},
        {"role": "user", "created_at": "2026-11-11T11:00:00Z", "attachments": [{"id": "earlier"}]},
    ]})
    payload = await resources_route(repo)("task")
    assert [message["attachments"][0]["id"] for message in payload["chat"]["messages"]] == ["earlier", "later"]


@pytest.mark.asyncio
async def test_legacy_file_index_migrates_once_and_subsequent_reads_are_lightweight(tmp_path):
    repo = ChatRepository(str(tmp_path / "app.db"))
    repo.insert({"id": "legacy", "messages": []})
    calls = []

    def migrate(chat_id):
        calls.append(chat_id)
        repo.mutate_metadata(chat_id, lambda chat: chat.update(generatedFiles=[{"path": "old.txt"}]))

    endpoint = resources_route(repo, migrate)
    assert (await endpoint("legacy"))["chat"]["files"] == [{"path": "old.txt"}]
    await endpoint("legacy")
    assert calls == ["legacy"]

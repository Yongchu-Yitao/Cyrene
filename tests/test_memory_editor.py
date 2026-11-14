import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyrene.plugins.builtin.cyrene_memory import memory_editor as editor
from cyrene.plugins.builtin.cyrene_memory.project_memory import ProjectMemoryApplicationError


@pytest.fixture(autouse=True)
def model_configuration(monkeypatch):
    from cyrene.platform import settings_store
    from cyrene.plugins import model_catalog

    profiles = {
        name: {"id": name, "model": name, "adapter": "openai",
               "base_url": "https://models.example/v1", "capabilities": ["chat"]}
        for name in ("selected-model", "next-model")
    }
    preference = {"candidate_id": "selected-model", "model": "selected-model",
                  "adapter": "openai", "base_url": "https://models.example/v1",
                  "reasoning_effort": "high"}
    settings = {"llm_session_model_preferences": {"assistant_global": preference}}
    routes = []  # Reproduce selection in the assistant with no default route.
    port = SimpleNamespace(
        candidates_for_route=lambda route: list(routes),
        candidate_for_profile=lambda profile_id, *args: profiles.get(profile_id),
        get_model_configuration=lambda: {"profiles": list(profiles.values())},
    )
    monkeypatch.setattr(model_catalog, "_model_configuration_port", lambda: port)
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: settings.get(key, default))
    return SimpleNamespace(profiles=profiles, preference=preference, routes=routes, settings=settings)


@pytest.fixture
def sources():
    return [
        {"id": "prompt:__personal_assistant__", "kind": "prompt", "scope": "__personal_assistant__", "label": "Cyrene", "content": "先给结论", "revision": "v1", "stale": False},
        {"id": "structured:p:m", "kind": "structured", "scope": "p", "key": "m", "label": "项目", "content": "使用 Python", "stale": False},
        {"id": "short:s", "kind": "short", "key": "s", "label": "短期", "content": "周五开会", "stale": False},
    ]


@pytest.mark.asyncio
async def test_summary_reads_all_sources_without_writing(monkeypatch, sources):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    model = AsyncMock(return_value={"content": json.dumps({"summary": "完整摘要", "changes": [{"id": sources[0]["id"], "content": "恶意自动写入"}]})})
    service = SimpleNamespace(model_gateway=SimpleNamespace(complete=model), update=AsyncMock())
    result = await editor.draft_memory(service, "__personal_assistant__")
    payload = json.loads(model.call_args.args[0][1]["content"])
    assert [text for group in payload for text in group["entries"]] == [source["content"] for source in sources]
    assert [(group["kind"], group["label"], group["stale"]) for group in payload] == [
        (source["kind"], source["label"], source["stale"]) for source in sources
    ]
    assert "max_tokens" not in model.call_args.kwargs
    assert result["sourceCount"] == 3
    assert result["changes"] == []
    service.update.assert_not_called()


@pytest.mark.asyncio
async def test_draft_retains_source_identity_and_previous_changes(monkeypatch, sources):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    changes = [{"id": sources[1]["id"], "content": "使用 Rust", "stale": False}]
    model = AsyncMock(return_value={"content": json.dumps({"summary": "已修改项目偏好", "changes": changes})})
    result = await editor.draft_memory(SimpleNamespace(model_gateway=SimpleNamespace(complete=model)), "__personal_assistant__", instruction="改用 Rust", changes=changes, version=editor.fingerprint(sources), history=[{"role": "assistant", "content": "你的项目偏好是 Python"}])
    assert result["changes"] == changes
    payload = json.loads(model.call_args.args[0][1]["content"])
    assert payload["sources"][1]["content"] == "使用 Rust"
    assert payload["conversation"][0]["content"] == "你的项目偏好是 Python"
    assert sources[1]["content"] == "使用 Python"


@pytest.mark.asyncio
async def test_concurrent_memory_change_prevents_model_and_save(monkeypatch, sources):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    service = SimpleNamespace(model_gateway=SimpleNamespace(complete=AsyncMock()), update=AsyncMock())
    for call in [editor.draft_memory(service, "a", instruction="改", version="old"), editor.apply_memory(service, "a", changes=[], version="old")]:
        with pytest.raises(ProjectMemoryApplicationError) as exc:
            await call
        assert exc.value.status_code == 409
    service.update.assert_not_called()
    service.model_gateway.complete.assert_not_called()


@pytest.mark.asyncio
async def test_confirmation_updates_correct_source(monkeypatch, sources):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    def update(*args):
        return None

    service = SimpleNamespace(structured_memories=SimpleNamespace(update=update), update=AsyncMock())
    changes = [{"id": sources[0]["id"], "content": "简明回答", "stale": False}]
    result = await editor.apply_memory(service, "__personal_assistant__", changes=changes, version=editor.fingerprint(sources))
    service.update.assert_awaited_once_with("__personal_assistant__", "简明回答", base_modified_at="v1")
    assert result["applied"] == [sources[0]["id"]]


def test_invalid_or_duplicate_source_is_rejected(sources):
    with pytest.raises(ValueError):
        editor.validate_changes([{"id": "another-account", "content": "x"}], sources)
    with pytest.raises(ValueError):
        editor.validate_changes([{"id": sources[0]["id"], "content": "x"}] * 2, sources)


@pytest.mark.asyncio
async def test_collect_includes_every_project_hidden_records_and_short_term(monkeypatch):
    from cyrene.plugins.builtin.cyrene_memory import project_memory, short_term
    from cyrene.workbench.sessions import context
    monkeypatch.setattr(context, "read_projects", lambda: [{"id": "one", "name": "One"}, {"id": "two", "name": "Two"}])
    monkeypatch.setattr(project_memory, "get_project_memory_prompt", lambda scope: {"current": {"prompt": scope, "modifiedAt": "v1"}})
    monkeypatch.setattr(short_term, "load_entries", lambda: [{"id": "recent", "content": "短期记忆"}])
    calls = []
    def list_memories(scope, include_hidden=False):
        calls.append((scope, include_hidden))
        return {"memories": [{"id": str(i), "content": str(i), "stale": i == 24} for i in range(25)]}
    service = SimpleNamespace(_require_project=AsyncMock(), structured_memories=SimpleNamespace(list=list_memories))
    result = await editor.collect_sources(service, "__personal_assistant__")
    assert {scope for scope, _ in calls} == {"one", "two", "__personal_assistant__", ""}
    assert all(hidden for _, hidden in calls)
    assert len(result) == 104  # three prompts, 100 records, one short-term record
    assert any(item["kind"] == "short" for item in result)
    assert any(item["stale"] for item in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["summary", "edit", "chunked"])
async def test_editor_routes_to_current_assistant_model_without_default(
    monkeypatch, sources, model_configuration, mode,
):
    from cyrene.plugins.model_catalog import configured_model_candidates
    from cyrene.plugins.model_router import _routed_candidates

    if mode == "chunked":
        sources[0]["content"] = "记忆" * 20000
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    assert configured_model_candidates("assistant-memory-editor") == []
    observed = []

    async def complete(messages, **kwargs):
        # Exercise real candidate routing with the identity supplied by the editor.
        candidates, _ = _routed_candidates(
            kwargs["session_id"], kwargs["route"], kwargs.get("model_identity"),
        )
        assert kwargs["session_id"] == "assistant-memory-editor"
        assert len(candidates) == 1
        observed.append(candidates[0])
        # A selection change during a chunked request applies on the next request.
        model_configuration.preference.update(candidate_id="next-model", model="next-model")
        return {"content": json.dumps({"summary": "摘要", "changes": []})}

    model = AsyncMock(side_effect=complete)
    service = SimpleNamespace(model_gateway=SimpleNamespace(complete=model), update=AsyncMock())
    result = await editor.draft_memory(
        service, "__personal_assistant__", instruction="请整理措辞" if mode == "edit" else "",
    )
    assert result["summary"]
    assert {item["id"] for item in observed} == {"selected-model"}
    assert all(item["reasoning_effort"] == "high" for item in observed)
    if mode == "chunked":
        assert len(observed) > 1
    observed.clear()
    await editor.draft_memory(service, "__personal_assistant__")
    assert {item["id"] for item in observed} == {"next-model"}
    service.update.assert_not_called()


@pytest.mark.asyncio
async def test_editor_without_available_model_returns_actionable_error(monkeypatch, sources, model_configuration):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    model_configuration.profiles.clear()
    service = SimpleNamespace(model_gateway=SimpleNamespace(complete=AsyncMock()))
    with pytest.raises(ProjectMemoryApplicationError, match="请先为助手选择模型") as exc:
        await editor.draft_memory(service, "__personal_assistant__")
    assert exc.value.status_code == 409
    assert exc.value.code == "memory_editor_model_unavailable"
    service.model_gateway.complete.assert_not_called()


@pytest.mark.asyncio
async def test_editor_uses_default_route_when_assistant_has_no_selection(monkeypatch, sources, model_configuration):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))
    model_configuration.settings.clear()
    model_configuration.routes.append(model_configuration.profiles["next-model"])
    model = AsyncMock(return_value={"content": json.dumps({"summary": "摘要"})})
    await editor.draft_memory(SimpleNamespace(model_gateway=SimpleNamespace(complete=model)), "__personal_assistant__")
    assert model.call_args.kwargs["model_identity"]["candidateId"] == "next-model"


def test_summary_groups_thousands_of_records_without_dropping_content():
    records = [{"kind": "short", "label": "短期", "content": f"记录 {i}: " + "事实" * 40,
                "stale": i % 10 == 0} for i in range(2108)]
    batches = editor.summary_batches(records, 1_000_000)
    assert len(batches) == 1
    assert len(batches[0]) == 2
    for group in batches[0]:
        assert group["entries"] == [item["content"] for item in records if item["stale"] == group["stale"]]
    assert len(json.dumps(batches)) < len(json.dumps(records))


def test_summary_splits_to_fit_small_context_and_preserves_long_record():
    from cyrene.plugins.model_router import request_token_estimate

    content = "长记忆" * 10000
    batches = editor.summary_batches([{"kind": "short", "label": "短期", "stale": True, "content": content}], 8192)
    assert len(batches) > 1
    assert "".join(text for batch in batches for group in batch for text in group["entries"]) == content
    for batch in batches:
        assert all(group["stale"] for group in batch)
        assert request_token_estimate([{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}], None) < 8192 - 2048


@pytest.mark.asyncio
async def test_empty_memory_summary_does_not_call_model(monkeypatch):
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=[]))
    model = AsyncMock()
    result = await editor.draft_memory(SimpleNamespace(model_gateway=SimpleNamespace(complete=model)), "__personal_assistant__")
    assert result["sourceCount"] == 0
    assert result["summary"] == "目前还没有保存的记忆。"
    model.assert_not_called()


@pytest.fixture
def persistent_service(tmp_path, monkeypatch, sources):
    from cyrene.plugins.builtin.cyrene_memory import project_memory
    from cyrene.workbench.persistence.store import ensure_schema

    database = tmp_path / "memory.sqlite"
    ensure_schema(database)
    monkeypatch.setattr(project_memory, "_STORE_DB_PATH", project_memory._STORE_DB_PATH)
    monkeypatch.setattr(editor, "collect_sources", AsyncMock(return_value=sources))

    def create():
        return project_memory.ProjectMemoryApplicationService(
            str(database), None, None, None,
            model_gateway=SimpleNamespace(complete=AsyncMock(
                return_value={"content": json.dumps({"summary": "保存的摘要"})})),
        )
    return create


@pytest.mark.asyncio
async def test_summary_survives_service_recreation_without_available_model(persistent_service, model_configuration):
    first = persistent_service()
    expected = await editor.draft_memory(first, "__personal_assistant__")
    first.model_gateway.complete.assert_awaited_once()
    second = persistent_service()
    model_configuration.profiles.clear()
    assert await editor.draft_memory(second, "__personal_assistant__") == expected
    second.model_gateway.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summary_refreshes_after_memory_changes_and_is_scoped(persistent_service, sources):
    service = persistent_service()
    first = await editor.draft_memory(service, "__personal_assistant__")
    sources[1]["content"] = "改用 Rust"
    service.model_gateway.complete.return_value = {"content": json.dumps({"summary": "更新的摘要"})}
    updated = await editor.draft_memory(service, "__personal_assistant__")
    assert updated["version"] != first["version"]
    assert updated["summary"] == "更新的摘要"
    assert await editor.draft_memory(service, "__personal_assistant__") == updated
    assert service.model_gateway.complete.await_count == 2
    await editor.draft_memory(service, "another-project")
    assert service.model_gateway.complete.await_count == 3


@pytest.mark.asyncio
async def test_concurrent_opens_generate_summary_once(persistent_service):
    service = persistent_service()
    first, second = await asyncio.gather(
        editor.draft_memory(service, "__personal_assistant__"),
        editor.draft_memory(service, "__personal_assistant__"),
    )
    assert first == second
    service.model_gateway.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_summary_is_not_cached(persistent_service):
    service = persistent_service()
    service.model_gateway.complete.return_value = {"content": '{"summary":""}'}
    with pytest.raises(ProjectMemoryApplicationError):
        await editor.draft_memory(service, "__personal_assistant__")
    service.model_gateway.complete.return_value = {"content": '{"summary":"重试成功"}'}
    result = await editor.draft_memory(service, "__personal_assistant__")
    assert result["summary"] == "重试成功"
    assert await editor.draft_memory(service, "__personal_assistant__") == result
    assert service.model_gateway.complete.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("instruction", ["", "改用 Rust"])
async def test_unsaved_draft_does_not_replace_cached_summary(persistent_service, sources, instruction):
    service = persistent_service()
    original = await editor.draft_memory(service, "__personal_assistant__")
    service.model_gateway.complete.return_value = {"content": '{"summary":"未保存的草稿"}'}
    draft = await editor.draft_memory(
        service, "__personal_assistant__", instruction=instruction,
        changes=[{"id": sources[1]["id"], "content": "使用 Rust", "stale": False}],
    )
    assert draft["summary"] == "未保存的草稿"
    assert await editor.draft_memory(service, "__personal_assistant__") == original
    assert service.model_gateway.complete.await_count == 2


@pytest.mark.asyncio
async def test_deleting_project_removes_saved_summary(persistent_service):
    from cyrene.plugins.builtin.cyrene_memory.project_memory import delete_project_memory
    from cyrene.workbench.persistence.store import list_document_keys

    service = persistent_service()
    await editor.draft_memory(service, "project")
    assert editor.summary_cache_key("project") in list_document_keys(service.db_path)
    delete_project_memory("project")
    assert editor.summary_cache_key("project") not in list_document_keys(service.db_path)

"""Native image admission, projection, lifetime and provider transport."""
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from PIL import Image

from cyrene.core.context.images import has_local_images, materialize_images, user_content
from cyrene.core.context.projection import project_model_messages, project_context_message
from cyrene.core.context.compaction import compact_messages, messages_token_estimate
from cyrene.workbench.chat.chat_attachment_service import image_inputs


@dataclass
class Node:
    id: str
    value: dict
    parent_id: str = ""


def image_node(path="/uploads/screenshot.png"):
    return Node("image", {"role": "user", "content": "Describe this", "run_id": "r1",
                          "metadata": {"input_images": [{"path": path, "width": 800, "height": 600}]}})


def test_projection_and_context_preserve_image_blocks(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (80, 60), "red").save(path)
    node = image_node(str(path))
    messages = project_model_messages([Node("root", {"role": "system", "content": "system"}), node])
    original = deepcopy(messages)
    project_context_message(messages, {"content": "turn instructions", "context_lifecycle": "turn"})
    assert has_local_images(messages)
    assert messages[-1]["content"][-1] == {"type": "text", "text": "turn instructions"}
    wire = materialize_images(messages)
    assert wire[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert has_local_images(messages)
    assert node.value["metadata"]["input_images"][0]["path"] == str(path)
    assert "base64" not in str(original)


@pytest.mark.parametrize("success", [True, False])
def test_successful_switch_retires_history_but_preserves_current_request(success):
    old = image_node()
    new = image_node("/uploads/current.png")
    new.id = "current"
    boundary = Node("switch", {"role": "tool_results", "results": [
        {"name": "switch_context", "success": success, "call_id": "switch", "value": {}}]})
    path = [Node("root", {"role": "system", "content": "system"}), old, new, boundary]
    messages = project_model_messages(path)
    assert has_local_images([messages[1]]) is (not success)
    assert has_local_images([messages[2]])
    assert "/uploads/screenshot.png" in str(messages[1])
    # Restoring the old branch after a subsequent user request cannot revive pixels.
    path.append(Node("next", {"role": "user", "content": "Continue"}))
    messages = project_model_messages(path)
    assert has_local_images(messages) is (not success)
    assert old.value["metadata"]["input_images"]  # immutable ledger


def test_compaction_replaces_images_even_in_recent_tail():
    messages = [{"role": "user", "content": "old " * 2000},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": user_content(image_node().value)}]
    result = compact_messages(messages, context_limit=1000, force=True)
    assert result.compacted
    assert not has_local_images(result.messages)
    assert "/uploads/screenshot.png" in str(result.messages)
    assert has_local_images(messages)
    assert messages_token_estimate([messages[-1]]) > 600


def test_attachment_selection_excludes_documents():
    assert image_inputs([{"path": "/uploads/image.png", "content_type": "application/octet-stream"},
                         {"path": "/uploads/doc.pdf", "content_type": "application/pdf"}]) == ({"path": "/uploads/image.png"},)


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_router_materializes_native_images_only_for_vision_models(monkeypatch, tmp_path, vision):
    from cyrene.core.plugin import Plugin, PluginContext, PluginRegistry
    from cyrene.plugins import model_router
    from cyrene.plugins.model_gateway import PluginModelGateway
    from test_vision_model_fallback import candidate, configure

    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20)).save(path)
    configure(monkeypatch, [], [candidate("selected", ("chat", "vision") if vision else ("chat",)),
                                candidate("text-fallback", ("chat",)), candidate("vision-fallback")])
    calls = []

    async def provider(arguments, context):
        calls.append(arguments)
        assert arguments["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        if arguments["model"] == "selected":
            raise RuntimeError("service unavailable")
        return {"content": "seen", "model": arguments["model"], "usage": {}}

    async def ignore(*args, **kwargs):
        pass

    monkeypatch.setattr(model_router, "remember_model_success", lambda *a, **k: None)
    for name in ("_publish_llm_event", "_publish_fallback", "_persist_fallback_result"):
        monkeypatch.setattr(model_router, name, ignore)
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(Plugin("TestVision", "test", {"type": "object"}, provider, kind="model",
                                   metadata={"provider": {"id": "openai", "name": "test"}}), source="test")
    messages = [{"role": "user", "content": user_content(image_node(str(path)).value)}]
    request = PluginModelGateway(registry).complete(messages, session_id="chat", context=PluginContext())
    if vision:
        assert (await request)["content"] == "seen"
        assert [call["model"] for call in calls] == ["selected", "vision-fallback"]
    else:
        with pytest.raises(RuntimeError, match="vision-capable") as failure:
            await request
        assert failure.value.code == "model_vision_required"
        assert calls == []
    assert has_local_images(messages)


def test_real_task_reload_and_restart_keep_only_historical_paths(tmp_path):
    import asyncio
    from test_task_contexts import make_session, command

    session = make_session(tmp_path)
    try:
        session.submit("Inspect", run_id="image", metadata=image_node().value["metadata"])
        asyncio.run(session.drain())
        task = session.task_contexts.read()["active"]
        assert has_local_images(session._messages(session.snapshot()["leaf_id"]))
        command(session, "unload_context", {"summary": "Image inspected"}, "unload")
        command(session, "load_context", {"context_id": task}, "reload")
        session.submit("Continue", run_id="next")
        asyncio.run(session.drain())
        messages = session._messages(session.snapshot()["leaf_id"])
        assert not has_local_images(messages)
        assert "/uploads/screenshot.png" in str(messages)
    finally:
        session.close()
    restored = make_session(tmp_path)
    try:
        assert not has_local_images(restored._messages(restored.snapshot()["leaf_id"]))
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_send_rejects_images_before_persistence(monkeypatch):
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    op = object.__new__(_SendOperation)
    op.is_external_agent = False
    op.chat_id = "chat"
    op.options = SimpleNamespace(lang="zh")

    async def ok(*args):
        return None

    async def environment():
        return SimpleNamespace(model=SimpleNamespace(candidate={"capabilities": ["chat"]})), None

    async def turn():
        return SimpleNamespace(input=SimpleNamespace(normalized=[{"path": "/uploads/test.png"}])), None

    async def forbidden():
        pytest.fail("image rejection must precede persistence")

    op._parse_request = ok
    op._load_chat = ok
    op._load_project_and_model = environment
    op._prepare_user_turn = turn
    op._persist_user_turn = forbidden
    result = await op.execute()
    assert result.status_code == 400
    assert "model_vision_required" in result.body.decode()


@pytest.mark.asyncio
async def test_runtime_persists_image_paths_with_user_turn(tmp_path, monkeypatch):
    from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime
    runtime = ConversationRuntime()
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="",
                                input_images=({"path": "/uploads/image.png"},))
    captured = {}
    result = object()

    class Bridge:
        def snapshot(self):
            return {"status": "idle", "run_id": ""}

        async def submit_result(self, text, **kwargs):
            captured.update(kwargs)
            return result

    async def use_bridge(config, operation, **kwargs):
        return await operation(Bridge())

    monkeypatch.setattr(runtime, "_with_bridge", use_bridge)
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda *args: None)
    assert await runtime.send(config, "Look", run_id="r") is result
    assert captured["metadata"]["input_images"] == [{"path": "/uploads/image.png"}]
    assert "base64" not in str(captured)


def test_image_prompt_does_not_require_attachment_tool():
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation
    op = object.__new__(_SendOperation)
    op.is_external_agent = op.is_side_agent = False
    op.options = SimpleNamespace(lang="zh")
    op.turn = SimpleNamespace(input=SimpleNamespace(message="Look", normalized=[
        {"path": "/uploads/image.png", "content_type": "image/png"}]))
    seen = []
    op.routes = SimpleNamespace(attachment_prompt_block=lambda items, **kwargs: seen.append(items) or "")
    op._build_agent_message()
    assert seen == [[]]
    assert "/uploads/image.png" in op.agent_message
    assert "attachment-analysis" not in op.agent_message

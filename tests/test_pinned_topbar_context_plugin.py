from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from cyrene.core.context import ContextStoreRouter
from cyrene.core.plugin import PluginRegistry, PluginSetupContext
from cyrene.plugins.builtin.cyrene_plugin_development.tools import (
    validate_plugin_source,
)
from cyrene.workbench.chat.chat_repository import ChatRepository


def _plugin_source() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "plugins"
        / "pinned_topbar_context"
    )


def test_pinned_topbar_context_mounts_resources_added_between_user_turns(
    tmp_path,
    monkeypatch,
):
    from cyrene.platform import attachments
    from cyrene.workbench.chat import chat_application, pinned_resources

    monkeypatch.setattr(
        chat_application,
        "public_chat_light",
        lambda chat: {
            "title": chat.get("title"),
            "status": chat.get("status"),
            "runStatus": "idle",
            "updatedAt": chat.get("updatedAt"),
        },
    )

    source = _plugin_source()
    assert validate_plugin_source(source)["ok"] is True

    plugin_root = tmp_path / "plugin_impl"
    plugin_root.mkdir()
    shutil.copytree(source, plugin_root / "pinned_topbar_context")
    registry = PluginRegistry(include_core=False)
    assert registry.load_directory(plugin_root) == ()
    pack = next(
        item for item in registry.list_packs()
        if item.id == "pinned_topbar_context"
    )

    db_path = tmp_path / "workbench.sqlite3"
    export_dir = tmp_path / "exports"
    monkeypatch.setattr(attachments, "EXPORTS_DIR", export_dir)
    pinned_resources.configure(str(db_path))

    first_file = tmp_path / "first.txt"
    first_file.write_text("first pinned file", encoding="utf-8")
    first = pinned_resources.upsert_resource({
        "kind": "file",
        "ownerSessionId": "source-chat",
        "name": first_file.name,
        "path": str(first_file),
    })

    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree(tree_id="target-chat", root_id="root")
    hooks = store.hooks_for(tree.id)
    assert pack.setup is not None
    setup_context = PluginSetupContext(
        data_directory=tmp_path / "data",
        plugin_directory=plugin_root,
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        root_id=tree.root_id,
        hooks=hooks,
        data={"db_path": str(db_path)},
        services={},
    )
    pack.setup(setup_context)

    first_turn = asyncio.run(hooks.turn_start_mounts())
    assert len(first_turn) == 1
    assert first_turn[0]["context_kind"] == "pinned_topbar_resources"
    assert first_turn[0]["context_source"] == "pinned_topbar_context"
    assert first["id"] in first_turn[0]["context"]
    store.mount(tree.id, tree.root_id, {
        "role": "context",
        "content": first_turn[0]["context"],
        "context_kind": first_turn[0]["context_kind"],
    })
    # Rebinding simulates reopening the conversation; persisted snapshots still
    # participate in change detection instead of being injected again.
    pack.setup(setup_context)
    assert asyncio.run(hooks.turn_start_mounts()) == ()

    snippet = pinned_resources.upsert_resource({
        "kind": "snippet",
        "ownerSessionId": "source-chat",
        "stableRef": "snippet:source-chat:after-first-turn",
        "title": "后来固定的文字",
        "text": "这段内容在第一次用户消息之后才固定。",
    })
    peer_chat = {
        "id": "peer-chat",
        "projectId": "project-1",
        "kind": "chat",
        "title": "后来固定的对话",
        "status": "idle",
        "soulActive": True,
        "workspaceActive": True,
        "contextActivations": {},
        "createdAt": "2026-08-29T11:59:00+00:00",
        "updatedAt": "2026-08-29T12:00:00+00:00",
        "messages": [
            {"role": "user", "content": "整理发布说明"},
            {"role": "assistant", "content": "发布说明已经整理完成。"},
        ],
        "generatedFiles": [{"name": "release-notes.md"}],
    }
    ChatRepository(str(db_path)).write({"chats": [peer_chat]})
    conversation = pinned_resources.upsert_resource({
        "kind": "conversation",
        "ownerSessionId": "peer-chat",
        "conversationId": "peer-chat",
        "ownerProjectId": "project-1",
        "ownerProjectName": "Demo",
        "title": peer_chat["title"],
    })

    second_turn = asyncio.run(hooks.turn_start_mounts())
    second_context = second_turn[0]["context"]
    assert snippet["id"] not in first_turn[0]["context"]
    assert conversation["id"] not in first_turn[0]["context"]
    assert snippet["id"] in second_context
    assert "后来固定的文字.md" in second_context
    assert conversation["id"] in second_context
    assert "整理发布说明" in second_context
    assert "发布说明已经整理完成" in second_context
    assert "release-notes.md" in second_context
    assert asyncio.run(hooks.turn_start_mounts()) == ()

    assert pinned_resources.remove_resource(first["id"]) is True
    assert pinned_resources.remove_resource(snippet["id"]) is True
    assert pinned_resources.remove_resource(conversation["id"]) is True
    cleared_turn = asyncio.run(hooks.turn_start_mounts())
    assert len(cleared_turn) == 1
    assert "No Workbench resources are currently pinned" in cleared_turn[0]["context"]
    assert asyncio.run(hooks.turn_start_mounts()) == ()

    store.close()

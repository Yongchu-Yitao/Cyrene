"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path


def _model_registry():
    from cyrene.core.plugin import Plugin, PluginRegistry

    registry = PluginRegistry()
    registry.register_plugin(
        Plugin(
            name="PromptTestModel",
            description="Unused model for prompt projection tests.",
            input_schema={"type": "object"},
            handler=lambda _arguments, _context: {"content": "", "tool_calls": []},
            kind="model",
        ),
        source="test",
    )
    return registry


def test_default_prompt_requires_plugin_discovery_for_external_information():
    from cyrene.plugins.builtin.cyrene_system_prompt.system_prompt import SYSTEM_PROMPT

    prompt = " ".join(SYSTEM_PROMPT.split())
    assert "current" in SYSTEM_PROMPT
    assert "external information" in SYSTEM_PROMPT
    assert "do not rely on memory" in SYSTEM_PROMPT
    from cyrene.core.plugin import PluginRegistry
    from cyrene.core.plugin.core_impl.toolbox import create_toolbox_plugin
    from cyrene.core.plugin.core_impl.write import WRITE_PLUGIN

    toolbox = create_toolbox_plugin(PluginRegistry()).description
    assert "toolbox.list" in toolbox
    assert "toolbox.describe" in toolbox
    assert "toolbox.invoke" in toolbox
    assert "a one-sentence purpose for each pack" in toolbox
    assert "correct the request and retry" in toolbox
    assert "toolbox.list" not in SYSTEM_PROMPT
    assert "WebSearch proactively" in SYSTEM_PROMPT
    assert "use it at the beginning" in SYSTEM_PROMPT
    assert "Prefer frequent useful updates" in SYSTEM_PROMPT
    assert "each part has been satisfied" in prompt
    assert "first call concerning that file must be Read, Edit, or Write" in prompt
    assert "Do not inspect it first with Bash" in prompt
    assert "treat the edit as a successful idempotent no-op" in prompt
    assert "Still complete the display obligation" in prompt
    assert "An argument error does not mean the tool is unavailable" in prompt
    assert "without a fixed character limit" in WRITE_PLUGIN.description
    assert "first chunk" in WRITE_PLUGIN.description
    assert "append later chunks" in WRITE_PLUGIN.description
    assert "separate tool-call turns" in WRITE_PLUGIN.description
    assert "stable boundaries" in WRITE_PLUGIN.description
    assert "never use overwrite to continue a file" in WRITE_PLUGIN.description
    assert "verify the assembled file" in WRITE_PLUGIN.description
    assert "overwrite to continue" not in prompt


def test_default_prompt_keeps_internal_work_out_of_user_facing_messages():
    from cyrene.plugins.builtin.cyrene_system_prompt.system_prompt import SYSTEM_PROMPT

    prompt = " ".join(SYSTEM_PROMPT.split())
    assert "Keep communication concise, user-facing, and focused on results" in prompt
    assert "Do not volunteer internal information" in prompt
    assert "the names of tools being used" in prompt
    assert "describe the intended action or result instead" in prompt
    assert "If the user asks for those details" in prompt
    assert "provide the relevant information directly" in prompt
    assert "Never expose secrets" in prompt


def test_bash_prompt_does_not_claim_shell_output_opens_workspace_files():
    from cyrene.core.plugin.core_impl.bash import BASH_PLUGIN

    assert "Shell file-printing output does not satisfy" in BASH_PLUGIN.description
    assert "workspace UI" in BASH_PLUGIN.description


def test_identity_is_owned_by_soul_and_existing_personas_are_preserved(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_soul import store
    from cyrene.plugins.builtin.cyrene_system_prompt.system_prompt import SYSTEM_PROMPT

    assert not SYSTEM_PROMPT.lower().startswith("you are")
    assert "universal assistant" not in SYSTEM_PROMPT
    assert "universal assistant" in store.default_soul(language="en")
    assert "not an assistant" not in store.default_soul(language="en")
    assert "通用助手" in store.default_soul(language="zh")
    path = tmp_path / "SOUL.md"
    custom = "# Custom persona\n\n## SELF:IDENTITY\nI am a patient tutor.\n"
    path.write_text(custom)
    monkeypatch.setattr(store, "soul_path", lambda: path)
    store.ensure_soul()
    assert path.read_text() == custom
    assert store.read_persona_context() == custom.strip()


def test_web_search_and_mid_run_message_are_direct_tools():
    from cyrene.core.plugin import PluginRegistry
    from cyrene.plugins.builtin.cyrene_content import plugin_pack as content_pack
    from cyrene.plugins.builtin.cyrene_delivery import plugin_pack as delivery_pack

    registry = PluginRegistry(include_core=False)
    registry.register_pack(content_pack, source="test-content")
    registry.register_pack(delivery_pack, source="test-delivery")

    direct_names = {
        definition["function"]["name"]
        for definition in registry.direct_tool_definitions()
    }
    assert {"WebSearch", "send_message"} <= direct_names


def test_reopened_tree_mounts_system_prompt_from_required_plugin(tmp_path):
    from cyrene.plugins.builtin.cyrene_system_prompt.system_prompt import SYSTEM_PROMPT
    from cyrene.core.session import AgentSession

    plugin_directory = tmp_path / "plugin_impl"
    shutil.copytree(
        Path(__file__).parents[1]
        / "src" / "cyrene" / "plugins" / "builtin"
        / "cyrene_system_prompt",
        plugin_directory / "cyrene_system_prompt",
    )
    first = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="chat",
        registry=_model_registry(),
        model_plugin="PromptTestModel",
    )
    assert asyncio.run(first.hooks.session_start()).startswith(SYSTEM_PROMPT.strip())
    assert first.initial_root_value == {"role": "system", "content": ""}
    first.close()

    reopened = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="chat",
        registry=_model_registry(),
        model_plugin="PromptTestModel",
    )
    try:
        context = asyncio.run(reopened.hooks.session_start())
        assert context.startswith(SYSTEM_PROMPT.strip())
        assert reopened.initial_root_value == {"role": "system", "content": ""}
        root = reopened.store.get_node(reopened.tree.id, reopened.tree.root_id)
        assert {key: value for key, value in root.value.items() if key != "_task_contexts"} == {"role": "system", "content": ""}
        assert root.value["_task_contexts"]["shared"] == {"body": ""}
    finally:
        reopened.close()


def test_unread_count_initializes_a_missing_session_inbox(monkeypatch, tmp_path):
    from cyrene.platform import inbox

    root = tmp_path / "inbox"
    monkeypatch.setattr(inbox, "INBOX_DIR", root)

    assert inbox.get_unread_count("main", session_id="chat") == 0
    assert (root / "chat" / "main" / ".unread").read_text(encoding="utf-8") == "0"
    assert not (root / "chat" / "main" / ".unread.tmp").exists()


def test_peek_messages_is_a_read_only_snapshot(monkeypatch, tmp_path):
    from cyrene.platform import inbox

    root = tmp_path / "inbox"
    target = root / "chat" / "main"
    target.mkdir(parents=True)
    message_path = target / "msg_001.json"
    message = {
        "message_id": "msg_001",
        "from": "worker",
        "to": "main",
        "type": "result",
        "content": "done",
        "round_id": "run_1",
    }
    message_path.write_text(json.dumps(message), encoding="utf-8")
    monkeypatch.setattr(inbox, "INBOX_DIR", root)

    assert inbox.peek_messages("main", session_id="chat") == {
        "messages": [message],
        "roundId": "run_1",
        "limit": 100,
        "eventsTruncated": False,
        "historyWindowTruncated": False,
    }
    assert json.loads(message_path.read_text(encoding="utf-8")) == message
    assert not (target / ".unread").exists()


def test_peek_messages_uses_numeric_sequence_and_bounds_the_window(
    monkeypatch,
    tmp_path,
):
    from cyrene.platform import inbox

    root = tmp_path / "inbox"
    target = root / "chat" / "main"
    target.mkdir(parents=True)
    for message_id, round_id in (
        ("msg_998", "old"),
        ("msg_999", "run_1"),
        ("msg_1000", "run_1"),
    ):
        (target / f"{message_id}.json").write_text(
            json.dumps({
                "message_id": message_id,
                "round_id": round_id,
                "content": message_id,
            }),
            encoding="utf-8",
        )
    monkeypatch.setattr(inbox, "INBOX_DIR", root)

    snapshot = inbox.peek_messages(
        "main",
        session_id="chat",
        round_id="run_1",
        limit=1,
    )

    assert [item["message_id"] for item in snapshot["messages"]] == ["msg_1000"]
    assert snapshot["eventsTruncated"] is True
    assert snapshot["historyWindowTruncated"] is True


def test_inbox_fifo_order_stays_numeric_after_message_999(monkeypatch, tmp_path):
    from cyrene.platform import inbox

    root = tmp_path / "inbox"
    target = root / "fifo" / "main"
    target.mkdir(parents=True)
    for message_id in ("msg_999", "msg_1000"):
        (target / f"{message_id}.json").write_text(
            json.dumps({
                "message_id": message_id,
                "round_id": "run_1",
                "content": message_id,
                "read": False,
                "delivery_ready": True,
            }),
            encoding="utf-8",
        )
    monkeypatch.setattr(inbox, "INBOX_DIR", root)

    unread = asyncio.run(inbox.read_unread_messages("main", session_id="fifo"))
    assert [item["message_id"] for item in unread] == ["msg_999", "msg_1000"]

    asyncio.run(inbox.mark_read_count("main", 1, session_id="fifo"))
    unread = asyncio.run(inbox.read_unread_messages("main", session_id="fifo"))
    assert [item["message_id"] for item in unread] == ["msg_1000"]

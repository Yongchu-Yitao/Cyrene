"""Deep-reflection context rewriting and conversation-archive integration."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from cyrene.core import AgentSession
from cyrene.core.context import ContextStoreRouter
from cyrene.core.hook import HookEvent, SESSION_END
from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.plugins.builtin.cyrene_control.deep_reflect import (
    DeepReflectionService,
    REFLECTION_SCHEMA,
    TOOL_METADATA,
)
from cyrene.plugins.builtin.cyrene_memory.archive import (
    archive_session_exchange,
    load_session_conversation_entries,
)
from cyrene.plugins.builtin.cyrene_memory.service import MemoryService
from cyrene.workbench.chat.chat_application import ContextTreeTranscript


def run(coroutine):
    return asyncio.run(coroutine)


def _valid_reflection() -> dict:
    return {
        "goal": "finish the actual task",
        "hard_constraints": ["preserve user wording"],
        "verified_facts": ["the old approach did not converge"],
        "completed_work": [],
        "failure_diagnosis": [
            {
                "claim": "the approach was aimed at the wrong layer",
                "evidence": "the supplied agent trace",
                "confidence": "high",
            }
        ],
        "assumptions_to_drop": ["the old layer is authoritative"],
        "chosen_direction": "edit the active ContextTree",
        "next_actions": ["continue from the replacement pack"],
        "success_check": "the next model call sees the Reflect Pack",
        "compressed_agent_trace": [
            {
                "attempt": "used the old approach",
                "result": "did not converge",
                "lesson": "switch layers",
            }
        ],
        "compressed_tool_trace": [],
    }


class _RetryingReflectionGateway:
    def __init__(self, *, fail_once: bool = True) -> None:
        self.fail_once = fail_once
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        assert kwargs["context"].tree is None
        assert kwargs["context"].node_id is None
        if self.fail_once and len(self.calls) == 1:
            return {"content": "not json"}
        return {
            "content": json.dumps(_valid_reflection(), ensure_ascii=False),
            "model": "reflection-model",
            "usage": {"total_tokens": 123},
        }


def test_reflection_retries_without_truncating_evidence_and_preserves_user_order(
    tmp_path,
):
    archive_session_exchange(
        "chat",
        "first original user message",
        "first answer",
        workspace_dir=tmp_path,
        round_id="run-1",
    )
    archive_session_exchange(
        "chat",
        "second original user message",
        "second answer",
        workspace_dir=tmp_path,
        round_id="run-2",
    )
    store = ContextStoreRouter(tmp_path / "context")
    tree = store.create_tree({"role": "system"}, tree_id="chat", root_id="root")
    long_user_text = "current exact user text " + ("用" * 20_000)
    user = store.mount(
        tree.id,
        tree.root_id,
        {
            "role": "user",
            "content": "internal decorated prompt",
            "run_id": "run-3",
            "metadata": {"public_user_message": long_user_text},
        },
        node_id="user-3",
    )
    assistant = store.mount(
        tree.id,
        user.id,
        {
            "role": "assistant",
            "content": "old response " + ("答" * 20_000),
            "reasoning": "full reasoning trace",
            "tool_calls": [],
            "run_id": "run-3",
        },
        node_id="assistant-3",
    )
    gateway = _RetryingReflectionGateway()
    context = PluginContext(
        workspace=tmp_path,
        tree=store,
        tree_id=tree.id,
        node_id=assistant.id,
        services={"model": gateway},
    )

    pack = run(
        DeepReflectionService().reflect(
            store.get_path(tree.id, assistant.id),
            {"goal_gap": "the approach is wrong"},
            context,
        )
    )

    assert len(gateway.calls) == 2
    assert all("max_tokens" not in call for call in gateway.calls)
    submitted_evidence = gateway.calls[-1]["messages"][-1]["content"]
    assert long_user_text in submitted_evidence
    assert "答" * 20_000 in submitted_evidence
    assert pack["reflection_attempts"] == 2
    assert [
        message["content"]
        for message in pack["model_context"]["user_messages"]
    ] == [
        "first original user message",
        "second original user message",
        long_user_text,
    ]
    assert pack["schema"] == REFLECTION_SCHEMA
    assert [node.id for node in store.get_subtree(tree.id, tree.root_id)] == [
        "root",
        "user-3",
        "assistant-3",
    ]
    store.close()


def test_deep_reflect_tool_rewrites_context_then_continues_without_changing_ui(
    tmp_path,
):
    model_inputs: list[list[dict]] = []

    async def model(arguments, _context):
        model_inputs.append(arguments["messages"])
        if any(message.get("reflect_pack") is True for message in arguments["messages"]):
            return {"content": "final answer from better direction", "tool_calls": []}
        return {
            "content": "visible pre-reflection reply",
            "reasoning": "the first approach is not converging",
            "tool_calls": [
                {
                    "id": "reflect-call",
                    "name": "DeepReflect",
                    "arguments": {"goal_gap": "wrong approach"},
                }
            ],
        }

    async def forbidden_handler(_arguments, _context):
        raise AssertionError("DeepReflect must be handled as a session transition")

    gateway = _RetryingReflectionGateway()

    def setup(context):
        context.provide("deep_reflection", DeepReflectionService(), replace=True)

    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (Plugin("MiniMax", "fake", {"type": "object"}, model, kind="model"),),
        ),
        source="test",
    )
    registry.register_pack(
        PluginPack(
            "reflection",
            "test reflection control",
            (
                Plugin(
                    "DeepReflect",
                    "rewrite context",
                    {"type": "object"},
                    forbidden_handler,
                    metadata={**TOOL_METADATA, "main_only": True},
                ),
            ),
            setup=setup,
        ),
        source="test",
    )
    data_directory = tmp_path / "agent-state"
    plugin_directory = tmp_path / "plugin_impl"
    plugin_directory.mkdir()
    session = AgentSession(
        data_directory,
        tmp_path,
        plugin_directory,
        tree_id="chat",
        registry=registry,
        plugin_services={"model": gateway},
    )
    session._configured_compaction_limit = lambda: 0
    original = session.submit(
        "internal prompt",
        run_id="run-reflect",
        metadata={"public_user_message": "exact visible user message"},
    )
    run(session.drain())

    nodes = session.store.get_subtree(session.tree.id, session.tree.root_id)
    assert [node.value.get("role") for node in nodes] == [
        "system",
        "context_reflection",
        "assistant",
    ]
    reflected = nodes[1]
    assert reflected.id == original.id
    assert reflected.value["model_context"]["user_messages"][-1]["content"] == (
        "exact visible user message"
    )
    assert reflected.value["reflection_attempts"] == 2
    assert nodes[-1].value["content"] == "final answer from better direction"
    assert len(model_inputs) == 2
    assert [message["role"] for message in model_inputs[-1]] == ["system", "user"]
    assert model_inputs[-1][-1]["reflect_pack"] is True

    transcript = ContextTreeTranscript(str(tmp_path / "workbench.sqlite3"))
    visible = transcript.messages("chat")
    assert [message.get("role") for message in visible] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert visible[0]["content"] == "internal prompt"
    assert visible[0]["metadata"]["public_user_message"] == (
        "exact visible user message"
    )
    assert visible[1]["content"] == "visible pre-reflection reply"
    assert visible[-1]["content"] == "final answer from better direction"
    assert visible[0]["id"] == original.id
    session.close()


def test_memory_session_end_hook_keeps_workspace_conversation_archive(tmp_path):
    service = MemoryService(
        workspace=tmp_path,
        tree=None,
        tree_id="chat",
        data={
            "session_id": "chat",
            "memory_archive_enabled": True,
            "memory_write_enabled": False,
        },
    )
    event = HookEvent(
        SESSION_END,
        "chat",
        datetime.now(timezone.utc),
        payload={
            "status": "completed",
            "run_id": "run-archive",
            "user_request": "decorated internal request",
            "assistant_text": "archived answer",
            "metadata": {"public_user_message": "original visible request"},
        },
        is_root=True,
    )

    run(service.on_session_end(event))

    entries = load_session_conversation_entries("chat", tmp_path)
    assert (tmp_path / ".cyrene" / "conversations" / "chat.md").is_file()
    assert [entry["round_id"] for entry in entries] == ["run-archive"]
    assert entries[0]["user_body"] == "original visible request"
    assert entries[0]["assistant_body"] == "archived answer"

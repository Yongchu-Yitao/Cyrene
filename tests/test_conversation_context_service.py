from types import SimpleNamespace

import pytest

from cyrene.workbench.chat.conversation_context_service import (
    AgentContextRepository,
    ConversationContextQueryService,
    ConversationContextUpdateError,
    ConversationInboxQueryService,
    _agent_path_messages,
    _agent_path_usage,
    _agent_path_plugin_usage,
)


class _Chats:
    def __init__(self, chat):
        self.chat = chat
        self.read_calls = 0

    def read(self):
        self.read_calls += 1
        return {"chats": [self.chat] if self.chat else []}

    def get(self, chat_id):
        self.read_calls += 1
        return self.chat if self.chat and self.chat["id"] == chat_id else None

    def find(self, payload, chat_id):
        return next(
            (item for item in payload["chats"] if item["id"] == chat_id),
            None,
        )


def _context_service(tmp_path, chat, *, agent_states=None, context_limit=None):
    return ConversationContextQueryService(
        chats=_Chats(chat),
        agent_states=(
            agent_states
            if agent_states is not None
            else AgentContextRepository(tmp_path / "missing-agent-context")
        ),
        default_model=lambda: "model",
        context_limit=context_limit or (lambda _model: 128_000),
        approx_token_count=lambda text: len(text),
    )


def test_agent_path_usage_normalizes_openai_cache_details():
    nodes = [
        SimpleNamespace(value={
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 8},
            },
            "auxiliary_usage": [{
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                    "cached_prompt_tokens": 1,
                },
            }],
        }),
    ]

    assert _agent_path_usage(nodes) == {
        "prompt_tokens": 14,
        "completion_tokens": 3,
        "total_tokens": 17,
        "prompt_cache_hit_tokens": 9,
        "prompt_cache_miss_tokens": 5,
    }


def test_agent_path_messages_preserve_each_turn_suffix_and_stable_system_prefix():
    nodes = [
        SimpleNamespace(id="root", value={"role": "system", "content": "base"}),
        SimpleNamespace(id="user-1", value={
            "role": "user", "content": "first", "run_id": "run-1",
        }),
        SimpleNamespace(id="stable-1", value={
            "role": "context",
            "content": "stable",
            "context_kind": "stable",
            "context_lifecycle": "session",
            "run_id": "run-1",
        }),
        SimpleNamespace(id="turn-1", value={
            "role": "context",
            "content": "dynamic-1",
            "context_kind": "third_party_turn",
            "context_lifecycle": "turn",
            "run_id": "run-1",
        }),
        SimpleNamespace(id="assistant-1", value={
            "role": "assistant", "content": "answer-1", "run_id": "run-1",
        }),
        SimpleNamespace(id="user-2", value={
            "role": "user", "content": "second", "run_id": "run-2",
        }),
        SimpleNamespace(id="stable-2", value={
            "role": "context",
            "content": "stable",
            "context_kind": "stable",
            "context_lifecycle": "session",
            "run_id": "run-2",
        }),
        SimpleNamespace(id="turn-2", value={
            "role": "context",
            "content": "dynamic-2",
            "context_kind": "third_party_turn",
            "context_lifecycle": "turn",
            "run_id": "run-2",
        }),
    ]

    assert _agent_path_messages(nodes) == [
        {"role": "system", "content": "base\n\nstable"},
        {"role": "user", "content": "first\n\ndynamic-1"},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": "second\n\ndynamic-2"},
    ]


def test_mounted_ephemeral_context_is_not_projected_as_a_second_layer(tmp_path):
    service = _context_service(tmp_path, {"id": "chat_1", "model": "model"})
    state = {
        "messages": [
            {
                "role": "system",
                "content": "system prompt\n\nmemory\n\nturn context",
            },
            {"role": "user", "content": "inspect"},
        ],
        "systemPrompt": "system prompt",
        "ephemeralContext": "turn context",
        "contextMounts": [{
            "kind": "plugin_session",
            "content": "memory\n\nturn context",
            "source": "hook",
        }],
    }

    blocks = service._agent_blocks({"model": "model"}, state)
    summary = service._agent_summary(state, model_name="model", ctx_limit=128_000)

    assert [layer["id"] for layer in blocks["layers"]] == [
        "system_prefix",
        "messages",
    ]
    assert blocks["contextUsed"] == summary["ctxUsed"]


def test_system_message_framing_overhead_is_not_split_across_prompt_sections(tmp_path):
    service = _context_service(tmp_path, {"id": "chat_1", "model": "model"})
    prompt = "You are A.\nFollow rules.\ntoolbox. Use tools."
    state = {
        "messages": [{"role": "system", "content": prompt}],
        "systemPrompt": prompt,
        "rootSystemPrompt": "",
        "contextMounts": [{
            "kind": "system_prompt",
            "content": prompt,
            "source": "cyrene_system_prompt",
        }],
    }

    blocks = service._agent_blocks({"model": "model"}, state)
    system_blocks = blocks["layers"][0]["blocks"]

    assert [block["id"] for block in system_blocks] == [
        "system.identity",
        "system.behavior",
        "system.tools",
        "system.message_overhead",
    ]
    assert system_blocks[-1] == {
        "id": "system.message_overhead",
        "type": "overhead",
        "tokens_est": 10,
        "chars": 0,
        "source": "message_envelope",
        "reason": "message_overhead",
    }


def test_plugin_session_context_is_disclosed_per_contributor(tmp_path):
    service = _context_service(tmp_path, {"id": "chat_1", "model": "model"})
    state = {
        "messages": [{
            "role": "system",
            "content": "persona\n\nmemory\n\nlearned",
        }],
        "rootSystemPrompt": "",
        "contextMounts": [
            {"kind": "persona", "content": "persona", "source": "cyrene_soul"},
            {"kind": "memory", "content": "memory", "source": "cyrene_memory"},
            {
                "kind": "learned_skills",
                "content": "learned",
                "source": "cyrene_skills",
            },
        ],
    }

    blocks = service._agent_blocks({"model": "model"}, state)
    system_blocks = blocks["layers"][0]["blocks"]

    assert [block["id"] for block in system_blocks[:3]] == [
        "context.persona",
        "context.memory",
        "context.learned_skills",
    ]
    assert [block["source"] for block in system_blocks[:3]] == [
        "cyrene_soul",
        "cyrene_memory",
        "cyrene_skills",
    ]


@pytest.mark.asyncio
async def test_new_agent_context_tree_drives_summary_blocks_and_plugin_usage(tmp_path):
    from cyrene.core.context import ContextStoreRouter

    context_directory = tmp_path / "agent-context"
    router = ContextStoreRouter(context_directory)
    tree = router.create_tree(
        {
            "role": "system",
            "content": "system prompt",
            "_plugin_session_state": {
                "cyrene_subagent": {
                    "child_context_ids": [],
                    "public_snapshot": {
                        "subagents": {
                            "reader": {
                                "task": "inspect the files",
                                "status": "done",
                                "result": "all clear",
                                "round_id": "run_1",
                            },
                        },
                    },
                },
            },
        },
        tree_id="chat_1",
        root_id="root",
    )
    user = router.mount(
        tree.id,
        tree.root_id,
        {
            "role": "user",
            "content": "inspect",
            "run_id": "run_1",
            "metadata": {"ephemeral_context": "turn context"},
        },
        node_id="user",
    )
    context = router.mount(
        tree.id,
        user.id,
        {
            "role": "context",
            "content": "project memory",
            "context_kind": "project_memory",
            "source_node_id": "user",
            "run_id": "run_1",
        },
        node_id="context",
    )
    assistant = router.mount(
        tree.id,
        context.id,
        {
            "role": "assistant",
            "content": "",
            "model": "actual-model",
            "model_identity": {
                "candidateId": "actual-candidate",
                "model": "actual-model",
            },
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
            "auxiliary_usage": [{
                "kind": "permission",
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            }],
            "effect_results": {
                "call_1": {
                    "call_id": "call_1",
                    "name": "toolbox",
                    "success": True,
                    "value": {
                        "operation": "invoke",
                        "name": "subagent.spawn",
                        "pack": "cyrene_subagent",
                        "result": "spawned",
                    },
                    "error": "",
                },
                "call_3": {
                    "call_id": "call_3",
                    "name": "toolbox",
                    "success": True,
                    "value": {
                        "operation": "invoke",
                        "name": "Glob",
                        "pack": "cyrene_code",
                        "result": [],
                    },
                    "error": "",
                },
            },
            "tool_calls": [{
                "id": "call_1",
                "name": "toolbox",
                "arguments": {
                    "operation": "invoke",
                    "name": "subagent.spawn",
                    "arguments": {"agent_id": "reader", "task": "inspect"},
                },
            }, {
                "id": "call_2",
                "name": "toolbox",
                "arguments": {
                    "operation": "invoke",
                    "name": "CustomLint",
                    "arguments": {},
                },
            }],
            "run_id": "run_1",
        },
        node_id="assistant_1",
    )
    tools = router.mount(
        tree.id,
        assistant.id,
        {
            "role": "tool_results",
            "run_id": "run_1",
            "results": [{
                "call_id": "call_1",
                "name": "toolbox",
                "success": True,
                "value": {
                    "operation": "invoke",
                    "name": "subagent.spawn",
                    "pack": "cyrene_subagent",
                    "result": "spawned",
                },
                "error": "",
            }, {
                "call_id": "call_2",
                "name": "toolbox",
                "success": True,
                "value": {
                    "operation": "invoke",
                    "name": "CustomLint",
                    "pack": None,
                    "result": "clean",
                },
                "error": "",
            }],
        },
        node_id="tools",
    )
    router.mount(
        tree.id,
        tools.id,
        {
            "role": "assistant",
            "content": "done",
            "model": "actual-model",
            "model_identity": {
                "candidateId": "actual-candidate",
                "model": "actual-model",
            },
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
            },
            "run_id": "run_1",
            "session_end_complete": True,
        },
        node_id="assistant_2",
    )
    router.close()

    resolved_models = []
    service = _context_service(
        tmp_path,
        {"id": "chat_1", "model": "selected-model"},
        agent_states=AgentContextRepository(context_directory),
        context_limit=lambda model: resolved_models.append(model) or 128_000,
    )

    summary = await service.summary("chat_1")
    blocks = await service.blocks("chat_1")
    subagents = await service.subagents("chat_1", "")
    activity_messages = await service.activity_messages("chat_1")

    assert summary["compositionSource"] == "agent_tree"
    assert len(activity_messages) == 1
    assert [entry["text"] for entry in activity_messages[0]["trace"]] == [
        "subagent.spawn",
        "CustomLint",
    ]
    assert summary["model"] == "actual-model"
    assert summary["selectedModel"] == "selected-model"
    assert summary["actualModel"] == "actual-model"
    assert summary["modelIdentity"]["candidateId"] == "actual-candidate"
    assert summary["usage"] == {
        "prompt_tokens": 34,
        "completion_tokens": 6,
        "total_tokens": 40,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    assert summary["ctxLimit"] == 128_000
    assert resolved_models == ["actual-candidate", "actual-candidate"]
    assert summary["ctxUsed"] > 0
    assert summary["ratio"] == summary["ctxUsed"] / 128_000
    assert summary["messageCount"] == 6
    assert summary["usedPluginPacks"] == ["cyrene_subagent", "cyrene_code"]
    assert "usedStandaloneTools" not in summary
    assert [item["key"] for item in summary["segments"]] == [
        "compacted", "system", "user", "assistant", "tool",
    ]
    segments = {item["key"]: item["tokens"] for item in summary["segments"]}
    assert segments["system"] == 39
    assert sum(segments.values()) == summary["ctxUsed"]
    assert blocks["compositionSource"] == "agent_tree"
    assert [layer["id"] for layer in blocks["layers"]] == [
        "system_prefix", "ephemeral", "messages",
    ]
    assert blocks["contextUsed"] == blocks["totalTokensEst"]
    assert blocks["contextUsed"] == summary["ctxUsed"]
    assert blocks["layers"][0]["totalTokens"] == 39
    assert blocks["layers"][0]["blocks"] == [{
        "id": "system.behavior",
        "type": "instructions",
        "tokens_est": 13,
        "chars": 13,
        "source": "context_tree",
        "reason": "behavior",
        "content": "system prompt",
        "nodeId": "root",
        "contextKind": "system_prompt",
        "lifecycle": "persistent",
        "createdAt": blocks["timeline"][0]["createdAt"],
        "updatedAt": blocks["timeline"][0]["updatedAt"],
        "nodeContent": "system prompt",
        "editable": True,
        "metadata": {},
    }, {
        "id": "context.project_memory",
        "nodeId": "context",
        "type": "memory",
        "tokens_est": 14,
        "chars": 14,
        "contextKind": "project_memory",
        "source": "context_tree",
        "reason": "project_memory",
        "lifecycle": "",
        "createdAt": blocks["contextMounts"][0]["createdAt"],
        "updatedAt": blocks["contextMounts"][0]["updatedAt"],
        "metadata": {},
    }, {
        "id": "system.message_overhead",
        "type": "overhead",
        "tokens_est": 12,
        "chars": 0,
        "source": "message_envelope",
        "reason": "message_overhead",
    }]
    assert blocks["layers"][1]["totalTokens"] == 14
    assert blocks["layers"][2]["totalTokens"] == blocks["messageTokens"]
    assert blocks["contextLimit"] == 128_000
    assert blocks["usedPluginPacks"] == ["cyrene_subagent", "cyrene_code"]
    assert "usedStandaloneTools" not in blocks
    assert blocks["messageCount"] == 6
    assert blocks["updatedAt"]
    assert blocks["treeId"] == "chat_1"
    assert blocks["rootId"] == "root"
    assert blocks["leafId"] == "assistant_2"
    assert blocks["selectedModel"] == "selected-model"
    assert blocks["actualModel"] == "actual-model"
    assert blocks["modelIdentity"]["candidateId"] == "actual-candidate"
    assert blocks["chronology"] == "ascending"
    assert [item["id"] for item in blocks["timeline"]] == [
        "root", "user", "context", "assistant_1", "tools", "assistant_2",
    ]
    assert [item["createdAt"] for item in blocks["timeline"]] == sorted(
        item["createdAt"] for item in blocks["timeline"]
    )
    assert blocks["timeline"][2]["contextKind"] == "project_memory"
    assert blocks["timeline"][3]["technical"]["tool_calls"][0] == {
        "id": "call_1",
        "name": "toolbox",
        "arguments": {
            "operation": "invoke",
            "name": "subagent.spawn",
            "arguments": {"agent_id": "reader", "task": "inspect"},
        },
    }
    assert blocks["timeline"][3]["technical"]["effect_results"][0][
        "plugin_pack"
    ] == "cyrene_subagent"
    assert blocks["timeline"][4]["toolAttributions"] == [{
        "pluginPack": "cyrene_subagent",
        "pluginName": "subagent.spawn",
        "operation": "invoke",
    }, {
        "pluginPack": "",
        "pluginName": "CustomLint",
        "operation": "invoke",
    }]
    assert blocks["contextMounts"] == [{
        "id": "context",
        "parentId": "user",
        "kind": "project_memory",
        "source": "context_tree",
        "lifecycle": "",
        "tokensEst": 14,
        "chars": 14,
        "createdAt": blocks["timeline"][2]["createdAt"],
        "updatedAt": blocks["timeline"][2]["updatedAt"],
        "metadata": {},
        "order": 0,
    }]
    assert subagents["activeRoundId"] == "run_1"
    assert subagents["rounds"] == [{
        "id": "run_1",
        "title": "run_1",
        "status": "done",
        "agentCount": 1,
        "activeCount": 0,
    }]
    assert subagents["agents"] == [{
        "id": "reader",
        "name": "reader",
        "task": "inspect the files",
        "status": "done",
        "result": "all clear",
        "error": "",
        "roundId": "run_1",
    }]


@pytest.mark.asyncio
async def test_system_prompt_block_can_be_inspected_and_persistently_updated(tmp_path):
    from cyrene.core.context import ContextStoreRouter

    context_directory = tmp_path / "agent-context"
    router = ContextStoreRouter(context_directory)
    tree = router.create_tree(
        {"role": "system", "content": "You are Cyrene.\nFollow the rules."},
        tree_id="chat_1",
        root_id="root",
    )
    router.mount(
        tree.id,
        tree.root_id,
        {"role": "user", "content": "hello", "run_id": "run_1"},
        node_id="user",
    )
    router.close()
    service = _context_service(
        tmp_path,
        {"id": "chat_1", "model": "model"},
        agent_states=AgentContextRepository(context_directory),
    )

    before = await service.blocks("chat_1")
    prompt_block = before["timeline"][0]

    assert prompt_block["nodeId"] == "root"
    assert prompt_block["content"] == "You are Cyrene.\nFollow the rules."
    assert prompt_block["nodeContent"] == "You are Cyrene.\nFollow the rules."
    assert prompt_block["editable"] is True
    assert prompt_block["technical"]["raw_value"]["role"] == "system"

    result = await service.update_system_prompt(
        "chat_1",
        "root",
        "You are Cyrene.\nUse concise answers.",
        prompt_block["updatedAt"],
    )
    after = await service.blocks("chat_1")

    assert result["ok"] is True
    assert after["timeline"][0]["nodeContent"] == (
        "You are Cyrene.\nUse concise answers."
    )
    with pytest.raises(ConversationContextUpdateError):
        await service.update_system_prompt(
            "chat_1",
            "root",
            "stale update",
            prompt_block["updatedAt"],
        )


@pytest.mark.asyncio
async def test_timeline_exposes_mount_state_and_all_node_content_for_editing(tmp_path):
    from cyrene.core.context import ContextStoreRouter

    context_directory = tmp_path / "agent-context"
    router = ContextStoreRouter(context_directory)
    tree = router.create_tree(
        {"role": "system", "content": "", "metadata": {"root": True}},
        tree_id="chat_1",
        root_id="root",
    )
    user = router.mount(
        tree.id,
        tree.root_id,
        {"role": "user", "content": "hello", "run_id": "run_1"},
        node_id="user",
    )
    prompt = router.mount(
        tree.id,
        user.id,
        {
            "role": "context",
            "content": "real system prompt",
            "context_kind": "system_prompt",
            "context_source": "cyrene_system_prompt",
            "run_id": "run_1",
        },
        node_id="prompt",
    )
    router.mount(
        tree.id,
        prompt.id,
        {
            "role": "tool_results",
            "run_id": "run_1",
            "results": [{
                "call_id": "call_1",
                "name": "lookup",
                "success": True,
                "value": {"result": "before"},
                "error": "",
            }],
        },
        node_id="tools",
    )
    router.close()
    service = _context_service(
        tmp_path,
        {"id": "chat_1", "model": "model"},
        agent_states=AgentContextRepository(context_directory),
    )

    before = await service.blocks("chat_1")
    root, user_block, prompt_block, tools_block = before["timeline"]

    assert root["role"] == "system"
    assert root["chars"] == 0
    assert root["metadata"] == {"root": True}
    assert root["editable"] is True
    assert user_block["content"] == "hello"
    assert user_block["editable"] is True
    assert prompt_block["contextKind"] == "system_prompt"
    assert prompt_block["content"] == "real system prompt"
    assert prompt_block["activeMount"] is True
    assert tools_block["contentFormat"] == "json"
    assert tools_block["editable"] is True

    await service.update_node_content(
        "chat_1",
        "user",
        "",
        user_block["updatedAt"],
    )
    replacement_results = """[{"call_id":"call_1","name":"lookup","success":true,"value":{"result":"after"},"error":""}]"""
    await service.update_node_content(
        "chat_1",
        "tools",
        replacement_results,
        tools_block["updatedAt"],
    )
    after = await service.blocks("chat_1")

    assert after["timeline"][1]["content"] == ""
    assert '"after"' in after["timeline"][3]["content"]
    with pytest.raises(ConversationContextUpdateError):
        await service.update_node_content(
            "chat_1",
            "tools",
            "not json",
            after["timeline"][3]["updatedAt"],
        )


@pytest.mark.asyncio
async def test_context_panel_has_no_old_state_or_transcript_fallback(tmp_path):
    service = _context_service(
        tmp_path,
        {
            "id": "chat_1",
            "messages": [{"role": "user", "content": "public transcript"}],
            "agentContextReport": {"used": 99, "size": 100},
        },
    )

    summary = await service.summary("chat_1")
    blocks = await service.blocks("chat_1")

    assert summary["compositionSource"] == "agent_tree"
    assert summary["ctxUsed"] == 0
    assert summary["segments"] == [
        {"key": "compacted", "tokens": 0},
        {"key": "system", "tokens": 0},
        {"key": "user", "tokens": 0},
        {"key": "assistant", "tokens": 0},
        {"key": "tool", "tokens": 0},
    ]
    assert blocks["compositionSource"] == "agent_tree"
    assert blocks["layers"] == []
    assert blocks["messageTokens"] == 0


def test_plugin_usage_scopes_reused_call_ids_to_their_assistant_batch():
    def toolbox_result(call_id, pack):
        return {
            "call_id": call_id,
            "name": "toolbox",
            "success": True,
            "value": {
                "operation": "invoke",
                "name": pack + ".tool",
                "pack": pack,
            },
        }

    nodes = [
        SimpleNamespace(
            id="assistant_1",
            parent_id="user_1",
            value={"role": "assistant", "effect_results": {
                "call_1": toolbox_result("call_1", "cyrene_code"),
            }},
        ),
        SimpleNamespace(
            id="tools_1",
            parent_id="assistant_1",
            value={"role": "tool_results", "results": [
                toolbox_result("call_1", "cyrene_code"),
            ]},
        ),
        SimpleNamespace(
            id="assistant_2",
            parent_id="user_2",
            value={"role": "assistant", "effect_results": {
                "call_1": toolbox_result("call_1", "cyrene_memory"),
            }},
        ),
        SimpleNamespace(
            id="tools_2",
            parent_id="assistant_2",
            value={"role": "tool_results", "results": [
                toolbox_result("call_1", "cyrene_memory"),
            ]},
        ),
    ]

    assert _agent_path_plugin_usage(nodes) == (
        ["cyrene_code", "cyrene_memory"],
        [],
    )


def test_plugin_usage_includes_successful_direct_tools_via_registry_ownership():
    result = {
        "call_id": "call_1",
        "name": "Bash",
        "success": True,
        "value": {"exit_code": 0},
    }
    nodes = [
        SimpleNamespace(
            id="assistant_1",
            parent_id="user_1",
            value={"role": "assistant", "effect_results": {"call_1": result}},
        ),
        SimpleNamespace(
            id="tools_1",
            parent_id="assistant_1",
            value={"role": "tool_results", "results": [result]},
        ),
    ]

    assert _agent_path_plugin_usage(
        nodes,
        lambda name: ("core", name),
    ) == (["core"], [])


@pytest.mark.asyncio
async def test_active_inbox_uses_only_the_durable_agent_round():
    chats = _Chats(None)
    run = SimpleNamespace(
        run_id="run_1",
        status="running",
    )

    async def agent_messages(chat_id, round_id, limit):
        assert chat_id == "chat_1"
        assert round_id == "run_1"
        assert limit == 100
        return {
            "roundId": "run_1",
            "messages": [{
                "message_id": "msg_1",
                "from": "researcher",
                "to": "main",
                "type": "message",
                "content": "ready",
                "round_id": "run_1",
                "timestamp": "2026-01-01",
                "read": False,
            }],
        }

    service = ConversationInboxQueryService(
        chats=chats,
        run_manager=SimpleNamespace(get=lambda _chat_id: run),
        utc_now=lambda: "2026-01-02",
        agent_messages=agent_messages,
    )

    result = await service.snapshot("chat_1")

    assert chats.read_calls == 0
    assert result["active"] is True
    assert result["counts"] == {
        "ready": 1,
        "consumed": 0,
        "total": 1,
    }
    assert result["events"][0]["eventId"] == "agent-inbox:msg_1"
    assert "tools" not in result
    assert "live" not in result
    assert result["queueDepth"] == 1


@pytest.mark.asyncio
async def test_agent_inbox_filters_the_active_round_and_reports_bounded_scope():
    async def agent_messages(_chat_id, round_id, limit):
        assert round_id == "run_live"
        assert limit == 100
        return {
            "messages": [
                {
                    "message_id": "msg_000",
                    "from": "old_worker",
                    "to": "main",
                    "type": "message",
                    "content": "old round",
                    "round_id": "run_old",
                    "timestamp": "2025-12-31T23:00:00+00:00",
                    "read": False,
                },
                {
                    "message_id": "msg_001",
                    "from": "researcher",
                    "to": "main",
                    "type": "task_result",
                    "content": "finding",
                    "summary": "finding",
                    "round_id": "run_live",
                    "timestamp": "2026-01-01T01:00:00+00:00",
                    "read": True,
                },
                {
                    "message_id": "msg_002",
                    "from": "reviewer",
                    "to": "main",
                    "type": "message",
                    "content": "please review",
                    "round_id": "run_live",
                    "timestamp": "2026-01-01T02:00:00+00:00",
                    "read": False,
                },
            ],
            "roundId": "run_live",
            "eventsTruncated": False,
            "historyWindowTruncated": True,
        }

    run = SimpleNamespace(
        run_id="run_live",
        status="running",
    )
    service = ConversationInboxQueryService(
        chats=_Chats({"id": "chat_1"}),
        run_manager=SimpleNamespace(get=lambda _chat_id: run),
        utc_now=lambda: "2026-01-02",
        agent_messages=agent_messages,
    )

    result = await service.snapshot("chat_1")

    assert [event["eventId"] for event in result["events"]] == [
        "agent-inbox:msg_001",
        "agent-inbox:msg_002",
    ]
    assert [event["status"] for event in result["events"]] == [
        "consumed",
        "ready",
    ]
    assert result["events"][0]["messageType"] == "task_result"
    assert result["events"][0]["roundId"] == "run_live"
    assert result["events"][0]["preview"] == "researcher: finding"
    assert result["events"][0]["fromAgent"] == "researcher"
    assert result["events"][0]["toAgent"] == "main"
    assert result["counts"]["ready"] == 1
    assert result["counts"]["consumed"] == 1
    assert result["counts"]["total"] == 2
    assert result["queueDepth"] == 1
    assert result["runId"] == "run_live"
    assert result["agentRoundId"] == "run_live"
    assert result["countsScope"] == "visible_events"
    assert result["eventLimit"] == 100
    assert result["eventsTruncated"] is False
    assert result["historyWindowTruncated"] is True
    assert result["updatedAt"] == "2026-01-01T02:00:00+00:00"
    assert result["observedAt"] == "2026-01-02"


@pytest.mark.asyncio
async def test_idle_inbox_reports_the_latest_agent_round_as_its_scope():
    async def agent_messages(_chat_id, round_id, limit):
        assert round_id == ""
        assert limit == 100
        return {
            "messages": [{
                "message_id": "msg_010",
                "from": "reader",
                "to": "main",
                "type": "task_result",
                "content": "done",
                "round_id": "run_latest",
                "timestamp": "2026-01-03T00:00:00+00:00",
                "read": True,
            }],
            "roundId": "run_latest",
            "eventsTruncated": False,
            "historyWindowTruncated": False,
        }

    service = ConversationInboxQueryService(
        chats=_Chats({"id": "chat_1"}),
        run_manager=SimpleNamespace(get=lambda _chat_id: None),
        utc_now=lambda: "2026-01-03T01:00:00+00:00",
        agent_messages=agent_messages,
    )

    result = await service.snapshot("chat_1")

    assert result["active"] is False
    assert result["runStatus"] == "idle"
    assert result["runId"] == "run_latest"
    assert result["agentRoundId"] == "run_latest"
    assert [item["roundId"] for item in result["events"]] == ["run_latest"]


@pytest.mark.asyncio
async def test_context_compaction_node_replaces_projected_model_history(tmp_path):
    from cyrene.core.context import ContextStoreRouter

    context_directory = tmp_path / "agent-context"
    router = ContextStoreRouter(context_directory)
    tree = router.create_tree(
        {"role": "system", "content": "root system"},
        tree_id="chat_compacted",
        root_id="root",
    )
    user = router.mount(
        tree.id,
        tree.root_id,
        {"role": "user", "content": "large old request", "run_id": "run_1"},
        node_id="user",
    )
    router.mount(
        tree.id,
        user.id,
        {
            "role": "context_compaction",
            "messages": [
                {"role": "system", "content": "root system"},
                {
                    "role": "system",
                    "content": "[Compacted earlier context]\nsummary",
                    "compacted_block": True,
                    "llm_compacted": True,
                },
            ],
            "run_id": "run_1",
            "trigger_model": False,
            "before_tokens": 700,
            "after_tokens": 80,
            "context_limit": 1_000,
            "distilled": True,
        },
        node_id="compaction",
    )
    router.close()

    repository = AgentContextRepository(context_directory)
    state = repository.read("chat_compacted")
    service = _context_service(
        tmp_path,
        {"id": "chat_compacted", "model": "model"},
        agent_states=repository,
        context_limit=lambda _model: 1_000,
    )
    summary = await service.summary("chat_compacted")

    assert [message["content"] for message in state["messages"]] == [
        "root system",
        "[Compacted earlier context]\nsummary",
    ]
    assert state["compaction"] == {
        "active": True,
        "blocks": 1,
        "beforeTokens": 700,
        "afterTokens": 80,
        "contextLimit": 1_000,
        "distilled": True,
        "updatedAt": state["compaction"]["updatedAt"],
    }
    assert summary["compaction"]["active"] is True
    assert summary["compaction"]["blocks"] == 1
    assert summary["compaction"]["distilled"] is True
    assert summary["segments"][0]["key"] == "compacted"


@pytest.mark.asyncio
async def test_manual_context_compaction_delegates_to_agent_runtime(tmp_path):
    calls = []

    class _State:
        def read(self, _chat_id):
            return {
                "messages": [{"role": "user", "content": "before"}],
                "usage": {},
            }

    async def compact_agent(chat_id, context_limit):
        calls.append((chat_id, context_limit))
        return {
            "compacted": True,
            "reason": "compacted",
            "before_tokens": 64,
            "after_tokens": 12,
            "context_limit": context_limit,
            "distilled": False,
        }

    service = ConversationContextQueryService(
        chats=_Chats({"id": "chat_1", "model": "model"}),
        agent_states=_State(),
        default_model=lambda: "model",
        context_limit=lambda _model: 1_000,
        approx_token_count=lambda text: len(text),
        compact_agent=compact_agent,
    )

    result = await service.compact("chat_1")

    assert calls == [("chat_1", 1_000)]
    assert result == {
        "ok": True,
        "compacted": True,
        "reason": "compacted",
        "beforeTokens": 64,
        "afterTokens": 12,
        "ctxLimit": 1_000,
        "triggerRatio": 0.6,
        "distilled": False,
    }

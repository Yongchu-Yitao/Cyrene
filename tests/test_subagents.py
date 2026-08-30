"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

from cyrene.core import AgentSession
from cyrene.core.plugin import (
    Plugin,
    PluginActivationState,
    PluginContext,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
    without_plugin_session_state,
)
from cyrene.workbench.core_adapter import WorkbenchSessionBridge
from cyrene.platform import inbox


CANONICAL_PLUGIN_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "cyrene" / "plugins" / "builtin"
)


def run(coroutine):
    return asyncio.run(coroutine)


def copy_subagent_pack(plugin_directory: Path) -> None:
    plugin_directory.mkdir(parents=True)
    shutil.copytree(
        CANONICAL_PLUGIN_DIRECTORY / "cyrene_subagent",
        plugin_directory / "cyrene_subagent",
    )


def model_registry(handler) -> PluginRegistry:
    registry = PluginRegistry(activation=PluginActivationState())
    registry.register_pack(
        PluginPack(
            "model",
            "test model",
            (
                Plugin(
                    "MiniMax",
                    "fake model",
                    {"type": "object"},
                    handler,
                    kind="model",
                ),
            ),
        ),
        source="test",
    )
    return registry


def tool_call(call_id: str, arguments: dict) -> dict:
    return {
        "content": "",
        "reasoning": "",
        "tool_calls": [
            {
                "id": call_id,
                "name": "toolbox",
                "arguments": arguments,
            }
        ],
        "model": "fake",
    }


def answer(content: str) -> dict:
    return {
        "content": content,
        "reasoning": "",
        "tool_calls": [],
        "model": "fake",
    }


def allow() -> dict:
    return {
        "content": "",
        "reasoning": "",
        "tool_calls": [
            {
                "id": "allow",
                "name": "decide",
                "arguments": {"approve": True, "rationale": "allowed in test"},
            }
        ],
        "model": "fake",
    }


class StubSubagentManager:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def spawn(
        self,
        requester_id: str,
        agent_id: str,
        task: str,
        *,
        effect_key: str = "",
        **options,
    ) -> dict:
        self.calls.append(("spawn", requester_id, agent_id, task, effect_key, options))
        return {"agent_id": agent_id, "task": task, "status": "running"}

    async def send(
        self,
        sender: str,
        target: str,
        content: str,
        *,
        effect_key: str = "",
    ) -> dict:
        self.calls.append(("send", sender, target, content, effect_key))
        return {"from": sender, "to": target, "message_id": "msg_001"}

    async def broadcast(
        self,
        sender: str,
        content: str,
        *,
        effect_key: str = "",
    ) -> dict:
        self.calls.append(("broadcast", sender, content, effect_key))
        return {"from": sender, "delivered": ["one", "two"], "errors": {}}

    def query(self, round_id: str = "") -> dict:
        self.calls.append(("query", round_id))
        return {"round_id": round_id, "subagents": []}


def test_subagent_pack_follows_toolbox_list_describe_invoke(tmp_path, monkeypatch):
    async def scenario() -> None:
        from cyrene.platform import settings_store

        configured = {"spawn_policy": "conservative"}
        monkeypatch.setattr(
            settings_store,
            "get",
            lambda key, default=None: configured.get(key, default),
        )
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)
        registry = PluginRegistry(activation=PluginActivationState())
        assert registry.load_directory(plugin_directory) == ()
        runtime = PluginRuntime(registry)
        manager = StubSubagentManager()
        context = PluginContext(
            workspace=tmp_path,
            data={"agent_id": "main"},
            services={"subagents": manager},
        )

        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert "cyrene_subagent" in listing.value["packs"]

        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_subagent"},
            context,
        )
        assert {tool["name"] for tool in described.value["plugins"]} == {
            "spawn_subagent",
            "send_agent_message",
            "broadcast_agent_message",
            "query_round",
        }

        descriptions = {
            item["name"]: item for item in described.value["plugins"]
        }
        spawn_schema = descriptions["spawn_subagent"]["input_schema"]
        assert spawn_schema["required"] == ["agent_id", "task"]
        assert spawn_schema["additionalProperties"] is False
        assert spawn_schema["properties"]["mode"]["enum"] == [
            "execution", "discussion"
        ]
        assert spawn_schema["properties"]["role"]["enum"] == [
            "moderator", "participant"
        ]
        assert spawn_schema["properties"]["success_criteria"]["maxItems"] == 20
        assert spawn_schema["properties"]["max_messages"]["maximum"] == 50

        spawned = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "spawn_subagent",
                "arguments": {"agent_id": "worker", "task": "inspect this"},
            },
            context,
        )
        sent = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "send_agent_message",
                "arguments": {"to": "worker", "content": "hello"},
            },
            context,
        )
        broadcast = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "broadcast_agent_message",
                "arguments": {"content": "heads up"},
            },
            context,
        )

        assert spawned.value["result"].startswith("Sub-agent 'worker' started.")
        assert sent.value["result"] == "Message sent to worker."
        assert broadcast.value["result"] == "Broadcast sent to 2/2 peers."
        assert manager.calls[0][:4] == (
            "spawn",
            "main",
            "worker",
            "inspect this",
        )
        assert manager.calls[0][4]

        configured["spawn_policy"] = "off"
        blocked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "spawn_subagent",
                "arguments": {
                    "agent_id": "blocked-worker",
                    "task": "must not start",
                },
            },
            context,
        )
        assert "disabled by the current spawn policy" in blocked.value["result"]
        assert not any(
            call[0] == "spawn" and call[2] == "blocked-worker"
            for call in manager.calls
        )

    run(scenario())


def test_subagent_pack_mounts_dynamic_spawn_policy_for_main_only(
    tmp_path,
    monkeypatch,
):
    async def scenario() -> None:
        from cyrene.platform import settings_store

        configured = {"spawn_policy": "aggressive"}
        monkeypatch.setattr(
            settings_store,
            "get",
            lambda key, default=None: configured.get(key, default),
        )
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)
        registry = model_registry(lambda _arguments, _context: answer("unused"))
        assert registry.load_directory(plugin_directory) == ()

        main = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="spawn-policy-main",
            registry=registry,
            load_plugins=False,
        )
        child = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="spawn-policy-child",
            registry=registry,
            agent_id="worker",
            parent_agent_id="main",
            load_plugins=False,
        )
        try:
            aggressive = await main.build_model_context()
            assert "## Subagent Spawn Policy" in aggressive
            assert "Current policy: aggressive." in aggressive
            assert "spawn_subagent" in aggressive

            configured["spawn_policy"] = "off"
            disabled = await main.build_model_context()
            assert "Current policy: off." in disabled
            assert "Do not invoke `spawn_subagent`." in disabled

            assert await child.build_model_context() == ""
        finally:
            child.close()
            main.close()

    run(scenario())


def test_disabled_subagent_pack_does_not_attach_session_driver(tmp_path):
    plugin_directory = tmp_path / "plugin_impl"
    copy_subagent_pack(plugin_directory)
    registry = model_registry(lambda _arguments, _context: answer("unused"))
    assert registry.load_directory(plugin_directory) == ()
    registry.configure_activation(
        plugins={},
        packs={"cyrene_subagent": False},
    )

    session = AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        tree_id="subagent-disabled",
        registry=registry,
        load_plugins=False,
    )
    try:
        assert session.session_driver is None
        assert "subagents" not in session.plugin_services
        assert "session_driver" not in session.plugin_services
        assert run(session.build_session_context()) == ""
    finally:
        session.close()


def test_quit_is_direct_and_subagent_scoped(tmp_path):
    plugin_directory = tmp_path / "plugin_impl"
    copy_subagent_pack(plugin_directory)
    registry = model_registry(lambda _arguments, _context: answer("unused"))
    assert registry.load_directory(plugin_directory) == ()

    assert "quit" not in {
        item["function"]["name"]
        for item in registry.direct_tool_definitions(agent_id="main")
    }
    child_tools = {
        item["function"]["name"]: item
        for item in registry.direct_tool_definitions(agent_id="worker")
    }
    assert "quit" in child_tools
    quit_schema = child_tools["quit"]["function"]["parameters"]
    assert quit_schema["required"] == ["completion_status"]
    assert quit_schema["properties"]["completion_status"]["enum"] == [
        "completed", "partial", "blocked"
    ]


def test_plugin_native_modes_budgets_completion_and_inbox(tmp_path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)

        async def model(_arguments, context):
            if context.data.get("model_call_kind") == "permission":
                return allow()
            return answer("idle result")

        session = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="mode-chat",
            registry=model_registry(model),
            plugin_context_data={"session_id": "mode-chat"},
        )
        manager = session.plugin_services["subagents"]
        try:
            session.submit("seed", run_id="mode-round")
            await session.drain()
            await manager.spawn(
                "main",
                "moderator",
                "lead",
                mode="discussion",
                role="moderator",
                discussion_id="design",
                success_criteria=["publish synthesis"],
            )
            await manager.spawn(
                "main",
                "participant",
                "review",
                role="participant",
                discussion_id="design",
            )
            await manager.spawn("main", "executor", "build", mode="execution")

            first = await manager.send(
                "moderator",
                "participant",
                "Concrete finding",
                effect_key="discussion-message-1",
            )
            retried = await manager.send(
                "moderator",
                "participant",
                "Concrete finding",
                effect_key="discussion-message-1",
            )
            assert first["message_id"] == retried["message_id"]
            snapshot = manager.query("mode-round")
            discussion = snapshot["discussions"][0]
            assert discussion["messages_total"] == 1
            assert discussion["rounds"] == 1
            assert len(discussion["transcript"]) == 1
            assert inbox.get_unread_count(
                "participant", session_id="mode-chat"
            ) == 1

            try:
                await manager.send("executor", "participant", "not allowed")
            except ValueError as exc:
                assert "requires discussion mode" in str(exc)
            else:
                raise AssertionError("execution worker communication was allowed")

            missing = manager.request_finish(
                "moderator", "completed", []
            )
            assert missing["accepted"] is False
            assert missing["missing_criteria"] == ["publish synthesis"]
            completed = manager.request_finish(
                "moderator",
                "completed",
                [{
                    "criterion": "publish synthesis",
                    "evidence": "draft is in the final response",
                }],
            )
            assert completed["accepted"] is True

            records = {
                item["agent_id"]: item for item in manager.query()["subagents"]
            }
            assert records["moderator"]["mode"] == "discussion"
            assert records["participant"]["role"] == "participant"
            assert records["executor"]["mode"] == "execution"
            root = session.store.get_node(session.tree.id, session.tree.root_id)
            persisted = root.value["_plugin_session_state"]["cyrene_subagent"]
            assert persisted["discussions"]["design"]["messages_total"] == 1
        finally:
            session.close()

    run(scenario())


def test_agent_session_subagent_tree_inbox_and_workbench_output(tmp_path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)
        permission_requests: list[dict] = []
        callers: list[tuple[str, str]] = []

        async def model(arguments, context):
            if context.data.get("model_call_kind") == "permission":
                permission_requests.append(
                    json.loads(arguments["messages"][-1]["content"])
                )
                return allow()

            agent_id = str(context.data.get("agent_id") or "main")
            callers.append(
                (agent_id, str(context.data.get("caller") or "main_agent"))
            )
            last = arguments["messages"][-1]
            if last["role"] == "user":
                content = str(last["content"])
                if agent_id == "main" and content.split("\n\n", 1)[0] == "delegate":
                    return tool_call("main-list", {"operation": "list"})
                if (
                    agent_id == "researcher"
                    and content.split("\n\n", 1)[0] == "research this"
                ):
                    return tool_call("child-list", {"operation": "list"})
                if agent_id == "main" and "(message)" in content:
                    return answer("progress received")
                if agent_id == "main" and "(result)" in content:
                    return answer("integrated child result")
                raise AssertionError((agent_id, content))

            payload = json.loads(last["content"])
            value = payload["value"]
            operation = value["operation"]
            if operation == "list":
                target = "spawn_subagent" if agent_id == "main" else "send_agent_message"
                return tool_call(
                    f"{agent_id}-describe",
                    {"operation": "describe", "name": target},
                )
            if operation == "describe" and agent_id == "main":
                return tool_call(
                    "main-spawn",
                    {
                        "operation": "invoke",
                        "name": "spawn_subagent",
                        "arguments": {
                            "agent_id": "researcher",
                            "task": "research this",
                        },
                    },
                )
            if operation == "describe" and agent_id == "researcher":
                return answer("child result")
            if operation == "invoke" and agent_id == "main":
                return answer("waiting for child")
            raise AssertionError((agent_id, value))

        session = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="chat-1",
            registry=model_registry(model),
            plugin_context_data={"session_id": "chat-1"},
        )
        bridge = WorkbenchSessionBridge(session)
        published: list[dict] = []

        async def publish(event: dict) -> None:
            published.append(event)

        try:
            result = await bridge.submit_result(
                "delegate",
                run_id="round-1",
                publish=publish,
            )
            assert result.text == "integrated child result"
            assert [event["response"] for event in published if event["type"] == "reply_done"] == [
                "integrated child result"
            ]

            state = session.snapshot()
            record = state["subagents"]["subagents"][0]
            assert record["agent_id"] == "researcher"
            assert record["status"] == "done"
            child_tree = session.store.get_tree(record["tree_id"])
            child_root = session.store.get_node(child_tree.id, child_tree.root_id)
            assert (
                without_plugin_session_state(child_root.value)
                == session.initial_root_value
            )
            child_root_children = session.store.get_children(
                child_tree.id,
                child_tree.root_id,
            )
            assert len(child_root_children) == 1
            instruction = child_root_children[0].value
            assert instruction["content"] == "research this"
            assert instruction["authorization_request"] == "delegate"
            assert instruction["metadata"]["source"] == "main_agent_instruction"

            main_inbox_nodes = [
                node
                for node in session.snapshot()["nodes"]
                if node["value"].get("role") == "user"
                and node["value"].get("metadata", {}).get("source") == "agent_inbox"
            ]
            assert len(main_inbox_nodes) == 1
            assert {
                node["value"]["metadata"]["message_type"]
                for node in main_inbox_nodes
            } == {"result"}
            assert all(
                node["value"]["authorization_request"] == "delegate"
                for node in main_inbox_nodes
            )
            assert inbox.get_unread_count("main", session_id="chat-1") == 0
            await session.drain()
            assert len(
                [
                    node
                    for node in session.snapshot()["nodes"]
                    if node["value"].get("metadata", {}).get("source")
                    == "agent_inbox"
                ]
            ) == 1
            assert ("main", "main_agent") in callers
            assert ("researcher", "subagent_researcher") in callers
            assert all(
                item["user_request"] == "delegate"
                for item in permission_requests
            )
        finally:
            session.close()

    run(scenario())


def test_subagent_tree_recovers_without_repeating_instruction(tmp_path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)
        complete_child = False
        child_started = threading.Event()

        async def model(arguments, context):
            nonlocal complete_child
            if context.data.get("model_call_kind") == "permission":
                return allow()
            agent_id = str(context.data.get("agent_id") or "main")
            last = arguments["messages"][-1]
            if agent_id == "main":
                if last["role"] == "user" and last["content"] == "seed":
                    return answer("seeded")
                return answer("integrated recovered result")
            child_started.set()
            while not complete_child:
                await asyncio.sleep(60)
            return answer("recovered result")

        first = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="recover-chat",
            registry=model_registry(model),
            plugin_context_data={"session_id": "recover-chat"},
        )
        first.submit("seed", run_id="recover-run")
        await first.drain()
        await first.plugin_services["subagents"].spawn(
            "main", "worker", "resume this"
        )
        assert await asyncio.to_thread(child_started.wait, 2)
        child_tree_id = "recover-chat.subagent.worker"
        first.close()

        complete_child = True
        reopened = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="recover-chat",
            registry=model_registry(model),
            plugin_context_data={"session_id": "recover-chat"},
        )
        try:
            await reopened.drain()
            record = reopened.snapshot()["subagents"]["subagents"][0]
            assert record["status"] == "done"
            child = reopened.store.get_tree(child_tree_id)
            task_nodes = [
                node
                for node in reopened.store.get_children(child.id, child.root_id)
                if node.value.get("metadata", {}).get("source")
                == "main_agent_instruction"
            ]
            assert len(task_nodes) == 1
            assert task_nodes[0].value["content"] == "resume this"
            result_messages = [
                message
                for message in await inbox.read_messages(
                    "main",
                    mark_read=False,
                    session_id="recover-chat",
                )
                if message.get("type") == "result"
                and message.get("from") == "worker"
            ]
            assert len(result_messages) == 1
            assert reopened.final_output("recover-run")["content"] == (
                "integrated recovered result"
            )
        finally:
            reopened.close()

    run(scenario())


def test_parent_cancel_cascades_while_main_is_idle(tmp_path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        plugin_directory = tmp_path / "plugin_impl"
        copy_subagent_pack(plugin_directory)
        child_started = threading.Event()

        async def model(_arguments, context):
            if context.data.get("model_call_kind") == "permission":
                return allow()
            if str(context.data.get("agent_id") or "main") == "main":
                return answer("seeded")
            child_started.set()
            await asyncio.sleep(60)
            return answer("too late")

        session = AgentSession(
            tmp_path / "data",
            tmp_path / "workspace",
            plugin_directory,
            tree_id="cancel-chat",
            registry=model_registry(model),
            plugin_context_data={"session_id": "cancel-chat"},
        )
        try:
            session.submit("seed", run_id="cancel-run")
            await session.drain()
            await session.plugin_services["subagents"].spawn(
                "main", "worker", "wait"
            )
            assert await asyncio.to_thread(child_started.wait, 2)
            assert session.is_idle is True

            assert await session.cancel("user_stop", timeout=2) is True
            record = session.snapshot()["subagents"]["subagents"][0]
            assert record["status"] == "cancelled"
            child = session.store.get_tree(record["tree_id"])
            cancelled = [
                node.value
                for node in session.store.get_subtree(child.id, child.root_id)
                if node.value.get("cancelled") is True
            ]
            assert len(cancelled) == 1
            assert cancelled[0]["cancel_reason"] == "user_stop"
            assert session.final_output("cancel-run")["cancelled"] is True
        finally:
            session.close()

    run(scenario())


def test_inbox_dedup_survives_unread_counter_failure(tmp_path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(inbox, "INBOX_DIR", tmp_path / "inbox")
        original_write_unread = inbox._write_unread
        writes = 0

        def fail_second_counter_write(agent_name, count, session_id=""):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("simulated counter write failure")
            return original_write_unread(agent_name, count, session_id)

        monkeypatch.setattr(inbox, "_write_unread", fail_second_counter_write)
        first = await inbox.send_message(
            "worker",
            "main",
            "result",
            "durable result",
            session_id="chat",
            dedup_key="result:worker:one",
        )
        assert first
        assert inbox.get_unread_count("main", session_id="chat") == 1

        retried = await inbox.send_message(
            "worker",
            "main",
            "result",
            "durable result",
            session_id="chat",
            dedup_key="result:worker:one",
        )
        assert retried == first
        assert inbox.get_unread_count("main", session_id="chat") == 1
        await inbox.mark_all_read("main", session_id="chat")
        assert inbox.get_unread_count("main", session_id="chat") == 0

        consumed_retry = await inbox.send_message(
            "worker",
            "main",
            "result",
            "durable result",
            session_id="chat",
            dedup_key="result:worker:one",
        )
        assert consumed_retry == first
        assert inbox.get_unread_count("main", session_id="chat") == 0

    run(scenario())

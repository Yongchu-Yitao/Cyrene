from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agent.plugin import PluginContext
from agent.workbench import chat_runtime


def run(coroutine):
    return asyncio.run(coroutine)


def test_workbench_model_uses_selected_session_route_and_normalizes_tools(monkeypatch):
    from cyrene.model_runtime import client as model_client

    captured = {}

    async def fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return {
            "role": "assistant",
            "content": "",
            "reasoning_content": "inspect first",
            "reasoning_details": [{"type": "reasoning.text", "text": "inspect first"}],
            "tool_calls": [
                {
                    "id": "call-read",
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "arguments": json.dumps({"path": "README.md"}),
                    },
                }
            ],
            "usage": {"prompt_tokens": 12},
            "model": "selected-model",
        }

    monkeypatch.setattr(model_client, "call_llm", fake_call_llm)
    stored_messages = [
        {"role": "system", "content": "base system"},
        {"role": "user", "content": "inspect"},
    ]
    result = run(
        chat_runtime.workbench_chat_model(
            {
                "messages": stored_messages,
                "tools": [],
            },
            PluginContext(
                tree_id="chat-selected",
                data={
                    "session_id": "chat-selected",
                    "run_id": "run-selected",
                    "model_call_kind": "agent",
                    "system_extra": "turn-only context",
                },
            ),
        )
    )

    assert captured["candidates"] is None
    assert captured["session_id"] == "chat-selected"
    assert captured["round_id"] == "run-selected"
    assert captured["caller"] == "main_agent"
    assert captured["phase"] == "agent"
    assert captured["messages"][0]["content"] == (
        "base system\n\nturn-only context"
    )
    assert stored_messages[0]["content"] == "base system"
    assert result["tool_calls"] == [
        {
            "id": "call-read",
            "name": "Read",
            "arguments": {"path": "README.md"},
        }
    ]
    assert result["reasoning"] == "inspect first"
    assert result["model"] == "selected-model"


def test_workbench_chat_kernel_is_default_with_explicit_legacy_rollback(monkeypatch):
    monkeypatch.delenv(chat_runtime.WORKBENCH_CHAT_KERNEL_ENV, raising=False)
    assert chat_runtime.workbench_chat_kernel_enabled() is True

    monkeypatch.setenv(chat_runtime.WORKBENCH_CHAT_KERNEL_ENV, "legacy")
    assert chat_runtime.workbench_chat_kernel_enabled() is False

    monkeypatch.setenv(chat_runtime.WORKBENCH_CHAT_KERNEL_ENV, "new")
    assert chat_runtime.workbench_chat_kernel_enabled() is True


def test_workbench_system_extra_is_not_added_to_permission_prompt():
    messages = [{"role": "system", "content": "permission system"}]

    projected = chat_runtime._model_messages(
        messages,
        phase="permission",
        system_extra="turn-only project context",
    )

    assert projected == messages
    assert projected is not messages


def test_production_runtime_seeds_forwards_context_and_leaves_final_reply_to_lifecycle(
    tmp_path,
    monkeypatch,
):
    from agent.plugin import native_tools

    seeded = []
    opened = {}
    published = []

    def fake_seed(directory):
        seeded.append(directory)

    class FakeBridge:
        def snapshot(self):
            return {"status": "idle", "run_id": ""}

        async def submit(self, text, *, run_id, metadata, publish):
            opened["text"] = text
            opened["run_id"] = run_id
            opened["metadata"] = metadata
            await publish({"type": "tool.started", "payload": {"name": "Read"}})
            await publish({"type": "reply_start"})
            await publish({"type": "reply_delta", "delta": "done"})
            await publish({"type": "reply_done", "response": "done"})

            writer = opened["plugin_context_data"]["run_context"][
                "runtime_event_writer"
            ]

            def worker_publish():
                asyncio.run(writer({"type": "legacy-runtime-event"}))

            await asyncio.to_thread(worker_publish)
            return "done"

        async def resume(self, *, publish):
            raise AssertionError("a new idle session must submit")

        async def cancel(self, _reason):
            raise AssertionError("an idle session must not cancel")

        def close(self):
            opened["closed"] = True

    def fake_open(*args, **kwargs):
        opened["args"] = args
        opened.update(kwargs)
        return FakeBridge()

    class Run:
        run_id = "run-production"

        async def publish(self, event):
            published.append(event)

    monkeypatch.setattr(native_tools, "seed_builtin_plugin_directory", fake_seed)
    monkeypatch.setattr(chat_runtime.WorkbenchSessionBridge, "open", fake_open)

    result = run(
        chat_runtime.run_workbench_chat(
            run=Run(),
            user_message="inspect it",
            bot=object(),
            legacy_chat_id="legacy-chat",
            db_path=str(tmp_path / "workbench.sqlite3"),
            session_id="chat-production",
            workspace_dir=str(tmp_path / "workspace"),
            client_request_id="request-1",
            permission_mode="default",
            command="",
            public_user_message="inspect it",
            attachment_paths={"report.txt": "/tmp/report.txt"},
            system_extra="project context",
            response_capabilities=("interactive_blocks",),
            plugin_directory=tmp_path / "plugins",
            data_directory=tmp_path / "agent-data",
        )
    )

    assert result == "done"
    assert seeded == [(tmp_path / "plugins").resolve()]
    assert opened["model_plugin"] == chat_runtime.WORKBENCH_CHAT_MODEL_PLUGIN
    assert opened["chat_id"] == "chat-production"
    assert opened["host_context"]["chat_id"] == "legacy-chat"
    assert opened["host_context"]["notify_state"] is None
    assert opened["plugin_context_data"]["system_extra"] == "project context"
    assert opened["text"] == "inspect it"
    context = opened["plugin_context_data"]["run_context"]
    assert context["session_id"] == "chat-production"
    assert context["round_id"] == "run-production"
    assert context["client_request_id"] == "request-1"
    assert context["attachment_paths"] == {"report.txt": "/tmp/report.txt"}
    assert context["response_capabilities"] == frozenset({"interactive_blocks"})
    assert [event["type"] for event in published] == [
        "tool.started",
        "legacy-runtime-event",
    ]
    assert opened["closed"] is True


def test_builtin_workbench_route_uses_new_runtime_without_touching_external_path(
    tmp_path,
    monkeypatch,
):
    from cyrene.runtime import host_bridge
    from cyrene.workbench import composer_context, project_memory_prompt
    from route.workbench.chat_routes.run_send_routes import _SendOperation

    captured = {}

    async def fake_runtime(**kwargs):
        captured.update(kwargs)
        return "new-kernel-reply"

    async def fake_source(_ui_instance_id):
        return "desktop_local"

    monkeypatch.delenv(chat_runtime.WORKBENCH_CHAT_KERNEL_ENV, raising=False)
    monkeypatch.setattr(chat_runtime, "run_workbench_chat", fake_runtime)
    monkeypatch.setattr(host_bridge, "resolve_conversation_source", fake_source)
    monkeypatch.setattr(project_memory_prompt, "build_main_agent_suffix", lambda *_args, **_kwargs: "memory")
    monkeypatch.setattr(composer_context, "build_context_activation_prompt", lambda _value: "context")

    operation = object.__new__(_SendOperation)
    operation.chat_id = "chat-route"
    operation.client_request_id = "request-route"
    operation.is_external_agent = False
    operation.is_side_agent = False
    operation.agent_message = "hello"
    operation.public_message = "hello"
    operation.public_attachments = []
    operation.normalized = []
    operation.command = ""
    operation.mode = "default"
    operation.workspace_dir = str(tmp_path / "workspace")
    operation.ui_instance_id = "ui-route"
    operation.context_activations = {}
    operation.dynamic_command_prompt = ""
    operation.chat = {"projectMemorySnapshot": {}}
    operation.context = SimpleNamespace(bot=object(), db_path=str(tmp_path / "db.sqlite3"))
    operation.routes = SimpleNamespace(chat_id="legacy-route")
    operation.service = SimpleNamespace(
        chat_soul_active=lambda _chat: True,
        chat_workspace_active=lambda _chat: False,
    )

    async def legacy_run(**_kwargs):
        raise AssertionError("built-in Chat must default to the new kernel")

    operation.run_agent = legacy_run
    workbench_run = SimpleNamespace(run_id="run-route", publish=lambda _event: None)

    result = run(operation._run_turn(workbench_run))

    assert result == "new-kernel-reply"
    assert captured["run"] is workbench_run
    assert captured["session_id"] == "chat-route"
    assert captured["legacy_chat_id"] == "legacy-route"
    assert captured["conversation_source"] == "desktop_local"
    assert captured["system_extra"] == "memory\n\ncontext"

"""Regression coverage for plugin-native mid-run guidance."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry
from cyrene.core.session import AgentSession
from cyrene.plugins.builtin.cyrene_guidance import plugin_pack as guidance_pack
from cyrene.workbench.application.inbox import (
    GuidanceAdmissionClosed,
    WorkbenchAgentInbox,
    WorkbenchGuidanceChannel,
)


def _event(event_id: str, text: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": "guidance",
        "payload": {
            "text": text,
            "agent_originated": False,
            "origin_session_id": "",
        },
    }


def _registry(model, *tools: Plugin) -> PluginRegistry:
    registry = PluginRegistry()
    registry.register_pack(
        PluginPack(
            "test_model",
            "test model and tools",
            (
                Plugin(
                    "MiniMax",
                    "test model",
                    {"type": "object"},
                    model,
                    kind="model",
                ),
                *tools,
            ),
        ),
        source="test",
    )
    registry.register_pack(guidance_pack, source="test")
    return registry


def _session(tmp_path, registry: PluginRegistry, channel: Any) -> AgentSession:
    plugin_directory = tmp_path / "plugins"
    plugin_directory.mkdir(exist_ok=True)
    return AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        plugin_directory,
        registry=registry,
        load_plugins=False,
        plugin_services={"guidance_channel": channel},
    )


class _ScriptedChannel:
    def __init__(self, terminal_events: list[dict[str, Any]] | None = None) -> None:
        self.pending: list[dict[str, Any]] = []
        self.terminal_events = list(terminal_events or [])
        self.acknowledged: list[str] = []

    @property
    def has_pending(self) -> bool:
        return bool(self.pending)

    async def wait(self) -> bool:
        await asyncio.Future()
        return False

    async def collect(self) -> list[dict[str, Any]]:
        events, self.pending = self.pending, []
        return events

    async def collect_or_seal(self) -> list[dict[str, Any]]:
        events, self.terminal_events = self.terminal_events, []
        return events

    def requeue(self, events: list[dict[str, Any]]) -> None:
        self.pending = [*events, *self.pending]

    async def acknowledge(self, events: list[dict[str, Any]]) -> None:
        self.acknowledged.extend(str(event.get("event_id") or "") for event in events)


def test_guidance_preempts_an_inflight_model_and_seals_at_terminal(tmp_path):
    async def scenario() -> None:
        started = threading.Event()
        cancelled = threading.Event()
        captured: list[list[dict[str, Any]]] = []

        async def model(arguments, _context):
            captured.append(list(arguments["messages"]))
            if len(captured) == 1:
                started.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.set()
            return {"content": "revised answer", "tool_calls": [], "model": "test"}

        inbox = WorkbenchAgentInbox("chat", run_id="run-guidance")
        channel = WorkbenchGuidanceChannel(inbox)
        channel.bind_owner_loop(asyncio.get_running_loop())
        session = _session(tmp_path, _registry(model), channel)
        try:
            session.submit("original request", run_id="run-guidance")
            draining = asyncio.create_task(session.drain())
            assert await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=3)
            accepted = await inbox.put_guidance(
                "use the corrected requirement",
                client_request_id="guidance-1",
            )
            await asyncio.wait_for(draining, timeout=5)

            assert cancelled.is_set()
            assert len(captured) == 2
            assert "use the corrected requirement" in captured[-1][-1]["content"]
            assert session.final_output("run-guidance")["content"] == "revised answer"
            assert any(event.type == "guidance.applied" for event in session.events())
            assert inbox._guidance_pending_count == 0
            with pytest.raises(GuidanceAdmissionClosed):
                await inbox.put_guidance(
                    "too late",
                    client_request_id="guidance-late",
                )
            assert accepted["event_id"]
        finally:
            session.close()
            channel.close()
            await inbox.close(termination_reason="completed")

    asyncio.run(scenario())


def test_terminal_guidance_turns_the_previous_answer_into_intermediate(tmp_path):
    async def scenario() -> None:
        channel = _ScriptedChannel([_event("terminal-guidance", "change the answer")])
        calls: list[list[dict[str, Any]]] = []

        async def model(arguments, _context):
            calls.append(list(arguments["messages"]))
            if len(calls) == 1:
                return {"content": "old answer", "tool_calls": [], "model": "test"}
            return {"content": "new answer", "tool_calls": [], "model": "test"}

        session = _session(tmp_path, _registry(model), channel)
        try:
            session.submit("question", run_id="run-terminal")
            await asyncio.wait_for(session.drain(), timeout=5)

            assistants = [
                node["value"]
                for node in session.snapshot()["nodes"]
                if node["value"].get("role") == "assistant"
            ]
            assert assistants[0]["content"] == "old answer"
            assert assistants[0]["intermediate"] is True
            assert assistants[-1]["content"] == "new answer"
            assert session.final_output("run-terminal")["content"] == "new answer"
            assert channel.acknowledged == ["terminal-guidance"]
            assert "change the answer" in calls[-1][-1]["content"]
        finally:
            session.close()

    asyncio.run(scenario())


def test_guidance_skips_tools_that_have_not_started(tmp_path):
    async def scenario() -> None:
        channel = _ScriptedChannel()
        executed: list[int] = []
        model_calls = 0

        async def mutate(arguments, _context):
            step = int(arguments["step"])
            executed.append(step)
            if step == 1:
                channel.pending.append(
                    _event("tool-guidance", "stop after the first mutation")
                )
            return {"step": step, "status": "done"}

        async def model(arguments, _context):
            nonlocal model_calls
            model_calls += 1
            if model_calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "first", "name": "mutate", "arguments": {"step": 1}},
                        {"id": "second", "name": "mutate", "arguments": {"step": 2}},
                    ],
                    "model": "test",
                }
            flattened = str(arguments["messages"])
            assert "user_guidance" in flattened
            assert "stop after the first mutation" in flattened
            return {"content": "stopped", "tool_calls": [], "model": "test"}

        tool = Plugin(
            "mutate",
            "mutate state",
            {
                "type": "object",
                "properties": {"step": {"type": "integer"}},
                "required": ["step"],
            },
            mutate,
            metadata={"permission_review": False},
        )
        session = _session(tmp_path, _registry(model, tool), channel)
        try:
            session.submit("perform both steps", run_id="run-tools")
            await asyncio.wait_for(session.drain(), timeout=5)

            assert executed == [1]
            tool_nodes = [
                node["value"]
                for node in session.snapshot()["nodes"]
                if node["value"].get("role") == "tool_results"
            ]
            assert tool_nodes[0]["results"][1]["value"]["status"] == "skipped"
            assert tool_nodes[0]["results"][1]["value"]["reason"] == "user_guidance"
            assert channel.acknowledged == ["tool-guidance"]
            assert session.final_output("run-tools")["content"] == "stopped"
        finally:
            session.close()

    asyncio.run(scenario())

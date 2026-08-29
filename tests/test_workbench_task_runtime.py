"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from cyrene.workbench.core_adapter.task_runtime import (
    TaskAgentRuntime,
    _pending_question,
    _project_tool_events,
    _task_system_extra,
)


def _snapshot() -> dict:
    return {
        "nodes": [
            {
                "id": "assistant-1",
                "created_at": "2026-08-26T00:00:00+00:00",
                "value": {
                    "role": "assistant",
                    "run_id": "run-1",
                    "tool_calls": [{
                        "id": "call-1",
                        "name": "toolbox",
                        "arguments": {
                            "operation": "invoke",
                            "name": "ask_user",
                            "arguments": {
                                "text": "选择执行范围",
                                "options": ["当前文件", {"label": "整个项目"}],
                            },
                        },
                    }],
                },
            },
            {
                "id": "tools-1",
                "created_at": "2026-08-26T00:00:01+00:00",
                "value": {
                    "role": "tool_results",
                    "run_id": "run-1",
                    "results": [{
                        "call_id": "call-1",
                        "name": "toolbox",
                        "success": True,
                        "value": {
                            "operation": "invoke",
                            "name": "ask_user",
                            "pack": "cyrene_control",
                            "result": {
                                "status": "awaiting_user",
                                "question_id": "q-1",
                            },
                        },
                        "error": "",
                    }],
                },
            },
        ]
    }


def test_pending_question_is_rebuilt_from_context_tree_tool_result():
    pending = _pending_question(_snapshot(), "run-1")

    assert pending == {
        "id": "q-1",
        "text": "选择执行范围",
        "options": ["当前文件", "整个项目"],
        "roundId": "run-1",
        "clientRequestId": "",
        "ownerLane": "execution",
        "allowCustom": True,
        "kind": "clarification",
    }


def test_toolbox_invocation_is_projected_from_context_tree():
    events = _project_tool_events(_snapshot(), "run-1")

    assert len(events) == 1
    assert events[0]["tool"] == "ask_user"
    assert events[0]["pack"] == "cyrene_control"
    assert events[0]["args"] == {
        "text": "选择执行范围",
        "options": ["当前文件", {"label": "整个项目"}],
    }
    assert events[0]["success"] is True
    assert events[0]["result"]["status"] == "awaiting_user"


def test_task_turn_persists_exact_host_context_on_user_node(monkeypatch, tmp_path):
    captured = {}

    class FakeBridge:
        def snapshot(self):
            return {"status": "idle", "run_id": ""}

        async def submit_result(self, text, *, run_id, metadata, **_kwargs):
            captured.update(text=text, run_id=run_id, metadata=dict(metadata))
            return SimpleNamespace(
                text="done",
                usage={},
                model="test-model",
                model_identity={},
                snapshot={"nodes": []},
                generation_duration_ms=1.0,
                output_tokens_per_second=1.0,
            )

        def close(self):
            captured["closed"] = True

    runtime = TaskAgentRuntime(bot=object(), db_path=str(tmp_path / "workbench.db"))
    monkeypatch.setattr(runtime, "_open_bridge", lambda **_kwargs: FakeBridge())
    project = {
        "id": "project-1",
        "name": "Project",
        "workspacePath": str(tmp_path),
    }
    session = {
        "id": "task-1",
        "title": "Inspect cache",
        "goal": "Persist context exactly once",
        "status": "running",
        "plan": [],
    }

    asyncio.run(runtime.run_turn(
        project=project,
        session=session,
        text="inspect",
        run_id="run-1",
        metadata={"ephemeral_context": "caller must not override host context"},
    ))

    assert captured["metadata"]["ephemeral_context"] == _task_system_extra(
        project,
        session,
        purpose="task",
        instruction="",
        attachments=(),
    )
    assert captured["closed"] is True


def test_task_and_goal_loop_respect_runtime_dependency_boundaries():
    src = Path(__file__).parents[1] / "src"
    paths = (
        src / "cyrene/workbench/core_adapter/task_runtime.py",
            src / "cyrene/workbench/tasks/task_execution_service.py",
            src / "cyrene/workbench/tasks/task_session_workflow_service.py",
            src / "cyrene/workbench/goals/goal_loop.py",
            src / "cyrene/workbench/goals/goal_loop_service.py",
        src / "cyrene/workbench/http/workbench/task_sessions.py",
        src / "cyrene/workbench/http/workbench/goal_loop.py",
        *(src / "cyrene/workbench/http/workbench/task_session_routes").glob("*.py"),
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden = (
        "cyrene.agent",
        "cyrene.tooling",
        "cyrene.tool_impl",
        "cyrene.subagent",
        "cyrene.model_runtime.client",
        "cyrene.model_runtime.compaction",
        "cyrene.workbench.chat",
        "cyrene.workbench.global_chat_service",
        "cyrene.workbench.runtime",
        "cyrene.workbench.runtime_facade",
        "cyrene.workbench.runtime_implementation",
        "cyrene.workbench.compat",
        "cyrene.workbench.generation_gateway",
    )

    assert not [name for name in forbidden if name in source]

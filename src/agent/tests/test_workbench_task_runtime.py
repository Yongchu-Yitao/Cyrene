from __future__ import annotations

from pathlib import Path

from agent.workbench.task_runtime import _pending_question, _project_tool_events


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


def test_task_and_goal_loop_sources_do_not_depend_on_removed_runtimes():
    src = Path(__file__).parents[2]
    paths = (
        src / "agent/workbench/task_runtime.py",
        src / "cyrene/workbench/task_execution_service.py",
        src / "cyrene/workbench/task_session_workflow_service.py",
        src / "cyrene/workbench/goal_loop.py",
        src / "cyrene/workbench/goal_loop_service.py",
        src / "route/workbench/task_sessions.py",
        src / "route/workbench/goal_loop.py",
        *(src / "route/workbench/task_session_routes").glob("*.py"),
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

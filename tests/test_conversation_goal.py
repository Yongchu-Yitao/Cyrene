from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cyrene.plugins.builtin.cyrene_goal import service as goal_service_module
from cyrene.plugins.builtin.cyrene_goal import plugin_pack
from cyrene.plugins.builtin.cyrene_goal.service import (
    ConversationGoalService,
    _reflection_committed,
)
from cyrene.workbench.core_adapter.bridge import AgentSessionRunError
from cyrene.plugins.contributions import (
    serialize_workbench_slash_command,
    validate_workbench_contributions,
    workbench_slash_commands,
)


def test_goal_pack_contributes_workflow_command_and_main_agent_tools() -> None:
    validate_workbench_contributions(plugin_pack)
    commands = workbench_slash_commands(plugin_pack)

    assert [command.id for command in commands] == ["goal"]
    assert commands[0].workflow_service == "goal"
    assert commands[0].workflow_action == "begin_negotiation"
    assert serialize_workbench_slash_command(plugin_pack, commands[0])["activation"] == {
        "kind": "pluginPacks",
        "id": "cyrene_goal",
    }
    assert {plugin.name for plugin in plugin_pack.plugins} == {
        "propose_goal",
        "submit_goal_result",
    }
    assert all(plugin.metadata.get("main_only") is True for plugin in plugin_pack.plugins)


def test_goal_reflection_requires_a_durable_context_rewrite() -> None:
    committed = SimpleNamespace(
        run_id="goal_reflect_1",
        snapshot={
            "nodes": [
                {
                    "value": {
                        "role": "context_reflection",
                        "run_id": "goal_reflect_1",
                    }
                }
            ]
        },
    )
    wrong_run = SimpleNamespace(
        run_id="goal_reflect_2",
        snapshot=committed.snapshot,
    )

    assert _reflection_committed(committed) is True
    assert _reflection_committed(wrong_run) is False


def test_goal_review_requires_exact_one_to_one_criterion_evidence(tmp_path) -> None:
    service = ConversationGoalService(db_path=str(tmp_path / "workbench.db"), bot=None)
    goal = {
        "revision": 2,
        "attempt": 3,
        "acceptanceCriteria": ["Tests pass", "Documentation is current"],
    }
    passing = service._normalize_review({
        "verdict": "pass",
        "criteria": [
            {"criterionIndex": 1, "criterion": "Tests pass", "passed": True, "evidence": "pytest: 12 passed"},
            {"criterionIndex": 2, "criterion": "Documentation is current", "passed": True, "evidence": "README inspected"},
        ],
        "criticalGaps": [],
    }, goal)
    duplicate = service._normalize_review({
        "verdict": "pass",
        "criteria": [
            {"criterionIndex": 1, "criterion": "Tests pass", "passed": True, "evidence": "pytest passed"},
            {"criterionIndex": 1, "criterion": "Tests pass", "passed": True, "evidence": "same criterion again"},
        ],
        "criticalGaps": [],
    }, goal)

    assert passing["verdict"] == "pass"
    assert duplicate["verdict"] == "fail"


def test_goal_lifecycle_projects_only_nonterminal_state_to_conversation(tmp_path) -> None:
    async def scenario() -> None:
        db_path = str(tmp_path / "workbench.db")
        service = ConversationGoalService(db_path=db_path, bot=None)
        chat_id = "chat-goal-lifecycle"
        chat = {
            "id": chat_id,
            "projectId": "project-1",
            "kind": "chat",
            "title": "Goal conversation",
            "status": "idle",
            "messages": [],
            "soulActive": True,
            "workspaceActive": True,
            "contextActivations": {},
            "remoteDeviceIds": [],
        }
        service.chat.repository.write({"chats": [chat]})
        service.chat.public_chat_light = lambda value: {  # type: ignore[method-assign]
            "id": value.get("id"),
            "projectId": value.get("projectId"),
            "activeGoal": value.get("activeGoal"),
        }
        wakes: list[str] = []
        service.wake = wakes.append  # type: ignore[method-assign]

        negotiating = await service.begin_negotiation(
            chat_id,
            initial_request="Ship the conversation Goal loop",
            project_id="project-1",
        )
        assert negotiating["status"] == "negotiating"
        assert service.chat.repository.get(chat_id)["activeGoal"]["status"] == "negotiating"

        negotiating.update({
            "status": "proposed",
            "phase": "awaiting_confirmation",
            "objective": "Ship the conversation Goal loop",
            "acceptanceCriteria": ["Goal stays active until independent review passes"],
            "durationSeconds": 3600,
        })
        await service.repository.save(negotiating)

        active = await service.confirm(chat_id, {})
        assert active["status"] == "active"
        assert wakes == [chat_id]
        assert service.chat.repository.get(chat_id)["activeGoal"]["status"] == "active"

        resized = await service.update(chat_id, {"durationSeconds": 5400})
        assert resized["status"] == "active"
        assert resized["durationSeconds"] == 5400

        revised = await service.update(chat_id, {
            "objective": "Ship and document the conversation Goal loop",
        })
        assert revised["status"] == "proposed"
        assert revised["revision"] == 2

        aborted = await service.abort(chat_id)
        assert aborted["status"] == "aborted"
        assert "activeGoal" not in service.chat.repository.get(chat_id)
        event_types = [item["event_type"] for item in await service.repository.events(chat_id)]
        assert event_types == [
            "negotiation_started",
            "goal_confirmed",
            "goal_updated",
            "goal_updated",
            "aborted",
        ]

    asyncio.run(scenario())


def test_goal_negotiation_start_rolls_back_every_durable_projection(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        db_path = str(tmp_path / "workbench.db")
        service = ConversationGoalService(db_path=db_path, bot=None)
        chat_id = "chat-goal-atomic"
        service.chat.repository.write({"chats": [{
            "id": chat_id,
            "projectId": "project-1",
            "kind": "chat",
            "title": "Atomic Goal",
            "status": "running",
            "messages": [{"id": "msg-goal", "role": "user", "content": "/goal"}],
        }]})
        service.chat.public_chat_light = lambda value: {  # type: ignore[method-assign]
            "id": value.get("id"),
            "projectId": value.get("projectId"),
            "activeGoal": value.get("activeGoal"),
        }
        before = service.chat.repository.get(chat_id)

        async def fail_milestone(*_args, **_kwargs):
            raise RuntimeError("milestone persistence failed")

        monkeypatch.setattr(service, "_milestone", fail_milestone)

        with pytest.raises(RuntimeError, match="milestone persistence failed"):
            await service.begin_negotiation(
                chat_id,
                initial_request="Atomic Goal",
                project_id="project-1",
            )

        assert await service.repository.get(chat_id) is None
        assert await service.repository.events(chat_id) == []
        assert service.chat.repository.get(chat_id) == before

    asyncio.run(scenario())


def test_goal_completion_publishes_a_conversation_notification(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        db_path = str(tmp_path / "workbench.db")
        service = ConversationGoalService(db_path=db_path, bot=None)
        service.chat.repository.write({"chats": [{
            "id": "chat-goal-notification",
            "projectId": "project-1",
            "kind": "chat",
            "title": "Release conversation",
            "status": "idle",
            "messages": [],
        }]})
        published: list[dict[str, object]] = []
        monkeypatch.setattr(
            goal_service_module,
            "append_notification",
            lambda **payload: published.append(payload),
        )

        await service._notify_goal_state({
            "id": "goal-1",
            "chatId": "chat-goal-notification",
            "projectId": "project-1",
            "revision": 3,
            "status": "completed",
            "completionMode": "review_passed",
            "objective": "Ship the release",
        }, "completed")

        assert len(published) == 1
        notice = published[0]
        assert notice["source"] == "conversation_goal_completed"
        assert notice["project_ref"] == "project-1"
        assert notice["link_label"] == "Release conversation"
        assert notice["meta"] == {
            "chatId": "chat-goal-notification",
            "goalId": "goal-1",
            "goalRevision": 3,
            "goalStatus": "completed",
            "completionMode": "review_passed",
        }

    asyncio.run(scenario())


def test_goal_reviewer_failure_preserves_candidate_and_resumes_review(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        db_path = str(tmp_path / "workbench.db")
        service = ConversationGoalService(db_path=db_path, bot=None)
        await service.repository.ensure_schema()
        chat_id = "chat-review-unavailable"
        service.chat.repository.write({"chats": [{
            "id": chat_id,
            "projectId": "project-1",
            "kind": "chat",
            "title": "Review recovery",
            "status": "idle",
            "messages": [],
        }]})
        candidate = {
            "summary": "ready",
            "evidence": ["focused test passed"],
            "deliverables": [],
        }
        goal = await service.repository.save({
            "id": "goal-review-1",
            "chatId": chat_id,
            "projectId": "project-1",
            "revision": 1,
            "status": "reviewing",
            "phase": "reviewing",
            "objective": "Verify reviewer recovery",
            "acceptanceCriteria": ["Review completes"],
            "constraints": [],
            "outOfScope": [],
            "durationSeconds": 3600,
            "activeSeconds": 0.0,
            "activeStartedAt": "",
            "attempt": 1,
            "candidate": candidate,
            "review": None,
            "childContextIds": [],
            "completionMode": "",
            "stopReason": "",
        })
        calls = 0

        async def fail_review(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AgentSessionRunError("模型调用失败。")

        async def config(*_args, **_kwargs):
            return SimpleNamespace()

        milestones: list[str] = []
        notifications: list[str] = []

        async def milestone(_goal, event_type, _text):
            milestones.append(event_type)

        async def notify(_goal, event_type):
            notifications.append(event_type)

        service.run_manager = SimpleNamespace(
            conversation_runtime=SimpleNamespace(send=fail_review),
        )
        service._config = config  # type: ignore[method-assign]
        service._project = lambda _goal: asyncio.sleep(0)  # type: ignore[method-assign]
        service._milestone = milestone  # type: ignore[method-assign]
        service._notify_goal_state = notify  # type: ignore[method-assign]
        monkeypatch.setattr(goal_service_module, "REVIEW_RETRY_DELAYS", (0.0,))

        await service._review(SimpleNamespace(publish=lambda _event: None), goal)

        paused = await service.repository.get(chat_id)
        assert calls == 2
        assert paused is not None
        assert paused["status"] == "paused"
        assert paused["phase"] == "paused"
        assert paused["pausedFromStatus"] == "reviewing"
        assert paused["stopReason"] == "review_provider_unavailable"
        assert paused["candidate"] == candidate
        assert paused["review"] is None
        assert milestones == [
            "review_started",
            "review_retry_scheduled",
            "review_unavailable",
        ]
        assert notifications == ["review_unavailable"]
        assert [item["event_type"] for item in await service.repository.events(chat_id)] == [
            "review_retry_scheduled",
            "review_unavailable",
        ]

        # Goals affected before this fix were stored as a generic blocked
        # driver error without pausedFromStatus. Preserve and review their
        # submitted candidate instead of starting another execution attempt.
        paused.update({
            "status": "blocked",
            "phase": "blocked",
            "pausedFromStatus": "",
            "stopReason": "driver_error",
        })
        await service.repository.save(paused)
        wakes: list[str] = []
        service.wake = wakes.append  # type: ignore[method-assign]
        resumed = await service.resume(chat_id)
        assert resumed["status"] == "reviewing"
        assert resumed["candidate"] == candidate
        assert resumed["stopReason"] == ""
        assert wakes == [chat_id]

    asyncio.run(scenario())

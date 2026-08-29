from __future__ import annotations

from typing import Any

import pytest

from cyrene.runtime import settings_store
from cyrene.workbench.planning import planning_contracts
from cyrene.workbench.chat.chat_service import ChatService


def test_planning_contract_uses_effective_language(monkeypatch) -> None:
    monkeypatch.setattr(settings_store, "get", lambda key, default="": "en")

    english = planning_contracts._workbench_planner_system_prompt()
    assert "Write all user-visible values in English" in english
    assert '"dependsOnStepIndexes"' in english
    assert '"acceptanceCriteria"' in english
    assert planning_contracts._workbench_template_labels()["blank"] == "Blank project"

    monkeypatch.setattr(settings_store, "get", lambda key, default="": "zh-CN")

    chinese = planning_contracts._workbench_planner_system_prompt()
    assert "全部用户可见内容使用简体中文" in chinese
    assert '"dependsOnStepIndexes"' in chinese
    assert '"acceptanceCriteria"' in chinese
    assert planning_contracts._workbench_template_labels()["blank"] == "空白项目"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "required", "forbidden"),
    [
        ("en-US", "Write every user-visible value in English", "全部使用简体中文"),
        ("zh-CN", "所有用户可见字段值均使用简体中文", "Write every user-visible value in English"),
    ],
)
async def test_chat_to_task_brief_prompt_follows_app_language(
    monkeypatch,
    language: str,
    required: str,
    forbidden: str,
) -> None:
    monkeypatch.setattr(settings_store, "get", lambda key, default="": language)
    captured: dict[str, Any] = {}

    async def secondary_json(
        prompt: str,
        *,
        max_tokens: int,
        caller: str,
    ) -> dict[str, Any]:
        captured.update(
            prompt=prompt,
            max_tokens=max_tokens,
            caller=caller,
        )
        return {
            "title": "Example",
            "goal": "Ship it",
            "constraints": [],
            "acceptanceCriteria": [],
        }

    service = object.__new__(ChatService)
    monkeypatch.setattr(service, "_secondary_json", secondary_json)
    result = await service.summarize_chat_to_brief(
        {
            "title": "Discussion",
            "messages": [
                {"role": "user", "content": "Please ship the feature."},
                {"role": "assistant", "content": "I will prepare the implementation."},
            ],
        },
        {"name": "Cyrene"},
    )

    assert result is not None
    assert required in captured["prompt"]
    assert forbidden not in captured["prompt"]
    assert "title, goal" in captured["prompt"] or "title、goal" in captured["prompt"]
    assert "acceptanceCriteria" in captured["prompt"]
    assert captured["caller"] == "workbench_chat_to_task_brief"


@pytest.mark.asyncio
async def test_chat_group_metadata_normalizes_locale_and_localizes_errors(
    monkeypatch,
) -> None:
    service = object.__new__(ChatService)

    with pytest.raises(ValueError, match="At least two chat members"):
        await service.generate_chat_group_metadata([], lang="en-GB")
    with pytest.raises(ValueError, match="至少需要两个对话成员"):
        await service.generate_chat_group_metadata([], lang="zh-TW")

    captured: dict[str, Any] = {}

    async def secondary_json(
        prompt: str,
        *,
        max_tokens: int,
        caller: str,
    ) -> dict[str, Any]:
        captured["prompt"] = prompt
        return {"title": "Shared work", "summary": "A shared topic"}

    monkeypatch.setattr(service, "_secondary_json", secondary_json)
    metadata = await service.generate_chat_group_metadata(
        [
            {"title": "First", "preview": "One"},
            {"title": "Second", "preview": "Two"},
        ],
        lang="en-US",
    )

    assert metadata["lang"] == "en"
    assert "Write both user-visible values in English" in captured["prompt"]


@pytest.mark.asyncio
async def test_chat_group_metadata_retries_generic_titles_with_explicit_constraints(
    monkeypatch,
) -> None:
    service = object.__new__(ChatService)
    prompts: list[str] = []
    responses = [
        {"title": "New chat group", "summary": "A broad placeholder"},
        {"title": "Release planning", "summary": "Coordinates release scope and readiness."},
    ]

    async def secondary_json(
        prompt: str,
        *,
        max_tokens: int,
        caller: str,
    ) -> dict[str, Any]:
        prompts.append(prompt)
        assert max_tokens == 512
        assert caller == "workbench_chat_group_metadata"
        return responses[len(prompts) - 1]

    monkeypatch.setattr(service, "_secondary_json", secondary_json)
    metadata = await service.generate_chat_group_metadata(
        [
            {"title": "Release checklist", "preview": "Prepare the final checklist."},
            {"title": "Launch readiness", "preview": "Review launch blockers."},
        ],
        lang="en",
    )

    assert len(prompts) == 2
    assert "under 48 characters" in prompts[0]
    assert "under 110 characters" in prompts[0]
    assert "New chat group" in prompts[0]
    assert "previous attempt returned" in prompts[1]
    assert metadata == {
        "title": "Release planning",
        "summary": "Coordinates release scope and readiness.",
        "lang": "en",
    }


@pytest.mark.asyncio
async def test_chat_group_metadata_retries_empty_locked_summary_and_keeps_title_empty(
    monkeypatch,
) -> None:
    service = object.__new__(ChatService)
    prompts: list[str] = []

    async def secondary_json(prompt: str, **_kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"title": "Must be ignored", "summary": ""}
        return {"title": "Must still be ignored", "summary": "共同处理发布准备。"}

    monkeypatch.setattr(service, "_secondary_json", secondary_json)
    metadata = await service.generate_chat_group_metadata(
        [
            {"title": "发布清单", "preview": "核对发布项。"},
            {"title": "上线准备", "preview": "检查上线阻塞。"},
        ],
        lang="zh",
        title_locked=True,
        current_title="用户标题",
    )

    assert len(prompts) == 2
    assert "标题不超过 18 个汉字" in prompts[0]
    assert "summary 必须为非空字符串" in prompts[1]
    assert metadata == {"title": "", "summary": "共同处理发布准备。", "lang": "zh"}

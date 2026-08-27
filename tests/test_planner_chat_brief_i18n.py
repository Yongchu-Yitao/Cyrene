from __future__ import annotations

from typing import Any

import pytest

from cyrene.runtime import settings_store
from cyrene.workbench import planning_contracts
from cyrene.workbench.chat_service import ChatService


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

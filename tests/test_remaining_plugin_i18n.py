from __future__ import annotations

import json

import pytest

from agent.plugin import PluginContext


@pytest.mark.asyncio
async def test_extension_search_localizes_and_masks_source_failures(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_extensions import search_environment

    class Service:
        def list_extensions(self):
            return {
                "mcp": [],
                "cli": [],
                "toolchains": [],
                "infrastructure": {},
            }

        async def search(self, _kind, _query, **_kwargs):
            raise RuntimeError("private registry diagnostic")

    monkeypatch.setattr(
        search_environment,
        "get_extension_service",
        lambda: Service(),
    )
    payload = json.loads(
        await search_environment._tool_search_environment(
            {"kind": "mcp", "query": "demo"},
            PluginContext(data={"language": "zh"}),
        )
    )

    assert payload["ok"] is False
    assert payload["source_errors"]["mcp"] == "此目录源暂时不可用。"
    assert "private registry diagnostic" not in json.dumps(payload)


def test_remote_tool_error_uses_stable_code_and_masks_exception():
    from agent.plugin.plugin_impl.cyrene_remote.common import remote_tool_error

    payload = remote_tool_error(
        RuntimeError("private transport diagnostic"),
        PluginContext(data={"language": "zh"}),
    )

    assert payload["code"] == "remote_controller_error"
    assert payload["error"] == "远程操作失败。"
    assert "private transport diagnostic" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_remote_command_boundary_replaces_raw_error(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_remote import commands

    executor = object.__new__(commands.RemoteCommandExecutor)

    async def execute(*_args, **_kwargs):
        return {
            "ok": False,
            "code": "artifact_unavailable",
            "error": "private filesystem diagnostic",
        }

    monkeypatch.setattr(executor, "_execute", execute)
    monkeypatch.setattr(
        commands,
        "localized",
        lambda _en, zh, **_values: zh,
    )

    payload = await executor("device", "artifacts.read", {}, "project")
    assert payload == {
        "ok": False,
        "code": "artifact_unavailable",
        "error": "产物不可用。",
    }


@pytest.mark.asyncio
async def test_learned_skill_lookup_masks_internal_exception(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_skills import get_learned_skill
    from agent.plugin.plugin_impl.cyrene_skills import orchestrator

    async def fail(*_args, **_kwargs):
        raise RuntimeError("private learning database diagnostic")

    monkeypatch.setattr(orchestrator, "get_learned_skill_by_name", fail)
    payload = json.loads(
        await get_learned_skill._tool_get_learned_skill(
            {"name": "demo"},
            PluginContext(data={"language": "zh"}),
        )
    )

    assert payload["code"] == "learned_skill_retrieval_failed"
    assert payload["error"] == "无法获取学习技能。"
    assert "private learning database diagnostic" not in json.dumps(payload)


def test_skill_validation_and_default_soul_follow_requested_language(tmp_path):
    from agent.plugin.plugin_impl.cyrene_skills.skills import (
        install_skill_from_path,
    )
    from agent.plugin.plugin_impl.cyrene_soul.store import default_soul

    result = install_skill_from_path(
        tmp_path / "missing",
        language="zh",
    )

    assert result["code"] == "invalid_skill_source"
    assert result["error"] == "技能来源路径无效。"
    assert "personal AI companion" in default_soul(language="en")
    assert "私人 AI 伙伴" in default_soul(language="zh")


def test_entity_reminder_prompt_follows_session_language(tmp_path):
    from agent.plugin.plugin_impl.cyrene_entity.service import EntityService

    service = EntityService(
        str(tmp_path / "entities.db"),
        reminders=object(),
        language="zh",
    )

    assert service._reminder_prompt({"title": "发布版本"}) == (
        "提醒用户：发布版本 到期了。"
    )

"""Exercise the language pack through the real session and Hook lifecycle."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from cyrene.core.plugin import Plugin, PluginRegistry
from cyrene.core.session import AgentSession
from cyrene.plugins.builtin.cyrene_plugin_development.tools import validate_plugin_source


def _session(tmp_path):
    registry = PluginRegistry()
    registry.register_plugin(
        Plugin(
            name="LanguageTestModel",
            description="Unused model for context projection tests.",
            input_schema={"type": "object"},
            handler=lambda _arguments, _context: {"content": "", "tool_calls": []},
            kind="model",
        ),
        source="test",
    )
    return AgentSession(
        tmp_path / "data",
        tmp_path / "workspace",
        tmp_path / "plugin_impl",
        tree_id="language-chat",
        registry=registry,
        model_plugin="LanguageTestModel",
        # A session's old run metadata must not override the current setting.
        plugin_context_data={"run_context": {"language": "en"}},
    )


@pytest.fixture
def language_pack(tmp_path):
    source = (
        Path(__file__).parents[1] / "src" / "cyrene" / "plugins" / "builtin"
        / "cyrene_user_language"
    )
    assert validate_plugin_source(source)["ok"] is True
    shutil.copytree(source, tmp_path / "plugin_impl" / source.name)


def _language_mount(session):
    mounts = asyncio.run(session.build_model_mounts())
    selected = [mount for mount in mounts if mount["kind"] == "user_language"]
    assert len(selected) == 1
    assert selected[0]["source"] == "cyrene_user_language"
    return selected[0]["content"]


def test_language_changes_each_turn_and_rebinds_on_reopen(
    tmp_path, monkeypatch, language_pack,
):
    settings = {"app_language": "zh-CN"}
    monkeypatch.setattr("cyrene.platform.settings_store.get", settings.get)

    first = _session(tmp_path)
    try:
        assert first.registry.pack_locked("cyrene_user_language") is True
        content = _language_mount(first)
        assert '<user_language code="zh">' in content
        assert "Simplified Chinese" in content
        assert "unless the user explicitly requests another language" in content
        assert "progress updates" in content
        settings["app_language"] = "en-US"
        content = _language_mount(first)
        assert '<user_language code="en">' in content
        assert "Simplified Chinese" not in content
    finally:
        first.close()

    settings["app_language"] = "zh"
    reopened = _session(tmp_path)
    try:
        assert '<user_language code="zh">' in _language_mount(reopened)
        assert len([
            hook for hook in reopened.hooks.list()
            if hook.id == "cyrene-user-language-turn-start"
        ]) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("configured", ["", "unsupported", None])
def test_language_uses_shared_system_fallback(
    tmp_path, monkeypatch, language_pack, configured,
):
    monkeypatch.setattr(
        "cyrene.platform.settings_store.get", lambda *_args: configured,
    )
    monkeypatch.setattr("cyrene.localization.system_language", lambda: "zh")
    session = _session(tmp_path)
    try:
        assert '<user_language code="zh">' in _language_mount(session)
    finally:
        session.close()

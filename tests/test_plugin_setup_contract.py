"""Setup is synchronous; asynchronous work belongs to executable callbacks."""
import functools
import inspect
import json

import pytest
from fastapi import APIRouter, FastAPI

from cyrene.core import AgentSession
from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.core.plugin.extensions import APPLICATION_SETUP, SESSION_SETUP, ExtensionContribution
from cyrene.plugins.application import PluginApplicationHost
from cyrene.plugins.builtin.cyrene_plugin_development import tools
from cyrene.plugins.validation import validate_plugin_source


async def async_setup(context):
    context.provide("weather.state", {})


class AsyncSetup:
    async def __call__(self, context):
        await async_setup(context)


@pytest.mark.parametrize("callback", [async_setup, AsyncSetup(), functools.partial(async_setup)])
@pytest.mark.parametrize("field,point", [("setup", SESSION_SETUP), ("application_setup", APPLICATION_SETUP)])
def test_async_setup_rejected_at_declaration(callback, field, point):
    with pytest.raises(TypeError, match="must be synchronous"):
        PluginPack("weather", "weather", (), **{field: callback})
    with pytest.raises(TypeError, match="must be synchronous"):
        ExtensionContribution(point, callback)


@pytest.mark.parametrize("field", ["setup", "application_setup"])
@pytest.mark.parametrize("imported", [False, True])
def test_static_validation_rejects_async_setup_without_executing_source(tmp_path, field, imported):
    definition = "async def initialize(context):\n    raise RuntimeError('must never execute')\n"
    if imported:
        (tmp_path / "tool.py").write_text(definition)
        definition = "from .tool import initialize as initialize\n"
    (tmp_path / "__init__.py").write_text(
        "from cyrene.core.plugin import PluginPack\n" + definition +
        f"plugin_pack = PluginPack('weather', 'weather', (), {field}=initialize)\n")
    result = validate_plugin_source(tmp_path)
    assert not result["ok"]
    assert any("must be synchronous" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_scaffold_edits_rejected_before_install(tmp_path, monkeypatch):
    context = PluginContext(workspace=tmp_path, data={"language": "zh"})
    await tools.scaffold({"path": "weather", "pack_id": "weather", "plugin_type": "application_plugin"}, context)
    source = tmp_path / "weather" / "application.py"
    source.write_text(source.read_text().replace("def setup_application(", "async def setup_application("))
    # Host lookup is harmless; install must stop at static validation.
    monkeypatch.setattr(tools, "application_plugin_scope", lambda: None)
    result = json.loads(await tools.install({"path": "weather"}, context))
    assert not result["ok"]
    assert any("必须是同步函数" in error for error in result["errors"])


def _wrapped_setup(coroutines):
    def setup(context):
        context.provide("partial.service", True)
        result = async_setup(context)
        coroutines.append(result)
        return result
    return setup


@pytest.mark.asyncio
async def test_application_wrapper_cannot_report_running_or_publish_partial_services(tmp_path):
    coroutines = []
    registry = PluginRegistry(include_core=False)
    registry.register_pack(PluginPack("weather", "weather", (), application_setup=_wrapped_setup(coroutines)), source="test")
    host = PluginApplicationHost(app=FastAPI(), registry=registry, bot=None,
        db_path=str(tmp_path / "state.db"), data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugins")
    host.attach(APIRouter())
    await host.startup()
    try:
        assert "must be synchronous" in host.setup_failures["weather"]
        assert not host.pack_running("weather")
        assert not host.pack_operational("weather")
        assert host.service("partial.service") is None
        assert host.service("weather.state") is None
        assert inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED
    finally:
        await host.shutdown()


def test_session_wrapper_rolls_back_partial_services(tmp_path):
    coroutines = []
    registry = PluginRegistry(include_core=False)
    registry.register_plugin(Plugin("MiniMax", "model", {"type": "object"},
        lambda a, c: {"content": "", "tool_calls": []}, kind="model"), source="test")
    registry.register_pack(PluginPack("weather", "weather", (), setup=_wrapped_setup(coroutines)), source="test")
    session = AgentSession(tmp_path / "data", tmp_path / "workspace", tmp_path / "plugins", registry=registry)
    try:
        assert "must be synchronous" in session._plugins.setup_failures["weather"]
        assert "weather" not in session._plugins.attachments
        assert "partial.service" not in session._plugin_services()
        assert inspect.getcoroutinestate(coroutines[0]) == inspect.CORO_CLOSED
    finally:
        session.close()

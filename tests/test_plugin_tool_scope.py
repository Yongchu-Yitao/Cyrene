"""Tool scopes leave shared registration/activation and ordinary Agents intact."""
import asyncio
from dataclasses import replace

import pytest
from cyrene.core.plugin import Plugin, PluginPack, PluginRegistry, PluginRuntime, PluginUnavailableError
from cyrene.core.plugin.tool_scope import PluginToolScope, ScopedPluginRegistry
from cyrene.plugins.builtin.session_management import plugin_pack as management, TOOL_NAMES
from cyrene.workbench.core_adapter.assistant_profile import personal_assistant_config
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig


def tool(name, **metadata):
    return Plugin(name=name, description=name, input_schema={"type": "object", "properties": {}},
                  handler=lambda a, c: "ok", metadata=metadata)


def test_live_scope_enforces_discovery_describe_direct_invoke_and_reload():
    async def scenario():
        registry = PluginRegistry()
        registry.register_pack(PluginPack(id="mixed", description="Mixed tools", plugins=(tool("Allowed"), tool("Blocked"))), source="test")
        registry.register_plugin(tool("DirectBlocked", agent_exposure="direct"), source="test")
        before = registry.direct_tool_definitions()
        ordinary = PluginRuntime(registry)
        before_list = (await ordinary.call("toolbox", {"operation": "list"})).value
        view = ScopedPluginRegistry(registry, PluginToolScope(tool_names=frozenset({"Allowed"})))
        runtime = PluginRuntime(view)
        names = {d["function"]["name"] for d in view.direct_tool_definitions()}
        assert {"Bash", "Read", "Write", "toolbox", "load_context"} <= names
        assert "DirectBlocked" not in names
        listing = await runtime.call("toolbox", {"operation": "list"})
        assert listing.success and listing.value["packs"] == ["mixed"]
        description = await runtime.call("toolbox", {"operation": "describe", "name": "mixed"})
        assert [p["name"] for p in description.value["plugins"]] == ["Allowed"]
        assert (await runtime.call("Allowed", {})).success
        assert (await runtime.call("toolbox", {"operation": "invoke", "name": "Allowed", "arguments": {}})).success
        for name in ["Blocked", "DirectBlocked"]:
            assert not (await runtime.call(name, {})).success
            for operation in ["describe", "invoke"]:
                assert not (await runtime.call("toolbox", {"operation": operation, "name": name})).success
        # An existing view must continue filtering newly registered/reloaded tools.
        registry.register_pack(PluginPack(id="later", description="Later", plugins=(tool("Later"),)), source="test")
        assert "later" not in (await runtime.call("toolbox", {"operation": "list"})).value["packs"]
        assert not (await runtime.call("Later", {})).success
        assert registry.direct_tool_definitions() == before
        assert before_list["packs"] == ["mixed"]
        assert (await ordinary.call("Blocked", {})).success
        assert (await ordinary.call("DirectBlocked", {})).success
        assert "later" in (await ordinary.call("toolbox", {"operation": "list"})).value["packs"]
        registry.set_plugin_enabled("Allowed", False)
        assert not view.plugin_accessible("Allowed")
        assert not (await runtime.call("Allowed", {})).success
    asyncio.run(scenario())


def test_assistant_profile_does_not_expand_ordinary_agent_surface():
    async def scenario():
        registry = PluginRegistry()
        registry.register_pack(PluginPack(id="ordinary", description="Ordinary", plugins=(tool("OrdinaryTool"),)), source="test")
        runtime = PluginRuntime(registry)
        definitions = registry.tool_definitions()
        direct = registry.direct_tool_definitions()
        listing = (await runtime.call("toolbox", {"operation": "list"})).value
        registry.register_pack(management, source="test")
        config = ConversationConfig(session_id="assistant", workspace_dir="/tmp", db_path="/tmp/unused")
        assistant = personal_assistant_config(replace(config, extra_direct_tool_names=("OrdinaryTool",)))
        assert config.tool_scope is None
        assert "OrdinaryTool" not in assistant.extra_direct_tool_names
        view = ScopedPluginRegistry(registry, assistant.tool_scope)
        for name in TOOL_NAMES:
            assert registry.registered(name).plugin.metadata["model_visible"] == "assistant"
            assert not registry.plugin_accessible(name)
            with pytest.raises(PluginUnavailableError):
                registry.resolve(name)
            assert not (await runtime.call(name, {})).success
            assert not (await runtime.call("toolbox", {"operation": "invoke", "name": name, "arguments": {}})).success
            assert view.resolve(name).name == name
            with pytest.raises(PluginUnavailableError):
                view.resolve(name, agent_id="child")
        assert not view.plugin_accessible("OrdinaryTool")
        core_names = {d["function"]["name"] for d in view.direct_tool_definitions()}
        assert core_names == {"Bash", "Read", "Write", "load_context", "unload_context", "append_context", "replace_context", "switch_context"}
        assert not (await PluginRuntime(view).call("toolbox", {"operation": "list"})).success
        assert (await runtime.call("toolbox", {"operation": "list"})).success
        assert registry.tool_definitions() == definitions
        assert registry.direct_tool_definitions() == direct
        assert (await runtime.call("toolbox", {"operation": "list"})).value == listing
        for name in ["session_management", *TOOL_NAMES]:
            assert not (await runtime.call("toolbox", {"operation": "describe", "name": name})).success
        # Merely constructing an assistant view cannot replace the shared toolbox.
        assert (await runtime.call("OrdinaryTool", {})).success
    asyncio.run(scenario())


def test_ordinary_session_has_no_assistant_notification_hook():
    from types import SimpleNamespace
    from cyrene.plugins.builtin.session_management import setup

    hooks = SimpleNamespace(
        list=lambda: [],
        register=lambda *a, **k: pytest.fail("Ordinary session acquired assistant Hook"),
    )
    setup(SimpleNamespace(
        services={"session_management": SimpleNamespace(store=SimpleNamespace(session=lambda _: None))},
        agent_id="main", data={"session_id": "ordinary"}, tree_id="ordinary", hooks=hooks,
    ))



def test_assistant_audience_cannot_be_granted_by_tool_name_or_customization():
    registry = PluginRegistry()
    registry.register_pack(management, source="test")
    view = ScopedPluginRegistry(registry, PluginToolScope(tool_names=frozenset(TOOL_NAMES)))
    for name in TOOL_NAMES:
        assert not view.plugin_accessible(name)
        with pytest.raises(PluginUnavailableError):
            view.resolve(name)
        registry.customize_tool(name, {"agent_exposure": "direct"})
        plugin = registry.registered(name).plugin
        assert plugin.metadata["model_visible"] == "assistant"
        assert not registry.plugin_accessible(name)
        assert plugin.model_visible is False
    with pytest.raises(TypeError):
        tool("InvalidAudience", model_visible="ordinary")

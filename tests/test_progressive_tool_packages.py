import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def _names(defs):
    return [item["function"]["name"] for item in defs]


def test_main_wire_bundle_is_the_fixed_29_tool_contract():
    from cyrene.tooling import get_main_wire_tool_defs

    defs = get_main_wire_tool_defs()
    assert _names(defs) == [
        "use_tools", "send_message", "ask_user", "quit", "enter_plan_mode",
        "update_plan_progress", "DeepReflect", "Read", "Write", "Edit",
        "Glob", "Grep", "Bash", "WebSearch", "WebFetch",
        "AnalyzeAttachment", "code_tools", "browser_tools",
        "desktop_tools", "memory_tools", "knowledge_tools", "task_tools",
        "entity_tools", "map_tools", "subagent_tools", "delivery_tools",
        "skill_tools", "remote_tools", "integration_tools",
    ]
    assert json.dumps(defs, sort_keys=True) == json.dumps(
        get_main_wire_tool_defs(),
        sort_keys=True,
    )


def test_every_native_tool_is_either_direct_or_in_exactly_one_pack():
    from cyrene.tooling.catalog import all_capabilities
    from cyrene.tooling.wire import DIRECT_TOOL_NAMES
    from cyrene.tooling.catalog import TOOL_DEFS

    native_names = {
        item["function"]["name"]
        for item in TOOL_DEFS
    }
    direct_names = set(DIRECT_TOOL_NAMES) - {"use_tools"}
    concrete_names = [
        capability.concrete_name
        for capability in all_capabilities(include_disabled=True)
        if not capability.external
    ]

    assert len(concrete_names) == len(set(concrete_names))
    assert direct_names.isdisjoint(concrete_names)
    assert native_names == direct_names | set(concrete_names)


def test_send_message_is_direct_even_when_delivery_package_is_disabled(
    monkeypatch,
):
    from cyrene.tooling import catalog, snapshot, wire
    from cyrene.tooling.gateway import resolve_wire_call

    def enabled(wire_name):
        return wire_name != "delivery_tools"

    monkeypatch.setattr(catalog, "is_tool_pack_enabled", enabled)
    monkeypatch.setattr(snapshot, "is_tool_pack_enabled", enabled)
    monkeypatch.setattr(wire, "is_tool_pack_enabled", enabled)

    assert "send_message" in _names(wire.get_main_wire_tool_defs())
    assert "delivery_tools" not in _names(wire.get_main_wire_tool_defs())
    assert "send_message" not in _names(wire.get_subagent_wire_tool_defs())
    assert "send_message" in _names(catalog.get_active_tool_defs())
    assert "delivery.send_message" not in {
        item.capability_id
        for item in catalog.capabilities_for_pack(
            "delivery_tools",
            include_disabled=True,
        )
    }

    frozen = snapshot.build_catalog_snapshot("main")
    assert "send_message" in frozen.enabled_capability_ids
    resolution = resolve_wire_call(
        "send_message",
        {"text": "Working on it."},
        catalog_snapshot=frozen,
    )
    assert resolution.capability_id == "send_message"
    assert resolution.concrete_name == "send_message"


def test_settings_and_dynamic_integrations_do_not_mutate_cached_wire_defs(monkeypatch):
    from cyrene.tooling import get_main_wire_tool_defs
    from cyrene.tooling import catalog as registry

    before = json.dumps(get_main_wire_tool_defs(), sort_keys=True)
    monkeypatch.setattr(
        registry,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name == "integration_tools",
    )
    monkeypatch.setattr(
        registry,
        "_mcp_definitions",
        lambda: {
            "late_mcp_tool": {
                "type": "function",
                "function": {
                    "name": "late_mcp_tool",
                    "description": "Loaded after the run began.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        },
    )

    assert json.dumps(get_main_wire_tool_defs(), sort_keys=True) == before
    assert registry.discover_capabilities("knowledge_tools") == []
    assert [
        item["id"]
        for item in registry.discover_capabilities("integration_tools")
    ] == ["integration.late_mcp_tool"]


def test_knowledge_pack_has_exact_boundary():
    from cyrene.tooling import discover_capabilities

    ids = {
        item["id"]
        for item in discover_capabilities("knowledge_tools", limit=50)
    }
    assert ids == {
        "knowledge.list_documents",
        "knowledge.search",
        "knowledge.library.list",
        "knowledge.library.search",
        "knowledge.library.update_metadata",
    }
    assert "AnalyzeAttachment" not in ids
    assert "WebSearch" not in ids
    assert not any("deep_research" in item.casefold() for item in ids)


def test_memory_pack_exposes_inventory_listing():
    from cyrene.tooling import discover_capabilities

    ids = {
        item["id"]
        for item in discover_capabilities("memory_tools", limit=50)
    }
    assert "memory.list" in ids


def test_static_memory_prompt_exposes_proactive_save_triggers_before_discovery():
    from cyrene.agent.prompts import (
        _MAIN_AGENT_PROMPT_TEMPLATE,
        _TOOL_PACK_PROMPT_TERMS,
        prompt_for_enabled_tool_packs,
    )

    rendered = prompt_for_enabled_tool_packs(
        _MAIN_AGENT_PROMPT_TEMPLATE,
        set(_TOOL_PACK_PROMPT_TERMS),
    )

    assert "use `memory.project.save` proactively" in rendered
    assert "This decision rule is available before tool discovery" in rendered
    assert "durable environment facts learned from tool results" in rendered
    assert "Do not wait for the user to ask you to remember them" in rendered


def test_static_entity_prompt_requires_foreground_extraction_with_steward_fallback():
    from cyrene.agent.prompts import (
        _MAIN_AGENT_PROMPT_TEMPLATE,
        _TOOL_PACK_PROMPT_TERMS,
        prompt_for_enabled_tool_packs,
    )

    rendered = prompt_for_enabled_tool_packs(
        _MAIN_AGENT_PROMPT_TEMPLATE,
        set(_TOOL_PACK_PROMPT_TERMS),
    )

    assert "前台主动提取 + 后台兜底" in rendered
    assert "先用 `entity.query` 去重" in rendered
    assert '调用 `entity.track`（source="extracted"' in rendered
    assert "后台存在不免除前台 Agent 的主动提取责任" in rendered


def test_package_switch_omits_gateway_and_member_metadata_from_model_context(
    monkeypatch,
):
    from cyrene.agent.prompts import (
        _MAIN_AGENT_PROMPT_TEMPLATE,
        prompt_for_enabled_tool_packs,
    )
    from cyrene.tooling import catalog, wire

    monkeypatch.setattr(
        catalog,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name != "browser_tools",
    )
    monkeypatch.setattr(
        wire,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name != "browser_tools",
    )

    assert catalog.discover_capabilities("browser_tools") == []
    assert catalog.capabilities_for_pack(
        "browser_tools",
        include_disabled=True,
    )
    wire_defs = wire.get_main_wire_tool_defs()
    assert "browser_tools" not in _names(wire_defs)
    assert "code_tools" in _names(wire_defs)
    rendered = json.dumps(wire_defs, ensure_ascii=False)
    assert "Persistent browser navigation" not in rendered
    filtered_prompt = prompt_for_enabled_tool_packs(
        _MAIN_AGENT_PROMPT_TEMPLATE,
        set(wire.enabled_module_tool_names()),
    )
    assert "browser_tools" not in filtered_prompt
    assert "browser.navigate" not in filtered_prompt
    assert "Prefer clicking visible page UI" not in filtered_prompt
    assert "browser file uploads" not in filtered_prompt
    assert "code_tools" in filtered_prompt
    assert "Progressive tool modules" in filtered_prompt
    assert "[[CYRENE_TOOL_PACK:" not in filtered_prompt


def test_disabled_package_prompt_blocks_are_removed_as_complete_sections():
    from cyrene.agent.prompts import (
        _MAIN_AGENT_PROMPT_TEMPLATE,
        _TOOL_PACK_PROMPT_TERMS,
        prompt_for_enabled_tool_packs,
    )

    enabled = set(_TOOL_PACK_PROMPT_TERMS) - {
        "delivery_tools",
        "memory_tools",
        "skill_tools",
        "entity_tools",
    }
    filtered = prompt_for_enabled_tool_packs(_MAIN_AGENT_PROMPT_TEMPLATE, enabled)

    assert "Proactive progress reporting" in filtered
    assert "## Memory" not in filtered
    assert "## Learned Skills" not in filtered
    assert "## 事务追踪" not in filtered
    assert "code_tools" in filtered
    assert "browser_tools" in filtered
    assert "delivery_tools" not in filtered
    assert "memory_tools" not in filtered
    assert "skill_tools" not in filtered
    assert "entity_tools" not in filtered
    assert "[[CYRENE" not in filtered


def test_catalog_snapshot_freezes_whole_package_switch(monkeypatch):
    from cyrene.tooling import snapshot

    monkeypatch.setattr(
        snapshot,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name != "knowledge_tools",
    )
    frozen = snapshot.build_catalog_snapshot("main")

    assert "knowledge.search" in frozen.capabilities
    assert "knowledge.search" not in frozen.enabled_capability_ids
    assert "browser.navigate" in frozen.enabled_capability_ids
    assert "AnalyzeAttachment" in frozen.enabled_capability_ids


@pytest.mark.asyncio
async def test_gateway_discover_describe_invoke_routes_to_concrete_handler(monkeypatch):
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import executor as tool_executor

    concrete = AsyncMock(return_value="matched passage")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    discovered = json.loads(await execute_wire_tool(
        "knowledge_tools",
        {"operation": "discover", "query": "search"},
        None, 0, "", None,
    ))
    assert discovered["status"] == "success"
    assert any(
        item["id"] == "knowledge.search"
        for item in discovered["capabilities"]
    )

    described = json.loads(await execute_wire_tool(
        "knowledge_tools",
        {"operation": "describe", "capability_ids": ["knowledge.search"]},
        None, 0, "", None,
    ))
    assert described["capabilities"][0]["id"] == "knowledge.search"
    assert "input_schema" in described["capabilities"][0]

    invoked = json.loads(await execute_wire_tool(
        "knowledge_tools",
        {
            "operation": "invoke",
            "capability_id": "knowledge.search",
            "arguments": {"query": "cache"},
        },
        None, 0, "db.sqlite3", None,
    ))
    assert invoked == {
        "status": "success",
        "capability_id": "knowledge.search",
        "result": "matched passage",
    }
    concrete.assert_awaited_once_with(
        "SearchKnowledge", {"query": "cache"}, None, 0, "db.sqlite3", None,
    )


@pytest.mark.parametrize("actor", ["main", "subagent"])
def test_every_deferred_capability_id_is_a_hidden_compatibility_alias(actor):
    from cyrene.tooling import gateway
    from cyrene.tooling.snapshot import build_catalog_snapshot

    frozen = build_catalog_snapshot(actor)
    checked = set()
    for capability_id in sorted(frozen.enabled_capability_ids):
        spec = frozen.capabilities[capability_id]
        wire_name = gateway._wire_name_for_pack_id(spec.pack_id)
        if not wire_name:
            continue
        arguments = gateway._schema_argument_example(spec.input_schema, {})
        resolution = gateway.resolve_wire_call(
            capability_id,
            arguments,
            actor=actor,
            catalog_snapshot=frozen,
        )
        assert resolution.wire_name == wire_name
        assert resolution.operation == "invoke"
        assert resolution.capability_id == capability_id
        assert resolution.concrete_name == spec.concrete_name
        assert resolution.concrete_arguments == arguments
        assert resolution.concrete_compat is True
        checked.add(capability_id)

    assert "memory.recall" in checked
    assert "knowledge.search" in checked
    if actor == "main":
        assert "browser.navigate" in checked


@pytest.mark.asyncio
async def test_direct_capability_id_call_executes_without_becoming_a_wire_definition(
    monkeypatch,
):
    from cyrene.tooling import execute_wire_tool, get_main_wire_tool_defs
    from cyrene.tooling import executor as tool_executor

    assert "memory.recall" not in _names(get_main_wire_tool_defs())
    concrete = AsyncMock(return_value="recent memory")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    invoked = json.loads(await execute_wire_tool(
        "memory.recall",
        {"query": "communication preference"},
        None, 0, "", None,
    ))

    assert invoked == {
        "status": "success",
        "capability_id": "memory.recall",
        "result": "recent memory",
    }
    concrete.assert_awaited_once_with(
        "RecallMemory",
        {"query": "communication preference"},
        None, 0, "", None,
    )


@pytest.mark.asyncio
async def test_direct_capability_id_compatibility_keeps_schema_and_pack_guards(
    monkeypatch,
):
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import gateway

    invalid = json.loads(await execute_wire_tool(
        "knowledge.search",
        {},
        None, 0, "", None,
    ))
    assert invalid["status"] == "error"
    assert invalid["error"]["type"] == "invalid_arguments"

    monkeypatch.setattr(
        gateway,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name != "memory_tools",
    )
    disabled = json.loads(await execute_wire_tool(
        "memory.recall",
        {},
        None, 0, "", None,
    ))
    assert disabled["status"] == "error"
    assert disabled["error"]["type"] == "permission_denied"


@pytest.mark.asyncio
async def test_gateway_discover_explains_ids_and_shows_gateway_call():
    from cyrene.tooling import execute_wire_tool

    discovered = json.loads(await execute_wire_tool(
        "memory_tools",
        {"operation": "discover", "query": "recent memory"},
        None, 0, "", None,
    ))

    assert discovered["status"] == "success"
    assert "not model-visible function names" in discovered["important"]
    assert "Never emit a function call named" in discovered["important"]
    assert "`memory_tools`" in discovered["next"]
    assert discovered["example_describe"]["tool"] == "memory_tools"
    assert discovered["example_describe"]["arguments"]["operation"] == "describe"
    assert discovered["example_describe"]["arguments"]["capability_ids"][0].startswith(
        "memory."
    )


@pytest.mark.asyncio
async def test_gateway_discovery_ranks_verbose_browser_intent_without_empty_result():
    from cyrene.tooling import execute_wire_tool

    discovered = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "discover",
            "query": "in-app browser open navigate Bilibili bilibili.com",
        },
        None, 0, "", None,
    ))

    assert discovered["status"] == "success"
    assert discovered["capabilities"]
    assert discovered["capabilities"][0]["id"] == "browser.navigate"


@pytest.mark.asyncio
async def test_gateway_discovery_falls_back_to_pack_catalog_for_unknown_terms():
    from cyrene.tooling import execute_wire_tool

    discovered = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "discover",
            "query": "B站",
            "limit": 3,
        },
        None, 0, "", None,
    ))

    assert len(discovered["capabilities"]) == 3
    assert discovered["capabilities"][0]["id"] == "browser.navigate"


@pytest.mark.asyncio
async def test_gateway_repairs_nested_invoke_capability_id(monkeypatch):
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import executor as tool_executor

    concrete = AsyncMock(return_value="opened")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    invoked = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "invoke",
            "arguments": {
                "capability_id": "browser.navigate",
                "url": "https://www.bilibili.com",
                "reason": "starting_page",
            },
        },
        None, 0, "", None,
    ))

    assert invoked["status"] == "success"
    concrete.assert_awaited_once_with(
        "browser_navigate",
        {
            "url": "https://www.bilibili.com",
            "reason": "starting_page",
        },
        None, 0, "", None,
    )


@pytest.mark.asyncio
async def test_gateway_repairs_double_nested_invoke_arguments(monkeypatch):
    """Regression for local models that wrap the whole invoke envelope."""
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import executor as tool_executor

    concrete = AsyncMock(return_value="opened")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    invoked = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "invoke",
            "arguments": {
                "capability_id": "browser.navigate",
                "arguments": {
                    "url": "https://www.bilibili.com",
                    "reason": "starting_page",
                },
            },
        },
        None, 0, "", None,
    ))

    assert invoked["status"] == "success"
    concrete.assert_awaited_once_with(
        "browser_navigate",
        {
            "url": "https://www.bilibili.com",
            "reason": "starting_page",
        },
        None, 0, "", None,
    )


@pytest.mark.asyncio
async def test_gateway_repairs_fully_nested_invoke_envelope(monkeypatch):
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import executor as tool_executor

    concrete = AsyncMock(return_value="opened")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    invoked = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "arguments": {
                "operation": "invoke",
                "capability_id": "browser.navigate",
                "arguments": {
                    "url": "https://www.bilibili.com",
                    "reason": "starting_page",
                },
            },
        },
        None, 0, "", None,
    ))

    assert invoked["status"] == "success"
    concrete.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_projects_required_arguments_across_wrapper_levels(monkeypatch):
    from cyrene.tooling import execute_wire_tool
    from cyrene.tooling import executor as tool_executor

    concrete = AsyncMock(return_value="opened")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)

    invoked = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "invoke",
            "capability_id": "browser.navigate",
            "arguments": {
                "url": "https://www.bilibili.com",
                "arguments": {
                    "reason": "starting_page",
                },
            },
        },
        None, 0, "", None,
    ))

    assert invoked["status"] == "success"
    concrete.assert_awaited_once_with(
        "browser_navigate",
        {
            "url": "https://www.bilibili.com",
            "reason": "starting_page",
        },
        None, 0, "", None,
    )


@pytest.mark.asyncio
async def test_gateway_repairs_nested_describe_capability_ids():
    from cyrene.tooling import execute_wire_tool

    described = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "describe",
            "arguments": {"capability_ids": ["browser.navigate"]},
        },
        None, 0, "", None,
    ))

    assert described["status"] == "success"
    assert described["capabilities"][0]["id"] == "browser.navigate"


@pytest.mark.asyncio
async def test_gateway_invalid_arguments_include_expected_call():
    from cyrene.tooling import execute_wire_tool

    result = json.loads(await execute_wire_tool(
        "browser_tools",
        {"operation": "invoke", "query": "https://www.bilibili.com"},
        None, 0, "", None,
    ))

    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_arguments"
    assert result["error"]["expected_call"] == {
        "tool": "browser_tools",
        "arguments": {
            "operation": "invoke",
            "capability_id": "<capability_id>",
            "arguments": {},
        },
    }


@pytest.mark.asyncio
async def test_gateway_expected_call_is_rebuilt_from_capability_schema():
    from cyrene.tooling import execute_wire_tool

    result = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "invoke",
            "capability_id": "browser.navigate",
            "arguments": {
                "arguments": {
                    "reason": "starting_page",
                },
            },
        },
        None, 0, "", None,
    ))

    assert result["status"] == "error"
    assert result["error"]["type"] == "invalid_arguments"
    assert result["error"]["expected_call"] == {
        "tool": "browser_tools",
        "arguments": {
            "operation": "invoke",
            "capability_id": "browser.navigate",
            "arguments": {
                "url": "<url>",
                "reason": "starting_page",
            },
        },
    }


@pytest.mark.asyncio
async def test_gateway_expected_call_replaces_invalid_values_from_schema():
    from cyrene.tooling import execute_wire_tool

    result = json.loads(await execute_wire_tool(
        "browser_tools",
        {
            "operation": "invoke",
            "capability_id": "browser.navigate",
            "arguments": {
                "url": 123,
                "reason": "not_a_reason",
            },
        },
        None, 0, "", None,
    ))

    assert result["error"]["expected_call"]["arguments"]["arguments"] == {
        "url": "<url>",
        "reason": "starting_page",
    }


@pytest.mark.asyncio
async def test_execute_capability_routes_through_explicit_catalog_snapshot(monkeypatch):
    from cyrene.tooling import build_catalog_snapshot, execute_capability
    from cyrene.tooling import executor as tool_executor
    from cyrene.tooling.types import ToolExecutionContext

    concrete = AsyncMock(return_value="snapshot result")
    monkeypatch.setattr(tool_executor, "_execute_tool", concrete)
    context = ToolExecutionContext(
        actor="main",
        db_path="db.sqlite3",
        catalog_snapshot=build_catalog_snapshot("main"),
    )

    result = json.loads(await execute_capability(
        "knowledge.search",
        {"query": "snapshot"},
        context,
    ))

    assert result == {
        "status": "success",
        "capability_id": "knowledge.search",
        "result": "snapshot result",
    }
    concrete.assert_awaited_once_with(
        "SearchKnowledge",
        {"query": "snapshot"},
        None,
        0,
        "db.sqlite3",
        None,
    )


@pytest.mark.asyncio
async def test_subagent_actor_cannot_discover_or_invoke_spawn():
    from cyrene.tooling import discover_capabilities, execute_wire_tool

    ids = {
        item["id"]
        for item in discover_capabilities(
            "subagent_tools", actor="subagent", limit=50,
        )
    }
    assert "subagent.spawn" not in ids
    assert {"subagent.send_message", "subagent.broadcast"} <= ids

    result = json.loads(await execute_wire_tool(
        "subagent_tools",
        {
            "operation": "invoke",
            "capability_id": "subagent.spawn",
            "arguments": {"task": "not allowed"},
        },
        None, 0, "", None, actor="subagent",
    ))
    assert result["status"] == "error"
    assert result["error"]["type"] == "unknown_capability"


def test_subagent_spawn_metadata_allows_independent_batch_launches():
    from cyrene.tooling import get_wire_tool_execution_metadata

    first = get_wire_tool_execution_metadata(
        "subagent_tools",
        {
            "operation": "invoke",
            "capability_id": "subagent.spawn",
            "arguments": {"agent_id": "track_one", "task": "track one"},
        },
    )
    second = get_wire_tool_execution_metadata(
        "subagent_tools",
        {
            "operation": "invoke",
            "capability_id": "subagent.spawn",
            "arguments": {"agent_id": "track_two", "task": "track two"},
        },
    )
    assert first["requires_order"] is False
    assert second["requires_order"] is False
    assert first["resource_keys"] != second["resource_keys"]


def test_prompts_use_new_module_names_and_keep_deep_research_specialized():
    from cyrene.agent.prompts import (
        _DEEP_RESEARCH_PROMPT,
        _EXECUTION_SYSTEM_PROMPT,
        _MAIN_AGENT_PROMPT,
    )

    combined = _MAIN_AGENT_PROMPT + _EXECUTION_SYSTEM_PROMPT
    for module_name in (
        "code_tools",
        "browser_tools",
        "desktop_tools",
        "memory_tools",
        "knowledge_tools",
        "task_tools",
        "entity_tools",
        "map_tools",
        "subagent_tools",
        "delivery_tools",
        "skill_tools",
        "remote_tools",
        "integration_tools",
    ):
        assert module_name in combined
    assert "AnalyzeAttachment" in combined
    assert "always direct" in combined
    assert "Capability IDs are not callable function names" in combined
    assert "research_tools" not in combined
    assert "work_tools" not in combined
    assert "collaboration_tools" not in combined
    assert "subagent.spawn" in _DEEP_RESEARCH_PROMPT


def test_final_tooling_layout_has_no_legacy_monoliths():
    root = Path(__file__).resolve().parents[1] / "src" / "cyrene"
    for relative in (
        "tooling/types.py",
        "tooling/catalog.py",
        "tooling/snapshot.py",
        "tooling/wire.py",
        "tooling/packs.py",
        "tooling/gateway.py",
        "tooling/executor.py",
        "tooling/validation.py",
        "tooling/results.py",
        "tooling/observability.py",
        "tooling/policy/engine.py",
        "tooling/adapters/mcp.py",
        "tooling/adapters/learned_skills.py",
    ):
        assert (root / relative).is_file(), relative
    for domain in (
        "control",
        "core",
        "code",
        "browser",
        "desktop",
        "memory",
        "knowledge",
        "task",
        "entity",
        "map",
        "subagent",
        "delivery",
        "skills",
    ):
        assert (root / "tool_impl" / domain / "__init__.py").is_file(), domain
    for removed in (
        "tool_legacy.py",
        "tool_executor.py",
        "registry_tools.py",
        "map_pin_tool.py",
        "agent/tools",
        "code_tools",
    ):
        assert not (root / removed).exists(), removed
    facade = (root / "tools.py").read_text(encoding="utf-8")
    assert "TOOL_HANDLERS" not in facade
    assert "tool_legacy" not in facade
    assert "ModuleType" not in facade


@pytest.mark.asyncio
async def test_normal_phase1_and_phase2_receive_identical_wire_defs(monkeypatch):
    from cyrene.agent import agent as agent_module
    from cyrene.tooling import wire

    calls = []
    system_prompts = []
    responses = iter([
        {
            "content": "",
            "tool_calls": [{
                "id": "phase1",
                "function": {
                    "name": "use_tools",
                    "arguments": json.dumps({"task": "inspect"}),
                },
            }],
        },
        {
            "content": "Wire definitions match across phases.",
            "tool_calls": [{
                "id": "done",
                "function": {
                    "name": "quit",
                    "arguments": "{}",
                },
            }],
        },
    ])

    async def fake_llm(messages, tools=None, **kwargs):
        calls.append(json.dumps(tools, sort_keys=True))
        system_prompts.append(str(messages[0].get("content") or ""))
        return next(responses)

    monkeypatch.setattr(
        wire,
        "is_tool_pack_enabled",
        lambda wire_name: wire_name != "browser_tools",
    )
    monkeypatch.setattr(agent_module, "_call_llm", fake_llm)
    monkeypatch.setattr(agent_module, "_save_session_messages", AsyncMock())
    assert await agent_module._run_main_agent(
        "inspect", [], None, 0, "db.sqlite3",
    ) == "Wire definitions match across phases."
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert "browser_tools" not in calls[0]
    assert "browser_tools" not in system_prompts[0]
    assert "browser.navigate" not in system_prompts[0]
    assert "code_tools" in calls[0]


def test_deep_research_length_handshake_remains_cache_exception():
    from cyrene.agent.state import _DEEP_RESEARCH_LIGHT_TOOL_DEFS
    from cyrene.tooling import get_main_wire_tool_defs

    assert _names(_DEEP_RESEARCH_LIGHT_TOOL_DEFS) == ["ask_user", "quit"]
    assert _names(_DEEP_RESEARCH_LIGHT_TOOL_DEFS) != _names(
        get_main_wire_tool_defs()
    )


def test_snapshot_keeps_loop_handled_direct_control_tools(monkeypatch):
    from cyrene.tooling import snapshot as snapshot_module

    monkeypatch.delitem(
        snapshot_module.TOOL_HANDLERS,
        "quit",
        raising=False,
    )
    snapshot = snapshot_module.build_catalog_snapshot("main")

    assert "quit" in snapshot.capabilities
    assert "quit" in snapshot.enabled_capability_ids
    assert snapshot.capabilities["quit"].handler is None


@pytest.mark.asyncio
async def test_active_catalog_snapshot_freezes_dynamic_integrations(monkeypatch):
    from cyrene.tooling import catalog, snapshot
    from cyrene.tooling.gateway import (
        activate_catalog_snapshot,
        execute_wire_tool,
        reset_catalog_snapshot,
    )

    def integration_def(name):
        return {
            name: {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"{name} integration",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        }

    monkeypatch.setattr(catalog, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(snapshot, "is_tool_pack_enabled", lambda _name: True)
    monkeypatch.setattr(
        catalog,
        "_mcp_definitions",
        lambda: integration_def("first_connector_tool"),
    )
    token = activate_catalog_snapshot("main")
    try:
        monkeypatch.setattr(
            catalog,
            "_mcp_definitions",
            lambda: integration_def("late_connector_tool"),
        )
        payload = json.loads(await execute_wire_tool(
            "integration_tools",
            {"operation": "discover"},
            None,
            0,
            "",
            None,
        ))
    finally:
        reset_catalog_snapshot(token)

    assert [item["id"] for item in payload["capabilities"]] == [
        "integration.first_connector_tool"
    ]


def test_phase_telemetry_can_distinguish_identical_phase_tool_arrays():
    from cyrene.agent import state
    from cyrene.tooling import get_main_wire_tool_defs

    assert state._llm_phase_name(get_main_wire_tool_defs()) == "phase2"
    token = state._llm_phase_override.set("phase1")
    try:
        assert state._llm_phase_name(get_main_wire_tool_defs()) == "phase1"
    finally:
        state._llm_phase_override.reset(token)


@pytest.mark.asyncio
async def test_streaming_phase_telemetry_uses_actual_tool_array(monkeypatch):
    from cyrene.agent import state
    from cyrene import call_llm
    from cyrene.tooling import get_main_wire_tool_defs

    seen = {}

    async def fake_unified_call(_messages, **kwargs):
        seen.update(kwargs)
        return {"content": "ok"}

    monkeypatch.setattr(call_llm, "call_llm", fake_unified_call)
    tools = get_main_wire_tool_defs()

    await state._call_llm_stream(
        [{"role": "user", "content": "finish"}],
        tools=tools,
    )

    assert seen["tools"] is tools
    assert seen["phase"] == "phase2"


@pytest.mark.asyncio
async def test_internal_execution_agent_uses_run_fixed_snapshot(monkeypatch):
    from cyrene.agent import coordinator
    from cyrene.tooling.gateway import _active_catalog_snapshot

    async def fake_llm(_messages, tools=None, **_kwargs):
        snapshot = _active_catalog_snapshot.get()
        assert snapshot is not None
        assert snapshot.actor == "main"
        assert _names(tools) == _names(
            coordinator.get_main_wire_tool_defs()
        )
        return {
            "content": "Scheduled work complete.",
            "tool_calls": [{
                "id": "done",
                "function": {
                    "name": "quit",
                    "arguments": "{}",
                },
            }],
        }

    monkeypatch.setattr(coordinator, "_call_llm", fake_llm)

    await coordinator._run_execution_agent(
        "scheduled work",
        None,
        0,
        "db.sqlite3",
    )
    assert _active_catalog_snapshot.get() is None

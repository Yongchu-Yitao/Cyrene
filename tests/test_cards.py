"""Assistant card contracts exercised through the actual PluginRuntime."""

import asyncio
from copy import deepcopy
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime, PluginUnavailableError
from cyrene.core.plugin.tool_scope import PluginToolScope, ScopedPluginRegistry
from cyrene.plugins.builtin import cyrene_cards
from cyrene.plugins.builtin.cyrene_cards import TOOL_NAMES, plugin_pack
from cyrene.plugins.builtin.cyrene_cards.context import setup
from cyrene.plugins.builtin.cyrene_cards.definitions import LEGACY_TEMPLATES, TEMPLATES, definition
from cyrene.plugins.builtin.cyrene_cards.schemas import CardError, encoded
from cyrene.plugins.builtin.cyrene_cards.service import CardService
from cyrene.workbench.core_adapter.assistant_profile import personal_assistant_config
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig


def form_spec():
    return {"title": "采购选择", "blocks": [
        {"block_id": "suppliers", "type": "table", "columns": [{"key": "name", "label": "供应商"}],
         "rows": [{"row_id": "a", "name": "青禾"}, {"row_id": "b", "name": "远景"}],
         "selection": {"mode": "single", "field_id": "supplier"}},
        {"block_id": "form", "type": "form", "fields": [{"field_id": "budget", "label": "预算", "type": "number", "default": 10000}]},
    ], "input_schema": {"type": "object", "properties": {"supplier": {"type": "string", "enum": ["a", "b"]}, "budget": {"type": "number", "minimum": 1}},
                         "required": ["supplier", "budget"], "additionalProperties": False},
        "actions": [{"action_id": "prepare", "kind": "submit", "label": "生成预览", "fields": ["supplier", "budget"], "intent": "生成预览"},
                    {"action_id": "approve", "kind": "confirm", "label": "确认", "fields": ["supplier", "budget"], "intent": "确认当前采购方案"}]}


@pytest.fixture
def rig(tmp_path):
    service = CardService(tmp_path / "cards")
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    config = personal_assistant_config(ConversationConfig(session_id="assistant", workspace_dir=str(tmp_path), db_path=""))
    runtime = PluginRuntime(ScopedPluginRegistry(registry, config.tool_scope))
    task = {"active": "purchase"}
    context = PluginContext(tree_id="assistant", node_id="node", data={"personal_assistant": True},
                            services={"cards": service, "task_contexts": SimpleNamespace(read=lambda: task)})
    return SimpleNamespace(service=service, registry=registry, runtime=runtime, context=context, task=task)


async def call(rig, name, args, call_id=None):
    result = await rig.runtime.call(name, args, rig.context, call_id=call_id)
    assert result.success, result
    return result.value


async def create(rig, **kwargs):
    value = await call(rig, "card.create", {"template": "data", "spec": form_spec(), "pinned": True, **kwargs}, "create")
    assert value["ok"], value
    return value


def event(event_id, *, revision=1, input_revision=0, kind="change", values=None, action=None):
    result = {"event_id": event_id, "base_revision": revision, "base_input_revision": input_revision,
              "kind": kind, "values": values or {"supplier": "b"}}
    if action:
        result["action_id"] = action
    return result


@pytest.mark.asyncio
async def test_only_assistant_can_discover_and_execute_even_with_explicit_name(rig):
    ordinary = PluginRuntime(rig.registry)
    before = {d["function"]["name"] for d in rig.registry.tool_definitions()}
    assert not set(TOOL_NAMES) & before
    assert "cyrene_cards" not in (await ordinary.call("toolbox", {"operation": "list"})).value["packs"]
    for name in TOOL_NAMES:
        assert rig.registry.registered(name).plugin.metadata["model_visible"] == "assistant"
        assert not (await ordinary.call(name, {}, rig.context)).success
        assert not (await ordinary.call("toolbox", {"operation": "invoke", "name": name, "arguments": {}}, rig.context)).success
        with pytest.raises(PluginUnavailableError):
            rig.runtime.registry.resolve(name, agent_id="child")
        rig.registry.customize_tool(name, {"agent_exposure": "direct"})
        assert not rig.registry.plugin_accessible(name)
    explicit = ScopedPluginRegistry(rig.registry, PluginToolScope(tool_names=frozenset(TOOL_NAMES)))
    assert not explicit.plugin_accessible("card.create")
    assert not (await ordinary.call("toolbox", {"operation": "describe", "name": "cyrene_cards"})).success
    # A forged ordinary entry using an assistant-scoped registry still lacks host context.
    rejected = await rig.runtime.call("card.create", {"template": "content", "spec": {"title": "No"}},
                                      PluginContext(tree_id="ordinary", services={"cards": rig.service}))
    assert rejected.value["error"]["code"] == "assistant_only"


@pytest.mark.asyncio
async def test_real_runtime_create_update_pin_archive_and_durable_replay(rig, tmp_path):
    created = await create(rig)
    assert await create(rig) == created
    card_id = created["card_id"]
    assert created["placement"] == "pinned" and created["presentation"] == {"kind": "assistant_card", "card_id": card_id, "revision": 1, "operation": "card.create"}
    conflict = await call(rig, "card.create", {"template": "content", "spec": {"title": "Other"}}, "create")
    assert conflict["error"]["code"] == "replay_conflict"
    read = await call(rig, "card.get", {"card_id": card_id})
    assert read["origin"]["session_id"] == "assistant" and read["origin"]["context_id"] == "purchase"
    assert read["input_state"] == {"draft": {}, "submitted": None}  # Defaults aren't user inputs.
    updated = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"title": "Revised"}}, "edit")
    assert updated["revision"] == 2 and updated["placement"] == "pinned"
    assert (await call(rig, "card.get", {"card_id": card_id}))["spec"]["blocks"] == form_spec()["blocks"]
    stale = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"title": "Stale"}})
    assert stale["error"]["code"] == "revision_conflict"
    unpinned = await call(rig, "card.pin", {"card_id": card_id, "pinned": False, "expected_placement_revision": 1})
    assert unpinned["placement"] == "conversation" and unpinned["revision"] == 2
    archived = await call(rig, "card.archive", {"card_id": card_id, "expected_revision": 2})
    assert archived["status"] == "archived"
    assert (await call(rig, "card.update", {"card_id": card_id, "expected_revision": 3, "changes": {"title": "No"}}))["error"]["code"] == "card_archived"
    reopened = CardService(tmp_path / "cards")
    stored = await reopened.command("assistant", "purchase", "card.get", {"card_id": card_id})
    assert stored["status"] == "archived" and stored["spec"]["title"] == "Revised"
    assert await reopened.command("assistant", "purchase", "card.create", {"template": "data", "spec": form_spec(), "pinned": True}, effect="node:create") == {k: v for k, v in created.items() if k != "ok"}


@pytest.mark.asyncio
async def test_versions_ownership_context_and_user_pin_override(rig):
    created = await create(rig)
    card_id = created["card_id"]
    with pytest.raises(CardError, match="scope"):
        await rig.service.command("other", "purchase", "card.get", {"card_id": card_id})
    rig.task["active"] = "travel"
    assert not (await call(rig, "card.list", {}))["cards"]
    assert (await call(rig, "card.list", {"scope": "pinned"}))["cards"][0]["card_id"] == card_id
    result = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"title": "No"}})
    assert result["error"]["code"] == "source_context_mismatch"
    await rig.service.user_pin("assistant", card_id, False, 1)
    result = await call(rig, "card.pin", {"card_id": card_id, "pinned": True, "expected_placement_revision": 2})
    assert result["error"]["code"] == "user_placement_override"
    await rig.service.user_pin("assistant", card_id, True, 2)
    assert (await call(rig, "card.get", {"card_id": card_id}))["user_unpinned"] is False
    missing = await call(rig, "card.update", {"card_id": "missing", "expected_revision": 1, "changes": {"title": "No"}})
    assert missing["error"]["code"] == "card_not_found"


@pytest.mark.asyncio
async def test_user_events_versions_snapshots_and_drafts_survive_updates(rig):
    card_id = (await create(rig))["card_id"]
    change = event("choose")
    first = await rig.service.user_event("assistant", card_id, change)
    assert first["actor"] == "user" and first["status"] == "accepted" and first["input_revision"] == 1
    assert await rig.service.user_event("assistant", card_id, change) == first
    with pytest.raises(CardError, match="conflict"):
        await rig.service.user_event("assistant", card_id, {**change, "values": {"supplier": "a"}})
    with pytest.raises(CardError, match="input_revision"):
        await rig.service.user_event("assistant", card_id, event("stale"))
    await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"subtitle": "New"}})
    read = await call(rig, "card.get", {"card_id": card_id})
    assert read["input_state"]["draft"] == {"supplier": "b"}
    schema = deepcopy(form_spec()["input_schema"])
    schema["properties"]["supplier"]["enum"] = ["a"]
    rejected = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 2, "changes": {"input_schema": schema}})
    assert rejected["error"]["code"] == "invalid_card"
    submit = event("submit", revision=2, input_revision=1, kind="submit", action="prepare", values={"supplier": "b", "budget": 10000})
    receipt = await rig.service.user_event("assistant", card_id, submit)
    assert receipt["spec_snapshot"]["subtitle"] == "New"
    with pytest.raises(CardError):
        await rig.service.user_event("assistant", card_id, event("old-approval", kind="confirm", action="approve"))
    confirmed = await rig.service.user_event("assistant", card_id, event("approve", revision=2, input_revision=2, kind="confirm", action="approve", values=submit["values"]))
    assert confirmed["input_revision"] == 3
    read = await call(rig, "card.get", {"card_id": card_id, "include_events": True, "limit": 1})
    assert len(read["events"]) == 1 and read["next_event_id"] == "choose"
    second = await call(rig, "card.get", {"card_id": card_id, "include_events": True, "after_event_id": "choose"})
    assert [x["event_id"] for x in second["events"]] == ["submit", "approve"]
    assert second["input_state"]["submitted"]["kind"] == "confirm"
    assert (await call(rig, "card.update", {"card_id": card_id, "expected_revision": 2, "changes": {"title": "Done"}, "in_response_to_event_id": "approve"}))["ok"]
    await call(rig, "card.archive", {"card_id": card_id, "expected_revision": 3})
    with pytest.raises(CardError, match="read-only"):
        await rig.service.user_event("assistant", card_id, event("after", revision=4, input_revision=3))


@pytest.mark.asyncio
async def test_invalid_actions_payload_and_unknown_rows_do_not_mutate(rig):
    card_id = (await create(rig))["card_id"]
    for payload in [event("row", values={"supplier": "missing"}),
                    event("actor") | {"actor": "user"},
                    event("target") | {"session_id": "other"},
                    event("bad-action", kind="confirm", action="prepare", values={"supplier": "a", "budget": 1}),
                    event("partial", kind="submit", action="prepare")]:
        with pytest.raises(CardError):
            await rig.service.user_event("assistant", card_id, payload)
    assert (await call(rig, "card.get", {"card_id": card_id}))["input_revision"] == 0


@pytest.mark.asyncio
async def test_atomic_replay_and_concurrent_updates_use_store_versions(rig):
    async def make():
        return await rig.service.command("assistant", "purchase", "card.create", {"template": "content", "spec": {"title": "One"}}, effect="same")
    results = await asyncio.gather(*[make() for _ in range(6)])
    assert len({x["card_id"] for x in results}) == 1
    async def edit(i):
        return await rig.service.command("assistant", "purchase", "card.update", {"card_id": results[0]["card_id"], "expected_revision": 1, "changes": {"title": str(i)}}, effect=str(i))
    edits = await asyncio.gather(edit(1), edit(2), return_exceptions=True)
    assert sum(isinstance(x, CardError) for x in edits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("template", TEMPLATES)
async def test_templates_and_inert_iframe_content(rig, template):
    spec = {"title": template}
    if template == "custom":
        spec["blocks"] = [{"block_id": "app", "type": "embed", "html": "<button>Calculate</button><script>window.sideEffect = true</script>"}]
    result = await create(rig, template=template, spec=spec)
    read = await call(rig, "card.get", {"card_id": result["card_id"]})
    assert read["spec"]["blocks"][0]["type"] == TEMPLATES[template]
    if template == "custom":
        assert read["spec"]["blocks"] == spec["blocks"]
        result = await call(rig, "card.update", {"card_id": result["card_id"], "expected_revision": 1,
                                               "changes": {"blocks": [{"block_id": "app", "type": "embed", "html": "<p>Updated</p>"}]}})
        assert result["revision"] == 2


def test_creation_exposes_only_six_general_templates():
    description, schema = definition("card.create")
    assert set(schema["properties"]["template"]["enum"]) == {"content", "collection", "data", "input", "map", "custom"}
    assert not set(LEGACY_TEMPLATES) & set(schema["properties"]["template"]["enum"])
    assert "freely mix" in description


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["content", "collection", "data", "input"])
async def test_native_templates_allow_mixed_blocks_layout_and_user_input(rig, template):
    spec = form_spec()
    spec["layout"] = "grid"
    spec["blocks"][:0] = [
        {"block_id": "intro", "type": "text", "title": "采购建议", "description": "选择后补充条件", "text": "优先比较交付与质保。"},
        {"block_id": "cost", "type": "metrics", "items": [{"label": "预算", "value": 10000, "unit": "CNY"}]},
        {"block_id": "progress", "type": "timeline", "items": [{"item_id": "review", "label": "审阅方案", "status": "pending"}]},
    ]
    created = await create(rig, template=template, spec=spec)
    card_id = created["card_id"]
    await rig.service.user_event("assistant", card_id, event("pick"))
    updated = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"layout": "stack", "density": "compact"}})
    assert updated["ok"]
    read = await call(rig, "card.get", {"card_id": card_id})
    assert read["spec"]["blocks"] == spec["blocks"]
    assert read["spec"]["layout"] == "stack" and read["spec"]["density"] == "compact"
    assert read["input_state"]["draft"] == {"supplier": "b"}
    assert read["placement"] == "pinned"


@pytest.mark.asyncio
async def test_custom_card_combines_one_iframe_with_native_interactions(rig):
    spec = form_spec()
    embed = {"block_id": "calculator", "type": "embed", "html": "<p>Calculator</p>"}
    spec["blocks"].insert(1, embed)
    card_id = (await create(rig, template="custom", spec=spec))["card_id"]
    receipt = await rig.service.user_event("assistant", card_id, event("pick"))
    assert receipt["values"] == {"supplier": "b"}
    for blocks in [form_spec()["blocks"], [embed, {**embed, "block_id": "second"}]]:
        result = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"blocks": blocks}})
        assert result["error"]["code"] == "invalid_card"
    assert (await call(rig, "card.get", {"card_id": card_id}))["spec"]["blocks"] == spec["blocks"]
    ordinary = await call(rig, "card.create", {"template": "content", "spec": {"title": "No implicit iframe", "blocks": [embed]}})
    assert ordinary["error"]["code"] == "invalid_card"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", LEGACY_TEMPLATES)
async def test_persisted_legacy_card_remains_editable_without_rewriting_history(rig, legacy):
    card_id = (await create(rig))["card_id"]
    await rig.service.user_event("assistant", card_id, event("pick"))
    stored = await rig.service.command("assistant", "purchase", "card.get", {"card_id": card_id})
    # Represent an already persisted card from the previous template catalog.
    stored["template"] = legacy
    stored["spec"].pop("layout", None)
    document = encoded(stored)
    with rig.service.store.connect(write=True) as db:
        db.execute("UPDATE cards SET document=? WHERE id=?", (document, card_id))
        db.execute("UPDATE versions SET document=? WHERE card_id=? AND revision=1", (document, card_id))
    reopened = CardService(rig.service.store.path.parent)
    result = await reopened.command("assistant", "purchase", "card.update",
                                    {"card_id": card_id, "expected_revision": 1, "changes": {"title": "更新说明"}}, effect="legacy-edit")
    assert result["template"] == legacy and result["revision"] == 2
    read = await reopened.command("assistant", "purchase", "card.get", {"card_id": card_id, "include_events": True})
    assert read["spec"]["blocks"] == stored["spec"]["blocks"]
    assert read["input_state"] == stored["input_state"] and read["placement"] == "pinned"
    assert read["events"][0]["event_id"] == "pick"
    with reopened.store.connect() as db:
        assert db.execute("SELECT document FROM versions WHERE card_id=? AND revision=1", (card_id,)).fetchone()["document"] == document
    with pytest.raises(CardError):
        await reopened.command("assistant", "purchase", "card.create", {"template": legacy, "spec": {"title": "Legacy name"}}, effect="new-legacy")


@pytest.mark.asyncio
async def test_invalid_schemas_and_oversize_card_create_nothing(rig):
    bad = [({"title": "No", "input_schema": {"$ref": "https://example.com/schema"}}, "content"),
           ({"title": "No", "blocks": [{"block_id": "a", "type": "form", "fields": [{"field_id": "missing", "label": "Missing", "type": "number"}]}]}, "input"),
           ({"title": "No"}, "custom"),
           ({"title": "No", "blocks": [{"block_id": "a", "type": "text", "text": "x" * 20001}]}, "content")]
    for i, (spec, template) in enumerate(bad):
        result = await rig.runtime.call("card.create", {"template": template, "spec": spec, "pinned": True}, rig.context, call_id=f"bad-{i}")
        assert not result.success or not result.value["ok"]
    assert not (await call(rig, "card.list", {}))["cards"]


def test_editable_copy_loads_schemas_and_scope(tmp_path):
    source = Path(cyrene_cards.__file__).parent
    shutil.copytree(source, tmp_path / "plugins" / "cyrene_cards", ignore=shutil.ignore_patterns("__pycache__"))
    registry = PluginRegistry()
    assert not registry.load_directory(tmp_path / "plugins")
    for name in TOOL_NAMES:
        assert registry.registered(name).pack_id == "cyrene_cards"
        assert not registry.plugin_accessible(name)


@pytest.mark.asyncio
async def test_hook_mounts_only_assistant_current_context_and_refreshes_inputs(rig):
    hooks = []
    context = SimpleNamespace(data={"personal_assistant": True}, agent_id="main", tree_id="assistant", services=rig.context.services,
                              tree=SimpleNamespace(get_path=lambda *a: []),
                              hooks=SimpleNamespace(list=lambda: [], register=lambda _, fn, **kw: hooks.append(fn)))
    setup(context)
    assert len(hooks) == 1
    card_id = (await create(rig))["card_id"]
    await rig.service.user_event("assistant", card_id, event("choose"))
    mounted = await hooks[0](SimpleNamespace(payload={"node_id": "node"}))
    assert '"supplier": "b"' in mounted["context"]
    assert mounted["context_lifecycle"] == "reference"
    context.tree.get_path = lambda *a: [SimpleNamespace(value={"role": "context", **mounted, "content": mounted["context"]})]
    assert await hooks[0](SimpleNamespace(payload={"node_id": "node"})) == {}
    rig.task["active"] = "travel"
    assert '"cards": []' in (await hooks[0](SimpleNamespace(payload={"node_id": "node"})))["context"]
    context.data = {"personal_assistant": False}
    setup(context)
    assert len(hooks) == 1


def test_application_service_routes_validate_ui_actor_and_keep_accepted_distinct(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_cards.application import setup_application
    from cyrene.workbench.application import app_services
    from cyrene.workbench.projects.assistant import ASSISTANT_ID
    services, router = {}, APIRouter()
    ctx = SimpleNamespace(data_directory=tmp_path, router=router, provide=lambda name, value: services.__setitem__(name, value),
                          on_startup=lambda fn: None, on_shutdown=lambda fn: None)
    monkeypatch.setattr(app_services, 'chat_application_port', lambda: SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(
        get=lambda key: {"id": key, "kind": "personal-assistant"}))))
    setup_application(ctx)
    service = services["cards"]
    created = asyncio.run(service.command(ASSISTANT_ID, "purchase", "card.create", {"template": "data", "spec": form_spec()}, effect="create"))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        path = f'/api/assistant/cards/{created["card_id"]}'
        assert client.get(path).status_code == 200
        assert client.post(path + '/events', json=event("spoof") | {"actor": "user"}).status_code == 422
        result = client.post(path + '/events', json=event("click"))
        assert result.status_code == 200 and result.json()["status"] == "accepted"
        assert client.put(path + '/pin', json={"pinned": True, "expected_placement_revision": 1}).status_code == 200
        assert client.get('/api/assistant/cards').json()["cards"][0]["card_id"] == created["card_id"]
        assert client.get('/api/assistant/cards?limit=1000').status_code == 422


def map_spec():
    return {"title": "旅行地图", "blocks": [{"block_id": "map", "type": "map", "places": [
        {"place_id": "a", "label": "西湖", "latitude": 30.24, "longitude": 120.14, "note": "**散步**"},
        {"place_id": "b", "label": "茶园", "latitude": 30.21, "longitude": 120.11}],
        "routes": [{"route_id": "walk", "from_place_id": "a", "to_place_id": "b", "transport": "步行", "note": "访问顺序"}],
        "selection": {"mode": "multiple", "field_id": "places"}}],
        "input_schema": {"type": "object", "properties": {"places": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}}, "required": ["places"], "additionalProperties": False},
        "actions": [{"action_id": "choose", "label": "选择地点", "kind": "submit", "fields": ["places"], "intent": "使用所选地点"}]}


@pytest.mark.asyncio
async def test_map_routes_selections_and_update_preserve_user_choices(rig):
    created = await create(rig, template="map", spec=map_spec(), pinned=False)
    assert created["presentation"]["operation"] == "card.create"
    card_id = created["card_id"]
    await rig.service.user_event("assistant", card_id, event("map-pick", values={"places": ["a"]}))
    with pytest.raises(CardError, match="unknown item"):
        await rig.service.user_event("assistant", card_id, event("unknown", input_revision=1, values={"places": ["missing"]}))
    receipt = await rig.service.user_event("assistant", card_id, event("map-submit", input_revision=1, kind="submit", action="choose", values={"places": ["a", "b"]}))
    assert receipt["spec_snapshot"]["blocks"][0]["routes"][0]["transport"] == "步行"
    updated = await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"title": "已选地点"}})
    assert updated["presentation"]["operation"] == "card.update"
    assert (await call(rig, "card.get", {"card_id": card_id}))["input_state"]["draft"] == {"places": ["a", "b"]}


@pytest.mark.asyncio
async def test_map_rejects_missing_route_endpoints_duplicate_places_and_wrong_binding(rig):
    for index in range(3):
        spec = map_spec()
        block = spec["blocks"][0]
        if index == 0:
            block["routes"][0]["to_place_id"] = "missing"
        elif index == 1:
            block["places"].append(block["places"][0])
        else:
            spec["input_schema"]["properties"]["places"] = {"type": "boolean"}
        result = await call(rig, "card.create", {"template": "map", "spec": spec}, f"invalid-map-{index}")
        assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,action", [("submit", "prepare"), ("confirm", "approve")])
async def test_submission_delivery_is_durable_immutable_and_retryable(rig, tmp_path, kind, action):
    import json
    from cyrene.plugins.builtin.cyrene_cards.delivery import CardDelivery
    from cyrene.workbench.control.control_services import ControlServiceError

    created = await create(rig)
    card_id = created["card_id"]
    calls = []
    fail = True

    async def dispatch(owner, message, **kwargs):
        calls.append((owner, message, kwargs))
        if fail:
            raise ControlServiceError("model unavailable", status_code=503)
        return {"status": "started"}

    port = SimpleNamespace(dispatch_user_message=dispatch)
    delivery = CardDelivery(rig.service, lambda: port)
    await rig.service.user_event("assistant", card_id, event("draft"), delivery_version='["birth", 0]')
    await delivery.tick()
    assert calls == []  # Drafts never send a turn.
    submitted = event("submitted", input_revision=1, kind=kind, action=action, values={"supplier": "b", "budget": 5000})
    await rig.service.user_event("assistant", card_id, submitted, delivery_version='["birth", 0]')
    # Model and draft edits after submission cannot change what gets delivered.
    await call(rig, "card.update", {"card_id": card_id, "expected_revision": 1, "changes": {"title": "新标题"}})
    await rig.service.user_event("assistant", card_id, event("later", revision=2, input_revision=2, values={"budget": 6000}))
    restored = CardDelivery(CardService(rig.service.store.path.parent), lambda: port)
    await restored.tick()  # Recovery after restart, without another browser request.
    assert len(calls) == 1
    read = await call(rig, "card.get", {"card_id": card_id})
    assert read["input_state"]["submitted"]["delivery"]["status"] == "failed"
    assert '采购选择' in calls[0][1] and '新标题' not in calls[0][1]
    data = json.loads(calls[0][1][calls[0][1].index('{'):])
    assert data["values"] == {"supplier": "b", "budget": 5000}
    assert data["display_values"]["supplier"][0]["name"] == "远景"
    assert data["origin"]["context_id"] == "purchase"
    assert calls[0][2]["expected_version"] == '["birth", 0]'
    fail = False
    assert (await restored.deliver("assistant", card_id, "submitted", retry=True))["status"] == "sent"
    assert calls[0] == calls[1]  # Same payload and receiver identity.
    await restored.deliver("assistant", card_id, "submitted", retry=True)
    assert len(calls) == 2
    with pytest.raises(CardError):
        await restored.deliver("other", card_id, "submitted", retry=True)
    with pytest.raises(CardError):
        await restored.deliver("assistant", "other-card", "submitted", retry=True)


def test_ui_submission_routes_dispatch_once_and_offer_delivery_retry(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_cards.application import setup_application
    from cyrene.workbench.application import app_services
    from cyrene.workbench.projects.assistant import ASSISTANT_ID
    from cyrene.workbench.control.control_services import ControlServiceError

    services, router, calls = {}, APIRouter(), []
    failure = True
    async def dispatch(owner, message, **kwargs):
        calls.append((owner, message, kwargs))
        if failure:
            raise ControlServiceError("temporarily unavailable", status_code=503)
        return {"status": "guided"}
    port = SimpleNamespace(service=SimpleNamespace(repository=SimpleNamespace(get=lambda key: {
        "id": key, "kind": "personal-assistant", "createdAt": "birth", "messageGeneration": 3})), dispatch_user_message=dispatch)
    monkeypatch.setattr(app_services, 'chat_application_port', lambda: port)
    ctx = SimpleNamespace(data_directory=tmp_path, router=router, provide=lambda k, v: services.__setitem__(k, v),
                          on_startup=lambda fn: None, on_shutdown=lambda fn: None)
    setup_application(ctx)
    created = asyncio.run(services["cards"].command(ASSISTANT_ID, "purchase", "card.create",
                                                 {"template": "input", "spec": form_spec()}, effect="create"))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        path = f'/api/assistant/cards/{created["card_id"]}'
        invalid = event("invalid", kind="submit", action="prepare")
        assert client.post(path + '/events', json=invalid).status_code == 422
        assert calls == []
        body = event("submit", kind="submit", action="prepare", values={"supplier": "a", "budget": 3000})
        first = client.post(path + '/events', json=body).json()
        assert first["status"] == "accepted" and first["delivery"]["status"] == "failed"
        assert client.post(path + '/events', json=body).json() == first
        assert len(calls) == 1
        failure = False
        assert client.post(path + '/events/submit/retry').json()["status"] == "sent"
        replay = client.post(path + '/events', json=body).json()
        assert replay["delivery"]["status"] == "sent" and len(calls) == 2
        assert client.get(path).json()["input_state"]["submitted"]["delivery"]["status"] == "sent"
        assert calls[0] == calls[1]


@pytest.mark.parametrize("template,multiple", [("map", True), ("map", False), ("data", False)])
@pytest.mark.asyncio
async def test_selection_schema_rejects_labels_instead_of_actual_ids(rig, template, multiple):
    spec = map_spec() if template == "map" else form_spec()
    key = "places" if template == "map" else "supplier"
    block = spec["blocks"][0]
    block["selection"]["mode"] = "multiple" if multiple else "single"
    spec["input_schema"]["properties"][key] = ({"type": "array", "items": {"type": "string", "enum": ["显示名称"]}}
        if multiple else {"type": "string", "enum": ["显示名称"]})
    rejected = await call(rig, "card.create", {"template": template, "spec": spec})
    assert rejected["ok"] is False
    assert "use IDs, not display labels" in rejected["error"]["message"]


@pytest.mark.asyncio
async def test_map_groups_validate_references_and_preserve_labels(rig):
    spec = map_spec()
    block = spec['blocks'][0]
    block['groups'] = [{'group_id': 'west', 'label': '西湖片区'}]
    block['places'][0]['group_id'] = 'west'
    block['routes'][0]['group_id'] = 'west'
    created = await create(rig, template='map', spec=spec)
    stored = await call(rig, 'card.get', {'card_id': created['card_id']})
    assert stored['spec']['blocks'][0]['groups'] == block['groups']
    for invalid in ('unknown', 'duplicate'):
        bad = map_spec()
        bad['blocks'][0]['groups'] = block['groups'] * (2 if invalid == 'duplicate' else 1)
        bad['blocks'][0]['places'][0]['group_id'] = 'missing' if invalid == 'unknown' else 'west'
        result = await call(rig, 'card.create', {'template': 'map', 'spec': bad}, f'bad-group-{invalid}')
        assert result['ok'] is False


@pytest.mark.asyncio
async def test_home_slots_editable_across_contexts_and_actions_preserve_target(rig):
    from cyrene.plugins.builtin.cyrene_cards.delivery import submission_message
    spec = {"title": "欢迎回来", "home_kind": "overview", "blocks": [
        {"block_id": "weather", "type": "weather", "location": "常州", "condition": "多云",
         "temperature": "18°C", "range": "14–21°C", "advice": "带外套", "source": "测试天气来源", "updated_at": "2026-11-08T19:50:00+08:00"},
        {"block_id": "pending", "type": "action_list", "title": "待你确认", "collapsed": True,
         "items": [{"item_id": "purchase", "label": "采购", "action_ids": ["continue"]}]},
    ], "actions": [{"action_id": "continue", "label": "继续进行", "kind": "submit", "fields": [],
                    "intent": "继续完成采购", "target_context_id": "purchase", "task_id": "task_123"}]}
    created = await create(rig, template="content", spec=spec)
    assert created['presentation']['home_kind'] == 'overview'
    duplicate = await call(rig, 'card.create', {'template': 'content', 'spec': spec}, 'duplicate-home')
    assert duplicate['error']['code'] == 'home_card_exists'
    rig.task['active'] = 'other-task'
    updated = await call(rig, 'card.update', {'card_id': created['card_id'], 'expected_revision': 1,
                                           'changes': {'title': '晚安'}})
    assert updated['ok']
    listed = await call(rig, 'card.list', {'scope': 'home'})
    assert [card['card_id'] for card in listed['cards']] == [created['card_id']]
    receipt = await rig.service.user_event('assistant', created['card_id'], {
        'event_id': 'continue-home', 'base_revision': 2, 'base_input_revision': 0,
        'kind': 'submit', 'action_id': 'continue', 'values': {}})
    message = submission_message(receipt)
    assert 'task_123' in message and 'purchase' in message and '继续完成采购' in message
    bad = deepcopy(spec)
    bad['blocks'][1]['items'][0]['action_ids'] = ['missing']
    rejected = await call(rig, 'card.create', {'template': 'content', 'spec': bad}, 'bad-home')
    assert rejected['error']['code'] == 'invalid_card'
    changed = await call(rig, 'card.update', {'card_id': created['card_id'], 'expected_revision': 2,
                                           'changes': {'home_kind': 'results'}}, 'change-home-kind')
    assert changed['error']['code'] == 'invalid_card'


@pytest.mark.asyncio
async def test_home_saved_visible_during_work_and_nobody_can_pin(rig):
    from cyrene.plugins.builtin.cyrene_cards.home import HomeCards
    card = await create(rig, template='content', spec={'title': '已有卡片', 'home_kind': 'resume'})
    assert card['placement'] == 'conversation'  # create helper requested pinned=True
    denied = await call(rig, 'card.pin', {'card_id': card['card_id'], 'pinned': True,
                                       'expected_placement_revision': 1}, 'auto-pin')
    assert denied['error']['code'] == 'user_placement_override'
    with pytest.raises(CardError, match='cannot be pinned'):
        await rig.service.user_pin('assistant', card['card_id'], True, 1)
    def no_agent():
        raise AssertionError('Reading saved cards must not access or wake the Agent')
    home = HomeCards(rig.service, no_agent)
    snapshot = await home.saved('assistant')
    assert snapshot['cards'][0]['spec']['title'] == '已有卡片'
    assert snapshot['cards'][0]['placement'] == 'conversation'
    await call(rig, 'card.update', {'card_id': card['card_id'], 'expected_revision': 1,
                                 'changes': {'home_visible': False}}, 'hide-slot')
    assert (await home.saved('assistant'))['cards'] == []
    assert (await call(rig, 'card.get', {'card_id': card['card_id']}))['placement'] == 'conversation'
    await call(rig, 'card.update', {'card_id': card['card_id'], 'expected_revision': 2,
                                 'changes': {'home_visible': True}}, 'show-slot')
    assert len((await home.saved('assistant'))['cards']) == 1
    # Application restart preserves the same home record.
    restored = CardService(rig.service.store.path.parent)
    assert (await restored.command('assistant', None, 'card.get', {'card_id': card['card_id']}))['placement'] == 'conversation'


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["user", "assistant"])
async def test_home_migration_removes_all_home_pins(rig, actor):
    card = await create(rig, template='content', spec={'title': '旧欢迎卡片', 'home_kind': 'overview'})
    with rig.service.store.connect(write=True) as db:
        stored = rig.service._get(db, 'assistant', card['card_id'])
        stored['placement'] = 'pinned'
        stored['placement_actor'] = actor
        rig.service._save(db, stored)
    restored = CardService(rig.service.store.path.parent)
    result = await restored.command('assistant', None, 'card.get', {'card_id': card['card_id']})
    assert result['placement'] == 'conversation'
    assert result['placement_revision'] == 2


@pytest.mark.asyncio
async def test_home_headline_edits_reuse_slot_and_cannot_archive_recreate(rig):
    created = await create(rig, template='content', spec={'title': '早上好，\n从这里接着来。',
                           'subtitle': '有两件事等你决定。', 'home_kind': 'overview'})
    for revision in range(1, 4):
        changed = await call(rig, 'card.update', {'card_id': created['card_id'], 'expected_revision': revision,
                            'changes': {'title': f'新的问候 {revision}', 'subtitle': 'Agent 更新的说明'}}, f'headline-{revision}')
        assert changed['card_id'] == created['card_id']
    rejected = await call(rig, 'card.archive', {'card_id': created['card_id'], 'expected_revision': 4}, 'archive-home')
    assert rejected['error']['code'] == 'invalid_card'
    listed = await call(rig, 'card.list', {'scope': 'home'})
    assert len(listed['cards']) == 1
    assert listed['cards'][0]['title'] == '新的问候 3'

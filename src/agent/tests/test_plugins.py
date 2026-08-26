from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx

from agent.context import ContextStoreRouter
from agent.hook import POST_TOOL_USE
from agent.plugin import (
    Plugin,
    PluginBatchRunner,
    PluginCall,
    PluginContext,
    PluginPack,
    PluginRegistry,
    PluginRuntime,
)
from agent.plugin.core_impl import PermissionReviewPlugin


def run(coroutine):
    return asyncio.run(coroutine)


def test_registry_includes_core_and_loads_user_tool_pack(tmp_path):
    root = tmp_path / "plugin_impl"
    package = root / "search"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        """
from agent.plugin import Plugin, PluginPack

def search(arguments, context):
    return {"query": arguments["query"], "tree_id": context.tree_id}

plugin_pack = PluginPack(
    id="search",
    description="Search tools",
    plugins=(Plugin(
        name="Search",
        description="Search for text",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search,
    ),),
)
""",
        encoding="utf-8",
    )
    registry = PluginRegistry()

    assert registry.load_directory(root) == ()
    assert [pack.id for pack in registry.list_packs()] == ["core", "search"]
    assert registry.registered("Search").pack_id == "search"
    assert registry.registered("Search").source == str(package)
    assert {item.plugin.name for item in registry.list_plugins()} >= {
        "Bash",
        "Read",
        "Search",
        "Write",
    }

    result = run(
        PluginRuntime(registry).call(
            "Search",
            {"query": "Cyrene"},
            PluginContext(tree_id="tree"),
        )
    )
    assert result.success is True
    assert result.value == {"query": "Cyrene", "tree_id": "tree"}


def test_bad_user_pack_isolated_as_load_failure(tmp_path):
    package = tmp_path / "plugin_impl" / "broken"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("plugin_pack = None\n", encoding="utf-8")
    registry = PluginRegistry(include_core=False)

    failures = registry.load_directory(package.parent)

    assert len(failures) == 1
    assert failures[0].path == package
    assert "must export PluginPack" in failures[0].error
    assert registry.list_packs() == ()


def test_registry_registers_standalone_plugins_without_a_pack():
    registry = PluginRegistry(include_core=False)
    first = Plugin(
        "Standalone",
        "first",
        {"type": "object"},
        lambda _arguments, _context: "first",
    )
    second = Plugin(
        "Standalone",
        "second",
        {"type": "object"},
        lambda _arguments, _context: "second",
    )

    registry.register_plugin(first, source="standalone-test")
    registered = registry.registered("Standalone")
    assert registered.plugin is first
    assert registered.pack_id is None
    registry.register_plugin(second, source="standalone-test", replace=True)
    assert registry.resolve("Standalone") is second
    assert registry.unregister_plugin("Standalone") is True
    assert registry.unregister_plugin("Standalone") is False


def test_toolbox_lists_and_refreshes_standalone_plugin_files(tmp_path):
    async def scenario():
        root = tmp_path / "plugin_impl"
        root.mkdir()
        standalone = root / "solo.py"

        def write_plugin(version: int, name: str = "StandaloneTool") -> None:
            suffix_property = (
                ', "suffix": {"type": "string"}' if version >= 2 else ""
            )
            standalone.write_text(
                f'''\
from agent.plugin import Plugin

def handle(arguments, _context):
    return "standalone-{version}:" + arguments["value"] + arguments.get("suffix", "")

plugin = Plugin(
    name="{name}",
    description="Standalone tool version {version}",
    input_schema={{
        "type": "object",
        "properties": {{"value": {{"type": "string"}}{suffix_property}}},
        "required": ["value"],
        "additionalProperties": False,
    }},
    handler=handle,
)
''',
                encoding="utf-8",
            )

        registry = PluginRegistry()
        assert registry.load_directory(root) == ()
        registry.register_pack(
            PluginPack(
                "existing",
                "An existing tool pack",
                (
                    Plugin(
                        "PackedTool",
                        "A packed tool",
                        {"type": "object"},
                        lambda _arguments, _context: "packed",
                    ),
                ),
            ),
            source="test-pack",
        )
        runtime = PluginRuntime(registry)

        write_plugin(1)
        listing = await runtime.call("toolbox", {"operation": "list"})
        assert listing.success is True
        assert listing.value["packs"] == [
            {
                "id": "existing",
                "description": "An existing tool pack",
                "tools": [
                    {"name": "PackedTool", "description": "A packed tool"}
                ],
            }
        ]
        assert listing.value["standalone_tools"] == [
            {
                "name": "StandaloneTool",
                "description": "Standalone tool version 1",
            }
        ]
        registered = registry.registered("StandaloneTool")
        assert registered.pack_id is None
        assert registered.source == str(standalone)

        first = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "StandaloneTool",
                "arguments": {"value": "one"},
            },
        )
        assert first.success is True
        assert first.value["result"] == "standalone-1:one"

        write_plugin(2)
        description = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "StandaloneTool"},
        )
        assert description.success is True
        current = description.value["plugins"][0]
        assert current["pack"] is None
        assert current["description"] == "Standalone tool version 2"
        assert set(current["input_schema"]["properties"]) == {"value", "suffix"}

        second = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "StandaloneTool",
                "arguments": {"value": "two", "suffix": "!"},
            },
        )
        assert second.success is True
        assert second.value["result"] == "standalone-2:two!"

        write_plugin(3, "RenamedTool")
        renamed = await runtime.call("toolbox", {"operation": "list"})
        assert renamed.success is True
        assert renamed.value["standalone_tools"] == [
            {
                "name": "RenamedTool",
                "description": "Standalone tool version 3",
            }
        ]
        assert "StandaloneTool" not in {
            item.plugin.name for item in registry.list_plugins()
        }
        renamed_call = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "RenamedTool",
                "arguments": {"value": "three", "suffix": "!"},
            },
        )
        assert renamed_call.success is True
        assert renamed_call.value["result"] == "standalone-3:three!"

        standalone.write_text("plugin = None\n", encoding="utf-8")
        failed_update = await runtime.call("toolbox", {"operation": "list"})
        assert failed_update.success is True
        assert failed_update.value["standalone_tools"][0]["name"] == "RenamedTool"
        assert failed_update.value["refresh_errors"][0]["path"] == str(standalone)

        standalone.unlink()
        removed = await runtime.call("toolbox", {"operation": "list"})
        assert removed.success is True
        assert removed.value["standalone_tools"] == []
        assert [pack["id"] for pack in removed.value["packs"]] == ["existing"]
        unavailable = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "RenamedTool",
                "arguments": {"value": "three"},
            },
        )
        assert unavailable.success is False
        assert "Plugin is not registered: RenamedTool" in unavailable.error

        old_search = await runtime.call("toolbox", {"operation": "search"})
        assert old_search.success is False
        assert "is not one of ['list', 'describe', 'invoke']" in old_search.error

    run(scenario())


def test_runtime_validates_the_resolved_plugins_current_schema():
    async def scenario():
        calls = []

        def first(arguments, _context):
            calls.append(("first", dict(arguments)))
            return arguments["value"]

        first_schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "live",
                "live",
                (Plugin("Live", "live", first_schema, first),),
            ),
            source="test",
        )
        runtime = PluginRuntime(registry)

        missing = await runtime.call("Live", {})
        wrong_type = await runtime.call("Live", {"value": 1})
        unknown = await runtime.call("Live", {"value": "ok", "extra": True})
        valid = await runtime.call("Live", {"value": "ok"})

        assert missing.success is False
        assert "'value' is a required property" in missing.error
        assert wrong_type.success is False
        assert "is not of type 'string'" in wrong_type.error
        assert unknown.success is False
        assert "Additional properties are not allowed" in unknown.error
        assert valid.success is True
        assert valid.value == "ok"

        def second(arguments, _context):
            calls.append(("second", dict(arguments)))
            return arguments["value"] + arguments.get("suffix", "")

        current_schema = {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "suffix": {"type": "string"},
            },
            "required": ["value"],
            "additionalProperties": False,
        }
        registry.register_pack(
            PluginPack(
                "live",
                "live replacement",
                (Plugin("Live", "live replacement", current_schema, second),),
            ),
            source="test",
            replace=True,
        )

        compatible = await runtime.call("Live", {"value": "new", "suffix": "!"})
        assert compatible.success is True
        assert compatible.value == "new!"
        assert calls == [
            ("first", {"value": "ok"}),
            ("second", {"value": "new", "suffix": "!"}),
        ]

    run(scenario())


def test_runtime_revalidates_arguments_modified_by_hooks():
    async def scenario():
        executed = False

        class InvalidatingHooks:
            async def pre_tool_use_batch(self, _calls):
                return ({"value": "ok", "unexpected": True},)

        def handler(_arguments, _context):
            nonlocal executed
            executed = True

        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "test",
                "test",
                (
                    Plugin(
                        "Strict",
                        "strict",
                        {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                        handler,
                    ),
                ),
            ),
            source="test",
        )

        result = await PluginRuntime(registry).call(
            "Strict",
            {"value": "ok"},
            PluginContext(hooks=InvalidatingHooks()),
        )

        assert result.success is False
        assert "Additional properties are not allowed" in result.error
        assert executed is False

    run(scenario())


def test_toolbox_reads_live_registry_and_refreshes_added_changed_and_deleted_packs(
    tmp_path,
):
    async def scenario():
        root = tmp_path / "plugin_impl"
        package = root / "live"
        package.mkdir(parents=True)
        initializer = package / "__init__.py"

        def write_pack(version: int) -> None:
            suffix_property = (
                ', "suffix": {"type": "string"}' if version == 2 else ""
            )
            initializer.write_text(
                f'''\
from agent.plugin import Plugin, PluginPack

def live(arguments, _context):
    return "version-{version}:" + arguments["value"] + arguments.get("suffix", "")

plugin_pack = PluginPack(
    id="live",
    description="Live reloadable tools version {version}",
    plugins=(Plugin(
        name="LiveTool",
        description="Live tool version {version}",
        input_schema={{
            "type": "object",
            "properties": {{"value": {{"type": "string"}}{suffix_property}}},
            "required": ["value"],
            "additionalProperties": False,
        }},
        handler=live,
    ),),
)
''',
                encoding="utf-8",
            )

        write_pack(1)
        registry = PluginRegistry()
        assert registry.load_directory(root) == ()
        runtime = PluginRuntime(registry)

        direct_names = {
            definition["function"]["name"]
            for definition in registry.direct_tool_definitions()
        }
        assert direct_names == {"Bash", "Read", "Write", "toolbox"}

        listing = await runtime.call("toolbox", {"operation": "list"})
        assert listing.success is True
        assert listing.value["standalone_tools"] == []
        assert listing.value["packs"] == [
            {
                "id": "live",
                "description": "Live reloadable tools version 1",
                "tools": [
                    {"name": "LiveTool", "description": "Live tool version 1"}
                ],
            }
        ]

        first = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "LiveTool",
                "arguments": {"value": "one"},
            },
        )
        assert first.success is True
        assert first.value["result"] == "version-1:one"

        write_pack(2)
        description = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "LiveTool"},
        )
        assert description.success is True
        current = description.value["plugins"][0]
        assert current["description"] == "Live tool version 2"
        assert set(current["input_schema"]["properties"]) == {"value", "suffix"}

        second = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "LiveTool",
                "arguments": {"value": "two", "suffix": "!"},
            },
        )
        assert second.success is True
        assert second.value["result"] == "version-2:two!"

        shutil.rmtree(package)
        removed = await runtime.call("toolbox", {"operation": "list"})
        assert removed.success is True
        assert removed.value["packs"] == []
        assert removed.value["standalone_tools"] == []
        unavailable = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "LiveTool",
                "arguments": {"value": "three"},
            },
        )
        assert unavailable.success is False
        assert "Plugin is not registered: LiveTool" in unavailable.error

    run(scenario())


def test_runtime_uses_tree_permission_and_post_tool_hooks(tmp_path):
    async def scenario():
        reviewed = []
        executed = []
        posted = []

        async def permission_model(_system, request):
            reviewed.append(request)
            allowed = request["tool"]["arguments"].get("value") != "blocked"
            return {"approve": allowed, "rationale": "allowed" if allowed else "denied"}

        reviewer = PermissionReviewPlugin(
            permission_model,
            user_request=lambda _event: "Run the echo tool",
        )
        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree(
            tree_id="tree",
            root_id="root",
            initial_hooks=(reviewer.registration(),),
        )
        hooks = store.hooks_for(tree.id)
        hooks.register(POST_TOOL_USE, lambda event: posted.append(event.payload))

        async def echo(arguments, _context):
            executed.append(arguments["value"])
            return arguments["value"]

        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "test",
                "test pack",
                (
                    Plugin(
                        "Echo",
                        "Echo a value",
                        {"type": "object"},
                        echo,
                    ),
                ),
            ),
            source="test",
        )
        runtime = PluginRuntime(registry)
        context = PluginContext(
            tree=store,
            tree_id=tree.id,
            node_id=tree.root_id,
            hooks=hooks,
        )

        allowed = await runtime.call("Echo", {"value": "allowed"}, context)
        blocked = await runtime.call("Echo", {"value": "blocked"}, context)

        assert allowed.success is True
        assert allowed.value == "allowed"
        assert blocked.success is False
        assert blocked.error == "denied"
        assert executed == ["allowed"]
        assert [item["tool"]["arguments"]["value"] for item in reviewed] == [
            "allowed",
            "blocked",
        ]
        assert all(item["user_request"] == "Run the echo tool" for item in reviewed)
        assert len(posted) == 1
        assert posted[0]["result"]["value"] == "allowed"
        store.close()

    run(scenario())


def test_batch_runs_concurrently_but_returns_model_call_order():
    async def scenario():
        active = 0
        peak = 0

        async def delayed(arguments, _context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(arguments["delay"])
                return arguments["value"]
            finally:
                active -= 1

        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "batch",
                "batch",
                (
                    Plugin(
                        "Delay",
                        "Delay",
                        {"type": "object"},
                        delayed,
                        allow_parallel=True,
                    ),
                ),
            ),
            source="test",
        )
        runner = PluginBatchRunner(PluginRuntime(registry))
        results = await runner.run(
            (
                PluginCall("Delay", {"delay": 0.03, "value": "first"}, id="first"),
                PluginCall("Delay", {"delay": 0.0, "value": "second"}, id="second"),
            )
        )

        assert peak == 2
        assert [result.call_id for result in results] == ["first", "second"]
        assert [result.value for result in results] == ["first", "second"]

    run(scenario())


def test_batch_finishes_all_parallel_reviews_before_tool_execution(tmp_path):
    async def scenario():
        review_finished = []
        executed = []
        active_reviews = 0
        peak_reviews = 0

        async def permission_model(_system, request):
            nonlocal active_reviews, peak_reviews
            active_reviews += 1
            peak_reviews = max(peak_reviews, active_reviews)
            await asyncio.sleep(0.01)
            value = request["tool"]["arguments"]["value"]
            review_finished.append(value)
            active_reviews -= 1
            return {"approve": value != "b", "rationale": "reviewed"}

        async def echo(arguments, _context):
            assert sorted(review_finished) == ["a", "b", "c"]
            executed.append(arguments["value"])
            return arguments["value"]

        store = ContextStoreRouter(tmp_path / "context")
        reviewer = PermissionReviewPlugin(permission_model)
        tree = store.create_tree(
            tree_id="tree",
            root_id="root",
            initial_hooks=(reviewer.registration(),),
        )
        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "test",
                "test",
                (
                    Plugin(
                        "Echo",
                        "Echo",
                        {"type": "object"},
                        echo,
                        allow_parallel=True,
                    ),
                ),
            ),
            source="test",
        )
        runner = PluginBatchRunner(PluginRuntime(registry))
        results = await runner.run(
            (
                PluginCall("Echo", {"value": "a"}, id="a"),
                PluginCall("Echo", {"value": "b"}, id="b"),
                PluginCall("Echo", {"value": "c"}, id="c"),
            ),
            PluginContext(hooks=store.hooks_for(tree.id)),
        )

        assert peak_reviews == 3
        assert sorted(executed) == ["a", "c"]
        assert [result.success for result in results] == [True, False, True]
        assert results[1].error == "reviewed"
        store.close()

    run(scenario())


def test_batch_limits_parallel_tools_to_eight_and_serial_plugins_are_barriers():
    async def scenario():
        active = 0
        peak = 0
        order = []

        async def parallel(arguments, _context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                order.append(arguments["value"])
                return arguments["value"]
            finally:
                active -= 1

        async def serial(arguments, _context):
            assert active == 0
            order.append(arguments["value"])
            return arguments["value"]

        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "test",
                "test",
                (
                    Plugin(
                        "Parallel",
                        "Parallel",
                        {"type": "object"},
                        parallel,
                        allow_parallel=True,
                    ),
                    Plugin(
                        "Serial",
                        "Serial",
                        {"type": "object"},
                        serial,
                        allow_parallel=False,
                    ),
                ),
            ),
            source="test",
        )
        runner = PluginBatchRunner(PluginRuntime(registry))
        calls = [
            PluginCall("Parallel", {"value": f"p{index}"}, id=f"p{index}")
            for index in range(10)
        ]
        calls.append(PluginCall("Serial", {"value": "serial"}, id="serial"))
        calls.extend(
            PluginCall("Parallel", {"value": f"tail{index}"}, id=f"tail{index}")
            for index in range(2)
        )
        results = await runner.run(calls)

        assert peak == 8
        assert order.index("serial") == 10
        assert [result.call_id for result in results] == [call.id for call in calls]

    run(scenario())


def test_tool_plugin_defines_its_runtime_timeout():
    async def scenario():
        async def slow(_arguments, _context):
            await asyncio.sleep(0.05)
            return "late"

        registry = PluginRegistry(include_core=False)
        registry.register_pack(
            PluginPack(
                "test",
                "test",
                (
                    Plugin(
                        "Slow",
                        "Slow",
                        {"type": "object"},
                        slow,
                        timeout_seconds=0.01,
                    ),
                ),
            ),
            source="test",
        )
        result = (
            await PluginBatchRunner(PluginRuntime(registry)).run(
                (PluginCall("Slow", {}, id="slow"),)
            )
        )[0]

        assert result.success is False
        assert result.error == "Plugin timed out after 0.01 seconds"

    run(scenario())


def test_fixed_read_write_and_bash_plugins(tmp_path):
    async def scenario():
        runtime = PluginRuntime(PluginRegistry())
        context = PluginContext(workspace=tmp_path)

        written = await runtime.call(
            "Write",
            {"path": "nested/file.txt", "content": "hello"},
            context,
        )
        read = await runtime.call("Read", {"path": "nested/file.txt"}, context)
        command = f'"{sys.executable}" -c "print(123)"'
        shell = await runtime.call("Bash", {"command": command}, context)

        assert written.success is True
        assert read.value == "hello"
        assert shell.success is True
        assert shell.value["exit_code"] == 0
        assert shell.value["stdout"].strip() == "123"

    run(scenario())


def test_filesystem_plugins_follow_toolbox_list_describe_invoke_chain(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        source = workspace / "src"
        source.mkdir(parents=True)
        target = source / "example.py"
        target.write_text(
            "def greeting():\n    return 'hello'\n",
            encoding="utf-8",
        )
        (source / "notes.txt").write_text("hello from notes\n", encoding="utf-8")

        registry = PluginRegistry()
        plugin_directory = Path(__file__).parents[3] / "plugin_impl"
        assert registry.load_directory(plugin_directory) == ()
        assert {
            definition["function"]["name"]
            for definition in registry.direct_tool_definitions()
        } == {"Bash", "Read", "Write", "toolbox"}
        runtime = PluginRuntime(registry)
        context = PluginContext(workspace=workspace)

        listing = await runtime.call(
            "toolbox",
            {"operation": "list"},
            context,
        )
        assert listing.success is True
        assert listing.value["packs"] == []
        assert listing.value["standalone_tools"] == [
            {
                "name": "Edit",
                "description": "Replace an exact string in a text file.",
            },
            {
                "name": "Glob",
                "description": "Find files in the workspace using a glob pattern.",
            },
            {
                "name": "Grep",
                "description": "Search file contents by regex pattern inside the workspace.",
            },
        ]

        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "names": ["Glob", "Grep", "Edit"]},
            context,
        )
        assert described.success is True
        descriptions = {
            item["name"]: item for item in described.value["plugins"]
        }
        assert set(descriptions) == {"Edit", "Glob", "Grep"}
        assert all(item["pack"] is None for item in descriptions.values())
        assert descriptions["Glob"]["input_schema"]["required"] == ["pattern"]
        assert descriptions["Grep"]["input_schema"]["required"] == ["pattern"]
        assert descriptions["Edit"]["input_schema"]["required"] == [
            "path",
            "old_string",
            "new_string",
        ]

        globbed = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "Glob",
                "arguments": {"pattern": "**/*.py"},
            },
            context,
        )
        assert globbed.success is True
        assert globbed.value["result"] == "src/example.py"

        grepped = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "Grep",
                "arguments": {
                    "pattern": "return 'hello'",
                    "path": "src",
                    "glob": "*.py",
                },
            },
            context,
        )
        assert grepped.success is True
        assert grepped.value["result"] == "src/example.py:2:    return 'hello'"

        edited = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "Edit",
                "arguments": {
                    "path": "src/example.py",
                    "old_string": "return 'hello'",
                    "new_string": "return 'hello plugin'",
                },
            },
            context,
        )
        assert edited.success is True
        assert edited.value["result"] == f"Edited {target}. Replacements: 1"
        assert "return 'hello plugin'" in target.read_text(encoding="utf-8")

    run(scenario())


def test_minimax_model_pack_uses_openai_tool_call_shape(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        async def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "response-1",
                    "model": "MiniMax-M2.7",
                    "usage": {
                        "prompt_tokens": 21,
                        "completion_tokens": 8,
                        "prompt_tokens_details": {"cached_tokens": 14},
                    },
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_details": [
                                    {"type": "reasoning.text", "text": "Need a file."}
                                ],
                                "tool_calls": [
                                    {
                                        "id": "call-read",
                                        "type": "function",
                                        "function": {
                                            "name": "Read",
                                            "arguments": '{"path":"README.md"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            )

        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        monkeypatch.setenv("MINIMAX_BASE_URL", "https://minimax.test/v1")
        registry = PluginRegistry(include_core=False)
        failures = registry.load_directory(Path(__file__).parents[3] / "plugin_impl")
        assert failures == ()
        assert registry.resolve("MiniMax").kind == "model"
        assert {
            definition["function"]["name"]
            for definition in registry.tool_definitions()
        } == {"Edit", "Glob", "Grep"}

        store = ContextStoreRouter(tmp_path / "context")
        tree = store.create_tree({"role": "system"}, tree_id="tree", root_id="root")
        result = await PluginRuntime(registry).call(
            "MiniMax",
            {
                "messages": [{"role": "user", "content": "Read the file"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "description": "Read",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "Read"},
                },
            },
            PluginContext(
                tree=store,
                tree_id=tree.id,
                node_id=tree.root_id,
                data={
                    "http_transport": httpx.MockTransport(respond),
                    "model_call_kind": "agent",
                },
            ),
        )

        assert result.success is True
        assert result.value["reasoning"] == "Need a file."
        assert result.value["tool_calls"] == [
            {
                "id": "call-read",
                "name": "Read",
                "arguments": {"path": "README.md"},
            }
        ]
        assert requests[0]["model"] == "MiniMax-M2.7"
        assert requests[0]["tool_choice"] == {
            "type": "function",
            "function": {"name": "Read"},
        }
        assert requests[0]["reasoning_split"] is True
        assert result.value["usage_observation"]["cached_prompt_tokens"] == 14
        assert result.value["usage_observation"]["cache_hit_rate"] == 14 / 21
        observations = [
            node.value
            for node in store.get_subtree(tree.id, tree.root_id)
            if isinstance(node.value, dict)
            and node.value.get("role") == "model_observation"
        ]
        assert len(observations) == 1
        assert observations[0]["call_kind"] == "agent"
        assert observations[0]["usage"]["cached_prompt_tokens"] == 14
        store.close()

    run(scenario())

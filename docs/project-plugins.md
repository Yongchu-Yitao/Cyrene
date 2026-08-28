# Cyrene custom plugins

[English](project-plugins.md) · [简体中文](project-plugins.zh-CN.md)

Cyrene has one plugin framework. Custom tools, application services, context
Hooks, model providers, and Workbench views are all supplied by `Plugin` or
`PluginPack`. The retired `plugin.json`, project-plugin subprocess, and
`cyrene.view` extension mechanisms are not part of the runtime.

## Directory format

User plugins live in the application data directory's `plugin_impl/` folder. A
standalone tool may be one Python file exporting `plugin`. A pack is a directory
whose `__init__.py` exports `plugin_pack`.

```python
from agent.plugin import PluginPack
from .application import setup_application

plugin_pack = PluginPack(
    id="example_dashboard",
    description="Example dashboard.",
    plugins=(),
    application_setup=setup_application,
    metadata={
        "frontend_views": ({
            "id": "main",
            "entry": "ui/index.html",
            "title": "Dashboard",
            "i18n": {"zh": {"title": "仪表盘"}},
        },),
        "project_tools": ({
            "id": "main",
            "view": "main",
            "title": "Dashboard",
            "subtitle": "Plugin view",
            "icon_text": "◇",
            "i18n": {
                "zh": {"title": "仪表盘", "subtitle": "插件视图"}
            },
        },),
    },
)
```

## Agent composition and context Hooks

Plugins do not inject content after a fixed Agent has already been created; the
enabled packs are the Agent's composition. At application startup,
`application_setup` may contribute routes, services, background jobs, and UI.
When a conversation opens, `setup` receives a `PluginSetupContext`, publishes
session services, and binds Hooks to that conversation's ContextTree. Each run
then uses those tree-local Hooks to build context, review tools, record results,
and finalize work.

A minimal context plugin returns one block from `SessionStart`:

```python
from agent.hook import SESSION_START, HookEvent
from agent.plugin import PluginPack, PluginSetupContext


def setup(context: PluginSetupContext) -> None:
    async def mount(_event: HookEvent) -> dict[str, str]:
        return {
            "context": "## Project rules\nOnly edit files in this workspace.",
            "context_position": "",
        }

    context.hooks.register(
        SESSION_START,
        mount,
        plugin_id="project_rules.mount",
        hook_id="project-rules-session-start",
        root_only=True,
        failure_policy="closed",
    )


plugin_pack = PluginPack(
    id="project_rules",
    description="Mount project rules into the Agent context.",
    plugins=(),
    setup=setup,
)
```

Use `context_position="system"` for base system instructions, `"top"` for a
high-priority block immediately below them (such as SOUL), and an empty value
for ordinary context. Ordinary blocks retain deterministic registration order.
A Hook ID persists with the tree; when restoring a session, the pack should use
`bind_plugin(..., replace=True)` to reconnect its implementation instead of
creating another state store.

| Hook | Appropriate work |
|---|---|
| `SessionStart` | Build the run's context mounts |
| `ContextChange` / `ContextUsed` | React to tree changes, account for actual token use, and drive memory or compaction logic |
| `PreToolUse` | Normalize, allow, or block tool arguments; use fail-closed behavior when failure must block |
| `PostToolUse` | Persist a tool result, learning evidence, or activity state |
| `SessionEnd` | Finalize asynchronous work after the durable result exists |
| `Stop` | Stop plugin-owned tasks when the user cancels or the session closes |

Input-box selections are not read independently by every provider.
`cyrene_composer_context` is the single composer-context plugin: it persists the
conversation's choices, then asks enabled workspace, MCP, and skills providers
for their `SessionStart` contribution and produces an explicit mount. Plugin
Center controls whether a plugin is available; the composer menu controls what
this conversation selects; the tool menu controls **directly visible** versus
**Agent finds and uses**. These are separate responsibilities.

Tool packs and standalone tools share the same `Plugin` protocol. A directly
visible tool places its schema in the immediate model tool list; other enabled
tools remain discoverable through
`toolbox.list → toolbox.describe → toolbox.invoke`. Both paths use the active
plugin's `input_schema`, runtime validation, `PreToolUse`, and `PostToolUse`.

## Workbench views

Every `project_tools[].view` must reference a `frontend_views[].id` in the same
pack, and each view `entry` must stay inside that pack. Enabling the pack adds
its entry to the Workbench sidebar. The view opens as a normal pane and supports
horizontal or vertical splits, drag and restore, and a separate window.

## Backend RPC

```python
async def load(arguments, request_context):
    return {"ok": True, "project_id": request_context["project_id"]}


def setup_application(context):
    context.provide_frontend_method("dashboard.load", load)
```

The sandboxed iframe sends a `postMessage` request:

```js
parent.postMessage({
  source: "cyrene-plugin",
  type: "call",
  requestId,
  method,
  args,
}, "*");
```

The host replies with `init` and `response` messages whose source is
`cyrene-host`. A plugin view cannot directly access the host React tree or DOM.

## Authoring flow

The Agent uses
`PluginAuthoringGuide → PluginScaffold → PluginValidate → PluginInstall` to
create and install plugins. `PluginScaffold.plugin_type` supports:

- `standalone_tool`: one directly registered tool file;
- `tool_pack`: a pack containing one or more tools;
- `model_plugin`: a Provider for model discovery and completion;
- `context_plugin`: a `SessionStart` Hook that builds context;
- `application_plugin`: application routes, services, and lifecycle;
- `ui_plugin`: a Workbench split-pane view with backend RPC;
- `full_pack`: a composable pack combining these contributions.

Generated sources include English and Chinese metadata. `PluginReload` rescans
the single plugin directory. Adding or changing `application_setup` requires an
application restart; frontend-only resources are read directly by the host.

See `examples/plugins/model-usage` for a complete example.

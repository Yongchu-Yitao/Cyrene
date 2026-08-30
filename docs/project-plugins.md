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
from cyrene.core.plugin import PluginPack
from cyrene.plugins import PluginApplicationContext
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
from cyrene.core.hook import SESSION_START, HookEvent
from cyrene.core.plugin import PluginPack, PluginSetupContext


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
| `SessionStart` | Freeze stable context once when the conversation starts |
| `TurnStart` | Build dynamic context for each user turn |
| `ContextChange` / `ContextUsed` | React to tree changes, account for actual token use, and drive memory or compaction logic |
| `PreToolUse` | Normalize, allow, or block tool arguments; use fail-closed behavior when failure must block |
| `PostToolUse` | Persist a tool result, learning evidence, or activity state |
| `SessionEnd` | Finalize asynchronous work after the durable result exists |
| `Stop` | Stop plugin-owned tasks when the user cancels or the session closes |

A `SessionStart` callable whose output depends on mutable stable input should
attach a provider with `with_session_start_cache_fingerprint(hook, provider)`.
The provider returns any JSON-serializable projection of those dependencies.
The kernel treats it as opaque, combines it with Hook topology and pack
implementation versions, and rebuilds the stable prefix once when the value
changes. Bound Hook owners may instead implement
`session_start_cache_fingerprint(event)` directly. This keeps SOUL, memory,
learned skills, CLI Hooks, and third-party providers inside their own plugin
boundaries.

Input-box selections are not read independently by every provider.
`cyrene_composer_context` is the single composer-context plugin: it persists the
conversation's choices, then asks enabled workspace, MCP, and skills providers
for their `TurnStart` contribution and produces an explicit mount. Plugin
Center controls whether a plugin is available; the composer menu controls what
this conversation selects; the tool menu controls **directly visible** versus
**Agent finds and uses**. These are separate responsibilities.

Tool packs and standalone tools share the same `Plugin` protocol. A directly
visible tool places its schema in the immediate model tool list; other enabled
tools remain discoverable through
`toolbox.list → toolbox.describe → toolbox.invoke`. Both paths use the active
plugin's `input_schema`, runtime validation, `PreToolUse`, and `PostToolUse`.

## Calling Cyrene's configured models

An executable plugin does not need its own provider client or API key. During a
normal Agent or background invocation, the host exposes the existing model
gateway as `PluginContext.services["model"]`. The gateway routes requests through
Cyrene's enabled model Provider Plugins and the models configured by the user.

```python
from dataclasses import replace

from cyrene.core.plugin import Plugin, PluginContext


async def summarize(arguments: dict, context: PluginContext) -> dict:
    gateway = context.services.get("model")
    if gateway is None:
        raise RuntimeError("Cyrene's configured model service is unavailable")

    # An auxiliary model response is consumed by this plugin, so do not project
    # its streaming deltas into the main assistant reply. Keep the remaining
    # context so usage and runtime events stay attached to this invocation.
    model_context = replace(
        context,
        services={
            name: service
            for name, service in context.services.items()
            if name != "model_stream"
        },
    )
    session_id = str(context.data.get("session_id") or context.tree_id or "")
    response = await gateway.complete(
        [
            {
                "role": "system",
                "content": "Summarize the supplied text in one paragraph.",
            },
            {"role": "user", "content": str(arguments["text"])},
        ],
        route="secondary",
        caller="plugin:example_summary",
        session_id=session_id,
        max_tokens=600,
        temperature=0.2,
        context=model_context,
    )
    return {
        "summary": str(response.get("content") or ""),
        "model": str(response.get("model") or ""),
        "usage": dict(response.get("usage") or {}),
    }


plugin = Plugin(
    name="ExampleSummary",
    description="Summarize text with a model configured in Cyrene.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    handler=summarize,
)
```

Use `primary` to follow the model selected for the current conversation,
`secondary` for Cyrene's configured auxiliary route, and `vision` for image-aware
requests. Pass the current session ID so conversation-specific model selection
continues to apply. The response contains normalized `content`, `reasoning`,
`tool_calls`, `usage`, `model`, and `model_identity` fields. Supplying `tools`
allows the model to return tool calls, but the gateway does not execute them for
the plugin. Provider credentials remain inside the selected Provider Plugin and
are not returned by the gateway.

## Contribution scopes

`PluginPack` contributions have three explicit lifetimes:

| Scope | API | Owner |
|---|---|---|
| Application | `application_setup` / `APPLICATION_SETUP` | Cyrene's plugin application host; routes, process services, search, frontend RPC, startup and shutdown |
| Session | `setup` / `SESSION_SETUP` | `cyrene.core.AgentSession`; ContextTree Hooks and conversation-local services |
| Run | `PluginContext.services` and `RUN_SERVICE` | One invocation/run; request data and ephemeral service bindings |

The callback fields are convenient forms of the same typed extension system;
hosts consume normalized `ExtensionContribution` values. Core code must not
import Workbench or FastAPI types. Application callbacks receive
`cyrene.plugins.PluginApplicationContext`, while session and run callbacks use
the host-neutral classes from `cyrene.core.plugin`.

The former `agent.*`, `route.*`, and `webui.*` Python packages do not exist and
are not compatibility aliases. Plugin code must use the canonical `cyrene.core`,
`cyrene.plugins`, and `cyrene.workbench` APIs shown here.

## Workbench views

Every `project_tools[].view` must reference a `frontend_views[].id` in the same
pack, and each view `entry` must stay inside that pack. Enabling the pack adds
its entry to the Workbench sidebar. The view opens as a normal pane and supports
horizontal or vertical splits, drag and restore, and a separate window.

### Dynamic workspace contributions

Workbench workspace capabilities use the same typed extension system:

| Extension point | Contribution |
|---|---|
| `WORKBENCH_SURFACE` | A native or sandboxed pane that can present validated resources inside a conversation split |
| `WORKSPACE_FILE_TYPE` | File extensions, viewer/editor mode, and language metadata owned by a pack |
| `WORKSPACE_ACTION` | A fixed Build, Run, Test, or Preview action whose handler is owned by the contributing pack |
| `WORKSPACE_PROJECT_TYPE` | Marker-based project detection plus action discovery and matching runtime-extension IDs |

Project-type packs should be disabled by default when they require a runtime.
List that runtime in `runtime_extensions`; the Extensions service installs or
enables the pack when the runtime is installed or already available on the
system. Detection must stay read-only and return workspace-relative action
profiles. A project action must resolve a program and argument vector—it must
not expose an arbitrary shell string—and all paths must remain inside the
validated project workspace.

Resource-producing tools can declare `resource_effects` in their `Plugin`
definition. The runtime converts successful effects into validated presentation
locations. An effect may update an already-open surface by default; opening a
new Editor or Files surface should require an explicit user request such as
showing or editing a named file. This keeps ordinary reads and searches from
continually changing the user's layout.

## Backend RPC

```python
async def load(arguments, request_context):
    return {"ok": True, "project_id": request_context["project_id"]}


def setup_application(context: PluginApplicationContext) -> None:
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

"""Progressive authoring and lifecycle tools for trusted Cyrene plugins.

The model sees these capabilities only after toolbox discovery.  Source editing
continues to use the ordinary workspace file tools; this package supplies the
Cyrene-specific contract, validation, installation and runtime debug loop.
"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from cyrene.plugins.manifest import PluginManifestError, load_manifest, require_plugin_id
from cyrene.plugins.manager import PluginError, get_plugin_manager
from cyrene.tooling.runtime_api import (
    request_destructive_confirmation,
    request_read_elevation,
    request_scope_elevation,
    request_write_elevation,
    resolve_tool_path,
    resolve_workspace_write_target,
)


Handler = Callable[[dict[str, Any], Any, int, str, dict[str, bool] | None], Awaitable[str]]


AUTHORING_GUIDE = """# Cyrene trusted plugin authoring contract (API v1)

Use this contract for Settings > Custom Plugins. It is unrelated to Custom Tools,
Agent Skills, MCP integrations, and the Extension Center. A plugin owns its models,
runtimes, downloads, services, ports, credentials, configuration, and functional
correctness. Cyrene supplies only package installation, per-project enablement,
an isolated Python host process, contribution registration, JSON RPC, events, and
iframe panes that participate in the existing split/detach/fullscreen layout.

## Complete development loop
1. Scaffold a package in the active workspace, or create the same files manually.
2. Edit plugin-owned source with the normal Read/Write/Edit/Glob/Grep tools.
3. Validate without executing plugin code. Fix every error before installation.
4. Install the exact package directory. Reinstall with replace=true after source changes.
5. Enable it for the current project, inspect contributions, call backend methods,
   inspect logs, and verify the UI in Cyrene. Reload after changing an installed copy.
6. Disable or delete through plugin lifecycle capabilities; never edit Cyrene's
   installed plugin store or state files directly.

All persistent lifecycle mutations and Agent-initiated backend calls use Cyrene's
unified review path. In Auto mode the central review Agent makes the bounded decision.
Deleting plugin data remains an irreversible operation and requires the central
destructive confirmation policy.

## Package
The package root contains plugin.json (cyrene.plugin.json is also accepted):

```json
{
  "apiVersion": 1,
  "id": "com.example.plugin",
  "name": "Example Plugin",
  "version": "1.0.0",
  "description": "...",
  "backend": {"type": "python", "entry": "plugin.py"},
  "frontend": {"mode": "iframe", "entry": "ui/index.html"},
  "contributes": []
}
```

IDs start with a letter or number and contain only letters, numbers, dot, underscore,
or hyphen. Entries must be relative files inside the package. Python and iframe are
the only v1 backend/frontend modes. A plugin may omit either backend or frontend.

## Python backend
The module may export sync or async activate(context) and deactivate(context).
The context exposes plugin_id, project_id, package_dir, data_dir,
register_method(name, callable), register(point, descriptor), and emit(name, payload).
Registered methods accept zero arguments or one JSON value and must return JSON.
Callable values inside a contribution descriptor become RPC method references.
As a fallback, export handle(method, args, context).

```python
async def refresh(args):
    return {"ok": True, "value": 42}

def activate(context):
    context.register_method("usage.refresh", refresh)
    context.register("cyrene.view", {"id": "usage.dashboard", "title": "Model Usage"})
    context.register("cyrene.projectTool", {
        "id": "usage", "title": "Model Usage", "view": "usage.dashboard"
    })
```

Each enabled plugin runs in a separate process for each project. Write durable files
only below context.data_dir. Stop plugin-owned services and child processes from
deactivate. stdout is redirected into plugin logs; protocol messages are host-owned.

## UI bridge and panes
A cyrene.projectTool contribution whose view references a cyrene.view appears under
the sidebar Tools section only while enabled for that project. Opening it creates a
normal plugin-view pane, so all current horizontal/vertical split, swap, drag,
fullscreen, and detached-window behavior is automatic.

The iframe calls its backend with parent.postMessage({source:'cyrene-plugin',
type:'call',requestId,method,args}, '*'). It receives source:'cyrene-host' messages:
init (context), response (requestId, ok, result/error), and event (event, payload).
Keep a requestId-to-Promise map and validate event.data.source before consuming it.
The iframe is plugin-owned HTML/CSS/JS; do not depend on Cyrene's private React code.

## Open contribution points
Known consumers include cyrene.view, cyrene.projectTool, cyrene.chatProvider,
cyrene.embeddingProvider, cyrene.ocrProvider, cyrene.asrProvider,
cyrene.ttsProvider, cyrene.command, and cyrene.agentAction. Plugins may publish
additional namespaced points; the plugin remains responsible for semantics.

A cyrene.chatProvider can expose models and a callable complete(request). It receives
model, messages, tools, stream, reasoning effort, phase, and session information. It
returns a string, a normalized message, or {message, usage, events}. This is how a
plugin-owned llama.cpp/GGUF runtime or another model backend becomes selectable in
Agent Chat; Cyrene does not install or supervise that runtime unless the plugin does.

## Validation and verification
Validation parses the manifest, confines entries, compiles Python syntax without
executing it, checks duplicate static contributions, and verifies static projectTool
view references. Runtime-only contributions must be verified after enablement with
contribution inspection. Use method calls for bounded backend checks and inspect logs
after startup or RPC failures. UI correctness remains the plugin's responsibility.
"""


def _definition(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


PROJECT_ID = {
    "type": "string",
    "description": "Workbench project ID. Omit to use the current conversation's project.",
}
PLUGIN_ID = {"type": "string", "description": "Stable plugin manifest ID."}


TOOL_DEFS = [
    _definition(
        "PluginAuthoringGuide",
        "Load the complete Cyrene API v1 trusted-plugin authoring contract and end-to-end development workflow.",
    ),
    _definition(
        "PluginScaffold",
        "Create a safe, complete Cyrene plugin source skeleton in a new workspace directory. Use normal file tools for subsequent edits.",
        {
            "path": {"type": "string", "description": "New workspace-relative package directory."},
            "plugin_id": PLUGIN_ID,
            "name": {"type": "string"},
            "description": {"type": "string"},
            "version": {"type": "string", "default": "0.1.0"},
            "include_backend": {"type": "boolean", "default": True},
            "include_ui": {"type": "boolean", "default": True},
            "project_tool": {"type": "boolean", "default": True},
        },
        ["path", "plugin_id", "name"],
    ),
    _definition(
        "PluginValidate",
        "Validate a plugin source package without importing or executing plugin code.",
        {"path": {"type": "string", "description": "Plugin package directory or archive-free source root."}},
        ["path"],
    ),
    _definition(
        "PluginList",
        "List installed plugins and their state for a project.",
        {"project_id": PROJECT_ID},
    ),
    _definition(
        "PluginInstall",
        "Install a validated local plugin package, or replace the installed copy after source changes. This is centrally reviewed.",
        {
            "path": {"type": "string", "description": "Local plugin directory or ZIP archive."},
            "replace": {"type": "boolean", "default": False},
        },
        ["path"],
    ),
    _definition(
        "PluginEnable",
        "Enable an installed plugin for a project and start its isolated host. This executes trusted plugin code and is centrally reviewed.",
        {"plugin_id": PLUGIN_ID, "project_id": PROJECT_ID},
        ["plugin_id"],
    ),
    _definition(
        "PluginDisable",
        "Disable a plugin for a project, stop its host, and remove its live contributions. This is centrally reviewed.",
        {"plugin_id": PLUGIN_ID, "project_id": PROJECT_ID},
        ["plugin_id"],
    ),
    _definition(
        "PluginReload",
        "Restart an enabled plugin host and refresh its contributions after development changes. This is centrally reviewed.",
        {"plugin_id": PLUGIN_ID, "project_id": PROJECT_ID},
        ["plugin_id"],
    ),
    _definition(
        "PluginContributions",
        "Inspect live contributions from enabled plugins, optionally filtered by extension point.",
        {
            "project_id": PROJECT_ID,
            "point": {"type": "string", "description": "For example cyrene.projectTool or cyrene.chatProvider."},
        },
    ),
    _definition(
        "PluginCall",
        "Call one registered plugin backend method for development verification. Arbitrary plugin methods may have side effects, so every call is centrally reviewed.",
        {
            "plugin_id": PLUGIN_ID,
            "project_id": PROJECT_ID,
            "method": {"type": "string"},
            "arguments": {"description": "Any JSON value passed to the plugin method."},
            "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 120},
        },
        ["plugin_id", "method"],
    ),
    _definition(
        "PluginLogs",
        "Read recent stderr logs for one plugin host in a project.",
        {"plugin_id": PLUGIN_ID, "project_id": PROJECT_ID},
        ["plugin_id"],
    ),
    _definition(
        "PluginDelete",
        "Delete an installed plugin package, optionally also deleting its retained data and logs. Uses central review and destructive confirmation when data is included.",
        {
            "plugin_id": PLUGIN_ID,
            "delete_data": {"type": "boolean", "default": False},
        },
        ["plugin_id"],
    ),
]


def _result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(exc: Exception) -> str:
    return _result({"ok": False, "error": str(exc), "errorType": exc.__class__.__name__})


def _project_id(args: dict[str, Any]) -> str:
    project_id = str(args.get("project_id") or "").strip()
    if project_id:
        return project_id
    from cyrene.agent.context import get_current_session_id
    from cyrene.workbench.context import resolve_workbench_project_id_for_session

    project_id = str(resolve_workbench_project_id_for_session(get_current_session_id()) or "").strip()
    if not project_id:
        raise PluginError("project_id is required outside a project conversation")
    return project_id


async def _review(operation: str, target: str, arguments: dict[str, Any]) -> str | None:
    encoded = json.dumps(
        {"operation": operation, "target": target, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return await request_scope_elevation(
        tool_name="PluginDeveloper",
        path_hint=f"plugin:{target}:{fingerprint[:16]}",
        operation=f"自定义插件操作：{operation} {target}",
        reason=(
            "Installing, executing, reloading, invoking, or removing a trusted plugin "
            "changes Cyrene's persistent or runtime capabilities."
        ),
        permission_kind="plugin_change",
        scope_hint="本机插件能力的 ",
    )


async def _resolve_read_path(path_value: Any, *, tool_name: str) -> Path | str:
    raw = str(path_value or "").strip()
    try:
        return resolve_tool_path(raw)
    except ValueError:
        reviewed = await request_read_elevation(
            tool_name=tool_name,
            path_hint=raw,
            reason="Agent needs to inspect the selected plugin package.",
        )
        if reviewed is not None:
            return reviewed
        return resolve_tool_path(raw)


def _validate_path(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = load_manifest(root)
    except (PluginManifestError, OSError, ValueError) as exc:
        return {"ok": False, "installable": False, "path": str(root), "errors": [str(exc)], "warnings": []}

    if manifest.backend_entry:
        backend_path = root / manifest.backend_entry
        try:
            compile(backend_path.read_text(encoding="utf-8"), str(backend_path), "exec")
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"backend syntax/read error: {exc}")

    seen: set[tuple[str, str]] = set()
    views: set[str] = set()
    project_view_refs: list[tuple[str, str]] = []
    for contribution in manifest.contributions:
        point = str(contribution.get("point") or "")
        contribution_id = str(contribution.get("id") or "")
        identity = (point, contribution_id)
        if identity in seen:
            errors.append(f"duplicate contribution: {point}/{contribution_id}")
        seen.add(identity)
        if point == "cyrene.view":
            views.add(contribution_id)
            if not manifest.frontend_entry:
                errors.append(f"cyrene.view {contribution_id} requires frontend.entry")
        elif point == "cyrene.projectTool":
            view_id = str(contribution.get("view") or "").strip()
            if not view_id:
                errors.append(f"cyrene.projectTool {contribution_id} requires view")
            else:
                project_view_refs.append((contribution_id, view_id))
    for contribution_id, view_id in project_view_refs:
        if view_id not in views:
            message = (
                f"cyrene.projectTool {contribution_id} references missing static "
                f"cyrene.view {view_id}"
            )
            if manifest.backend_entry:
                warnings.append(message + "; verify that the backend registers it at runtime")
            else:
                errors.append(message)
    if manifest.backend_entry and not manifest.contributions:
        warnings.append("runtime contributions cannot be verified until the plugin is enabled")
    return {
        "ok": not errors,
        "installable": not errors,
        "path": str(root),
        "manifest": {**manifest.public_dict(), "contributionCount": len(manifest.contributions)},
        "errors": errors,
        "warnings": warnings,
    }


async def _guide(_args: dict[str, Any], *_unused: Any) -> str:
    return _result({"ok": True, "apiVersion": 1, "guide": AUTHORING_GUIDE})


async def _scaffold_root(raw_path: str) -> Path | str:
    try:
        return resolve_workspace_write_target(raw_path)
    except ValueError:
        reviewed = await request_write_elevation(
            tool_name="PluginScaffold",
            path_hint=raw_path,
            reason="Agent needs to create the requested plugin source package.",
        )
        if reviewed is not None:
            return reviewed
        return resolve_workspace_write_target(raw_path)


def _scaffold_manifest(
    *, plugin_id: str, name: str, version: str, description: str,
    include_backend: bool, include_ui: bool, project_tool: bool,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "apiVersion": 1,
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
    }
    if include_backend:
        manifest["backend"] = {"type": "python", "entry": "plugin.py"}
    if include_ui:
        manifest["frontend"] = {"mode": "iframe", "entry": "ui/index.html"}
    if not include_backend and project_tool:
        manifest["contributes"] = [
            {"point": "cyrene.view", "id": "main", "title": name},
            {"point": "cyrene.projectTool", "id": "main", "title": name, "view": "main"},
        ]
    return manifest


def _scaffold_backend_source(name: str, include_ui: bool, project_tool: bool) -> str:
    registration = ""
    if include_ui:
        registration += (
            f"    context.register(\"cyrene.view\", {{\"id\": \"main\", \"title\": {name!r}}})\n"
        )
        if project_tool:
            registration += (
                f"    context.register(\"cyrene.projectTool\", {{\"id\": \"main\", \"title\": {name!r}, \"view\": \"main\"}})\n"
            )
    return (
        '"""Cyrene plugin backend."""\n\n'
        "async def ping(arguments):\n"
        "    return {\"ok\": True, \"echo\": arguments}\n\n\n"
        "def activate(context):\n"
        "    context.register_method(\"ping\", ping)\n"
        f"{registration or '    # Register extension-point contributions here.\n'}\n\n"
        "def deactivate(context):\n"
        "    # Stop plugin-owned services and child processes here.\n"
        "    pass\n"
    )


def _scaffold_ui_source(name: str, include_backend: bool) -> str:
    safe_name = html.escape(name)
    backend_control = (
        '<button id="ping">Test backend</button><pre id="output">Ready</pre>'
        if include_backend
        else '<p>UI-only plugin ready.</p>'
    )
    backend_listener = (
        """document.querySelector('#ping').addEventListener('click', async () => {
      const output = document.querySelector('#output');
      try { output.textContent = JSON.stringify(await call('ping', {from:'ui'}), null, 2); }
      catch (error) { output.textContent = String(error); }
    });"""
        if include_backend
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_name}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; padding: 24px; background: Canvas; color: CanvasText; }}
    .card {{ border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); border-radius: 16px; padding: 20px; }}
    button {{ font: inherit; padding: 8px 14px; }} pre {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  <main class="card"><h1>{safe_name}</h1>{backend_control}</main>
  <script>
    const pending = new Map();
    function call(method, args) {{
      const requestId = crypto.randomUUID();
      parent.postMessage({{source:'cyrene-plugin', type:'call', requestId, method, args}}, '*');
      return new Promise((resolve, reject) => pending.set(requestId, {{resolve, reject}}));
    }}
    addEventListener('message', event => {{
      const message = event.data;
      if (message?.source !== 'cyrene-host') return;
      if (message.type === 'init') window.pluginContext = message.context;
      if (message.type === 'response') {{
        const request = pending.get(message.requestId); if (!request) return;
        pending.delete(message.requestId);
        message.ok ? request.resolve(message.result) : request.reject(new Error(message.error));
      }}
      if (message.type === 'event') document.dispatchEvent(new CustomEvent('cyrene-plugin-event', {{detail: message}}));
    }});
    {backend_listener}
  </script>
</body>
</html>
"""


def _scaffold_files(
    manifest: dict[str, Any], *, name: str, description: str,
    include_backend: bool, include_ui: bool, project_tool: bool,
) -> dict[str, str]:
    files = {
        "plugin.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "README.md": (
            f"# {name}\n\n{description or 'Cyrene trusted plugin.'}\n\n"
            "Validate, install, enable for a project, then inspect contributions and logs.\n"
        ),
    }
    if include_backend:
        files["plugin.py"] = _scaffold_backend_source(name, include_ui, project_tool)
    if include_ui:
        files["ui/index.html"] = _scaffold_ui_source(name, include_backend)
    return files


async def _scaffold(args: dict[str, Any], *_unused: Any) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        name = str(args.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        root = await _scaffold_root(str(args.get("path") or "").strip())
        if isinstance(root, str):
            return root
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError("scaffold target must be a new or empty directory")

        include_backend = args.get("include_backend") is not False
        include_ui = args.get("include_ui") is not False
        project_tool = args.get("project_tool") is not False and include_ui
        if not include_backend and not include_ui:
            raise ValueError("a plugin scaffold requires a backend or UI")

        version = str(args.get("version") or "0.1.0").strip() or "0.1.0"
        description = str(args.get("description") or "").strip()
        manifest = _scaffold_manifest(
            plugin_id=plugin_id, name=name, version=version,
            description=description, include_backend=include_backend,
            include_ui=include_ui, project_tool=project_tool,
        )
        files = _scaffold_files(
            manifest, name=name, description=description,
            include_backend=include_backend, include_ui=include_ui,
            project_tool=project_tool,
        )

        root.mkdir(parents=True, exist_ok=True)
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        validation = _validate_path(root)
        return _result({
            "ok": bool(validation.get("ok")),
            "path": str(root),
            "files": sorted(files),
            "validation": validation,
            "next": ["edit source", "validate", "install", "enable", "inspect contributions and logs"],
        })
    except (OSError, ValueError, PluginManifestError) as exc:
        return _error(exc)


async def _validate(args: dict[str, Any], *_unused: Any) -> str:
    resolved = await _resolve_read_path(args.get("path"), tool_name="PluginValidate")
    if isinstance(resolved, str):
        return resolved
    if not resolved.is_dir():
        return _result({"ok": False, "installable": False, "errors": ["validation path must be a plugin directory"], "warnings": []})
    return _result(_validate_path(resolved))


async def _list(args: dict[str, Any], *_unused: Any) -> str:
    try:
        project_id = str(args.get("project_id") or "").strip()
        if not project_id:
            try:
                project_id = _project_id(args)
            except PluginError:
                project_id = ""
        plugins = await get_plugin_manager().list_plugins(project_id)
        return _result({"ok": True, "projectId": project_id, "plugins": plugins})
    except (PluginError, OSError, ValueError) as exc:
        return _error(exc)


async def _install(args: dict[str, Any], *_unused: Any) -> str:
    try:
        raw = str(args.get("path") or "").strip()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            from cyrene.agent.context import active_workspace_dir
            candidate = active_workspace_dir() / candidate
        source = candidate.resolve()
        if not source.exists():
            raise PluginError("plugin source does not exist")
        if source.is_dir():
            validation = _validate_path(source)
            if not validation.get("ok"):
                return _result(validation)
            target = str((validation.get("manifest") or {}).get("id") or source.name)
        else:
            target = source.name
        reviewed = await _review("install" if not args.get("replace") else "replace", target, args)
        if reviewed is not None:
            return reviewed
        result = await get_plugin_manager().install(source, replace=bool(args.get("replace")))
        return _result(result)
    except (PluginError, PluginManifestError, OSError, ValueError) as exc:
        return _error(exc)


async def _set_enabled(args: dict[str, Any], enabled: bool) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        project_id = _project_id(args)
        action = "enable" if enabled else "disable"
        reviewed = await _review(action, f"{plugin_id}@{project_id}", args)
        if reviewed is not None:
            return reviewed
        return _result(await get_plugin_manager().set_enabled(plugin_id, project_id, enabled))
    except (PluginError, PluginManifestError, OSError, ValueError) as exc:
        return _error(exc)


async def _enable(args: dict[str, Any], *_unused: Any) -> str:
    return await _set_enabled(args, True)


async def _disable(args: dict[str, Any], *_unused: Any) -> str:
    return await _set_enabled(args, False)


async def _reload(args: dict[str, Any], *_unused: Any) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        project_id = _project_id(args)
        reviewed = await _review("reload", f"{plugin_id}@{project_id}", args)
        if reviewed is not None:
            return reviewed
        return _result(await get_plugin_manager().reload(plugin_id, project_id))
    except (PluginError, PluginManifestError, OSError, ValueError) as exc:
        return _error(exc)


async def _contributions(args: dict[str, Any], *_unused: Any) -> str:
    try:
        project_id = _project_id(args)
        contributions = await get_plugin_manager().contributions(project_id, str(args.get("point") or ""))
        return _result({"ok": True, "projectId": project_id, "contributions": contributions})
    except (PluginError, OSError, ValueError) as exc:
        return _error(exc)


async def _call(args: dict[str, Any], *_unused: Any) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        project_id = _project_id(args)
        method = str(args.get("method") or "").strip()
        if not method or method.startswith("$"):
            raise ValueError("method is required and reserved '$' methods cannot be called")
        reviewed = await _review("call", f"{plugin_id}@{project_id}:{method}", args)
        if reviewed is not None:
            return reviewed
        timeout = max(1.0, min(float(args.get("timeout_seconds") or 120), 300.0))
        result = await get_plugin_manager().call(plugin_id, project_id, method, args.get("arguments"), timeout=timeout)
        return _result({"ok": True, "pluginId": plugin_id, "projectId": project_id, "method": method, "result": result})
    except (PluginError, PluginManifestError, OSError, ValueError, TypeError) as exc:
        return _error(exc)


async def _logs(args: dict[str, Any], *_unused: Any) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        project_id = _project_id(args)
        return _result({"ok": True, "pluginId": plugin_id, "projectId": project_id, **get_plugin_manager().logs(plugin_id, project_id)})
    except (PluginError, PluginManifestError, OSError, ValueError) as exc:
        return _error(exc)


async def _delete(args: dict[str, Any], *_unused: Any) -> str:
    try:
        plugin_id = require_plugin_id(args.get("plugin_id"))
        delete_data = bool(args.get("delete_data"))
        if delete_data:
            reviewed = await request_destructive_confirmation(
                tool_name="PluginDelete",
                operation=f"删除插件 {plugin_id} 及其全部数据和日志",
                detail="The installed package, retained per-project data, and logs will be permanently removed.",
                path_hint=f"plugin:{plugin_id}",
                destructive_kind="plugin_data_delete",
            )
        else:
            reviewed = await _review("delete", plugin_id, args)
        if reviewed is not None:
            return reviewed
        return _result(await get_plugin_manager().delete(plugin_id, delete_data=delete_data))
    except (PluginError, PluginManifestError, OSError, ValueError) as exc:
        return _error(exc)


TOOL_HANDLERS: dict[str, Handler] = {
    "PluginAuthoringGuide": _guide,
    "PluginScaffold": _scaffold,
    "PluginValidate": _validate,
    "PluginList": _list,
    "PluginInstall": _install,
    "PluginEnable": _enable,
    "PluginDisable": _disable,
    "PluginReload": _reload,
    "PluginContributions": _contributions,
    "PluginCall": _call,
    "PluginLogs": _logs,
    "PluginDelete": _delete,
}

_READ_ONLY = {
    "PluginAuthoringGuide",
    "PluginValidate",
    "PluginList",
    "PluginContributions",
    "PluginLogs",
}
TOOL_METADATA: dict[str, dict[str, Any]] = {
    name: {
        "read_only": name in _READ_ONLY,
        "requires_order": name not in _READ_ONLY,
        "resource_keys": (
            ("plugin:authoring-guide",)
            if name == "PluginAuthoringGuide"
            else ("fs:{path}",)
            if name in {"PluginScaffold", "PluginValidate", "PluginInstall"}
            else ("plugins:{plugin_id}",)
            if name in {"PluginEnable", "PluginDisable", "PluginReload", "PluginCall", "PluginLogs", "PluginDelete"}
            else ("plugins:global",)
        ),
    }
    for name in TOOL_HANDLERS
}


def register_all(
    tool_defs: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    tool_metadata: dict[str, dict[str, Any]],
) -> None:
    for definition in TOOL_DEFS:
        name = str(definition["function"]["name"])
        tool_defs.append(definition)
        tool_handlers[name] = TOOL_HANDLERS[name]
        tool_metadata[name] = TOOL_METADATA[name]


__all__ = [
    "AUTHORING_GUIDE",
    "TOOL_DEFS",
    "TOOL_HANDLERS",
    "TOOL_METADATA",
    "register_all",
]

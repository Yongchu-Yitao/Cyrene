"""The isolated Python process used by one enabled plugin in one project.

The protocol is newline-delimited JSON on stdin/stdout. Plugin stdout is
redirected to stderr so ordinary prints can never corrupt the control stream.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from cyrene.plugins.manifest import load_manifest


_PROTOCOL_OUT = sys.stdout
sys.stdout = sys.stderr


def _send(payload: dict[str, Any]) -> None:
    _PROTOCOL_OUT.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    _PROTOCOL_OUT.flush()


def _json_value(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=None)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc


class PluginContext:
    def __init__(self, plugin_id: str, project_id: str, package_dir: Path, data_dir: Path):
        self.plugin_id = plugin_id
        self.project_id = project_id
        self.package_dir = package_dir
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._methods: dict[str, Callable[..., Any]] = {}
        self._contributions: list[dict[str, Any]] = []

    @property
    def contributions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._contributions]

    def register_method(self, name: str, handler: Callable[..., Any]) -> str:
        method = str(name or "").strip()
        if not method or not callable(handler):
            raise ValueError("plugin method requires a name and callable handler")
        if method in self._methods:
            raise ValueError(f"plugin method already registered: {method}")
        self._methods[method] = handler
        return method

    def register(self, point: str, descriptor: dict[str, Any]) -> dict[str, Any]:
        extension_point = str(point or "").strip()
        if not extension_point or not isinstance(descriptor, dict):
            raise ValueError("extension registration requires point and descriptor")
        contribution_id = str(descriptor.get("id") or "").strip()
        if not contribution_id:
            raise ValueError("extension contribution requires id")
        if any(
            item.get("point") == extension_point and item.get("id") == contribution_id
            for item in self._contributions
        ):
            raise ValueError(
                f"extension contribution already registered: "
                f"{extension_point}/{contribution_id}"
            )
        public: dict[str, Any] = {}
        for key, value in descriptor.items():
            if callable(value):
                method = f"contribution:{extension_point}:{contribution_id}:{key}"
                self.register_method(method, value)
                public[key] = {"$method": method}
            else:
                public[key] = _json_value(value, label=f"contribution {contribution_id}.{key}")
        record = {"point": extension_point, **public}
        self._contributions.append(record)
        return dict(record)

    def emit(self, event: str, payload: Any = None) -> None:
        _send({
            "type": "event",
            "event": str(event or "event"),
            "payload": _json_value(payload, label="event payload"),
        })

    async def call(self, method: str, args: Any) -> Any:
        handler = self._methods.get(str(method or ""))
        if handler is None:
            raise KeyError(f"plugin method not found: {method}")
        parameters = inspect.signature(handler).parameters
        result = handler() if not parameters else handler(args)
        if inspect.isawaitable(result):
            result = await result
        return _json_value(result, label=f"result from {method}")


def _load_module(entry: Path, plugin_id: str):
    module_name = "cyrene_project_plugin_" + "".join(
        character if character.isalnum() else "_" for character in plugin_id
    )
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin entry: {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def _invoke_lifecycle(module: Any, name: str, context: PluginContext) -> Any:
    handler = getattr(module, name, None)
    if not callable(handler):
        return None
    result = handler(context)
    return await result if inspect.isawaitable(result) else result


async def _run(args: argparse.Namespace) -> None:
    package_dir = Path(args.package).resolve()
    manifest = load_manifest(package_dir)
    context = PluginContext(
        manifest.id,
        str(args.project),
        package_dir,
        Path(args.data).resolve(),
    )
    for contribution in manifest.contributions:
        source = dict(contribution)
        point = str(source.pop("point"))
        context.register(point, source)
    module = None
    if manifest.backend_entry:
        module = _load_module(package_dir / manifest.backend_entry, manifest.id)
        await _invoke_lifecycle(module, "activate", context)
        fallback = getattr(module, "handle", None)
        if callable(fallback) and "handle" not in context._methods:
            context.register_method("handle", lambda payload: fallback(
                str((payload or {}).get("method") or ""),
                (payload or {}).get("args"),
                context,
            ))
    _send({"type": "ready", "contributions": context.contributions})

    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        try:
            request = json.loads(line)
            request_id = str(request.get("id") or "")
            method = str(request.get("method") or "")
            if method == "$shutdown":
                _send({"type": "response", "id": request_id, "ok": True, "result": None})
                break
            result = await context.call(method, request.get("args"))
            _send({"type": "response", "id": request_id, "ok": True, "result": result})
        except Exception as exc:
            _send({
                "type": "response",
                "id": str(locals().get("request_id") or ""),
                "ok": False,
                "error": str(exc),
                "errorType": exc.__class__.__name__,
            })
    if module is not None:
        try:
            await _invoke_lifecycle(module, "deactivate", context)
        except Exception:
            traceback.print_exc(file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--package", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    os.chdir(args.package)
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        _send({"type": "fatal", "error": str(exc), "errorType": exc.__class__.__name__})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

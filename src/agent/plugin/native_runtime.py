"""Native execution helpers for editable Plugin implementations.

The helpers in this module are derived exclusively from the active
``PluginExecution`` and its immutable ``PluginContext``.  They intentionally
do not consult the deleted tooling support layer or create a second approval
path: model-produced calls have already passed the Runtime's PreToolUse review.
Deterministic workspace and managed-attachment boundaries remain enforced here.
"""

from __future__ import annotations

import json
import inspect
import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .execution import require_plugin_execution
from .plugin import PluginContext


@runtime_checkable
class AttachmentPathResolver(Protocol):
    """Optional session service for resolving a user-visible attachment name."""

    def resolve(self, path: str) -> str | Path | None: ...


def current_plugin_context() -> PluginContext:
    """Return the context for the Plugin handler currently being executed."""

    return require_plugin_execution().context


def plugin_service(name: str) -> Any | None:
    """Read one explicitly injected session service from the active context."""

    return current_plugin_context().services.get(str(name or "").strip())


def run_context_data(context: PluginContext) -> Mapping[str, Any]:
    """Return the immutable run metadata carried by a Plugin invocation."""

    value = context.data.get("run_context")
    return value if isinstance(value, Mapping) else {}


def run_context_value(
    context: PluginContext,
    name: str,
    default: Any = "",
) -> Any:
    """Read one host value without consulting legacy ContextVars."""

    key = str(name or "").strip()
    if key in context.data:
        return context.data[key]
    return run_context_data(context).get(key, default)


async def publish_runtime_event(
    context: PluginContext,
    event: Mapping[str, Any],
) -> None:
    """Publish through the invocation's explicit event-writer service port."""

    writer = (
        context.services.get("runtime_events")
        or run_context_data(context).get("runtime_event_writer")
        or context.data.get("runtime_event_writer")
    )
    if not callable(writer):
        return
    result = writer(dict(event))
    if inspect.isawaitable(result):
        await result


def json_result(payload: Any) -> str:
    """Encode a model-facing Plugin result without ASCII escaping."""

    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False)


def workspace_root(context: PluginContext | None = None) -> Path:
    """Return the active Plugin workspace as a canonical absolute path."""

    workspace = (context or current_plugin_context()).workspace
    if workspace is None:
        raise RuntimeError("PluginContext.workspace is required for path access")
    return Path(workspace).expanduser().resolve()


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_workspace_path(
    path_str: str,
    context: PluginContext | None = None,
) -> Path:
    """Resolve one path and fail closed if it escapes the active workspace."""

    raw = str(path_str or "").strip() or "."
    root = workspace_root(context)
    candidate = Path(raw).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not _within(resolved, root):
        raise ValueError(
            "Path is outside the active Plugin workspace: "
            f"{raw} -> {resolved} (workspace: {root})"
        )
    return resolved


def _attachment_mapping(context: PluginContext) -> dict[str, str]:
    values: dict[str, str] = {}
    direct = context.data.get("attachment_paths")
    if isinstance(direct, Mapping):
        values.update(
            (str(name), str(path))
            for name, path in direct.items()
            if str(name or "").strip() and str(path or "").strip()
        )
    run_context = context.data.get("run_context")
    nested = run_context.get("attachment_paths") if isinstance(run_context, Mapping) else None
    if isinstance(nested, Mapping):
        values.update(
            (str(name), str(path))
            for name, path in nested.items()
            if str(name or "").strip() and str(path or "").strip()
        )
    service = context.services.get("attachment_paths")
    if isinstance(service, Mapping):
        values.update(
            (str(name), str(path))
            for name, path in service.items()
            if str(name or "").strip() and str(path or "").strip()
        )
    return values


def resolve_tool_path(
    path_str: str,
    context: PluginContext | None = None,
) -> Path:
    """Resolve a workspace path or a path explicitly exposed as an attachment."""

    raw = str(path_str or "").strip() or "."
    from cyrene.runtime.attachments import resolve_managed_attachment_path

    managed = resolve_managed_attachment_path(raw)
    if managed is not None:
        return managed.resolve()

    active_context = context or current_plugin_context()
    resolver = active_context.services.get("attachment_resolver")
    if isinstance(resolver, AttachmentPathResolver):
        resolved_by_service = resolver.resolve(raw)
        if resolved_by_service is not None:
            return Path(resolved_by_service).expanduser().resolve()

    mapping = _attachment_mapping(active_context)
    mapped = mapping.get(raw) or mapping.get(Path(raw).name)
    if mapped:
        return Path(mapped).expanduser().resolve()
    return resolve_workspace_path(raw, active_context)


def resolve_exportable_path(path_str: str) -> Path:
    """Resolve a path that may be exposed through Cyrene's attachment channel."""

    raw = str(path_str or "").strip()
    if not raw:
        raise ValueError("Export path cannot be empty")
    try:
        return resolve_tool_path(raw)
    except ValueError:
        pass

    from cyrene.config import DATA_DIR

    candidate = Path(raw).expanduser()
    resolved = (
        candidate
        if candidate.is_absolute()
        else workspace_root() / candidate
    ).resolve()
    data_root = Path(DATA_DIR).expanduser().resolve()
    if _within(resolved, data_root):
        return resolved
    raise ValueError(f"Path cannot be exported outside workspace/data roots: {raw}")


def is_dangerous_subshell(command: str) -> bool:
    raw = str(command or "")
    return bool(re.search(r"\$\(", raw) or "`" in raw)


def _first_command(raw: str) -> str:
    try:
        first = shlex.split(str(raw or ""), posix=True)[0]
    except (IndexError, ValueError):
        first = str(raw or "").strip().split(" ", 1)[0]
    return Path(first.lstrip("\\")).name.lower().strip("'\"")


def shell_command_requires_write_guard(command: str) -> bool:
    raw = str(command or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    if any(token in lowered for token in (
        " rm ", "rm -", "mv ", "cp ", "mkdir ", "touch ", "tee ",
        "sed -i", "truncate ", "install ", "rmdir ", "unlink ", ">",
    )):
        return True
    return _first_command(raw) in {
        "rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install",
        "rmdir", "unlink", "dd", "sed", "ln",
    }


def _redirect_targets(raw: str) -> list[str]:
    targets = [
        match.group(2)
        for match in re.finditer(r"(?:^|\s)(\d*)>>?\s*([^\s;&|]+)", raw)
        if not match.group(2).startswith("&")
    ]
    targets.extend(
        match.group(1)
        for match in re.finditer(r"(?:^|\s)&\s*>\s*([^\s;&|]+)", raw)
    )
    return targets


def _is_null_target(token: str) -> bool:
    expanded = os.path.expandvars(os.path.expanduser(str(token or "").strip("'\"")))
    return expanded.rstrip("/") == os.devnull or expanded.lower() in {"nul", "nul:"}


def guard_shell_command_workspace_write(
    command: str,
    context: PluginContext | None = None,
) -> None:
    """Fail closed when a shell write cannot be proven workspace-local."""

    raw = str(command or "").strip()
    if not raw or not shell_command_requires_write_guard(raw):
        return
    if is_dangerous_subshell(raw):
        raise ValueError(
            "Shell command substitution is not allowed for workspace writes; "
            "use explicit paths instead"
        )
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ValueError("Shell write command could not be parsed safely") from exc

    write_commands = {
        "rm", "mv", "cp", "mkdir", "touch", "tee", "truncate", "install",
        "rmdir", "unlink", "dd", "ln",
    }
    directory_commands = {"cd", "pushd"}
    separators = {"&&", "||", "|", ";"}
    active = ""
    candidates: list[str] = []
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        normalized = _first_command(value)
        if value in separators:
            active = ""
            continue
        if normalized in write_commands or normalized in directory_commands:
            active = normalized
            continue
        if value.startswith((">", ">>")):
            target = value.lstrip(">").strip()
            if target:
                candidates.append(target)
            active = ""
            continue
        if active and not value.startswith("-"):
            if active in {"cp", "mv"} or active in directory_commands or (
                value.startswith(("/", "./", "../"))
                or "/" in value
                or re.search(r"\.[A-Za-z0-9]{1,8}$", value)
            ):
                candidates.append(value)
    candidates.extend(_redirect_targets(raw))

    blocked: list[str] = []
    for token in candidates:
        if token.startswith("-") or _is_null_target(token):
            continue
        expanded = os.path.expandvars(os.path.expanduser(token))
        try:
            resolve_workspace_path(expanded, context)
        except ValueError:
            blocked.append(token)
    if blocked:
        raise ValueError(
            "Shell command writes outside the active workspace: "
            + ", ".join(blocked[:5])
        )


__all__ = [
    "AttachmentPathResolver",
    "current_plugin_context",
    "guard_shell_command_workspace_write",
    "is_dangerous_subshell",
    "json_result",
    "plugin_service",
    "publish_runtime_event",
    "resolve_exportable_path",
    "resolve_tool_path",
    "resolve_workspace_path",
    "run_context_data",
    "run_context_value",
    "shell_command_requires_write_guard",
    "workspace_root",
]

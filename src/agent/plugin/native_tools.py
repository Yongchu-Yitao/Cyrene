"""Adapt Cyrene's existing built-in tools to the new Plugin protocol.

The legacy tool modules remain the owners of their business handlers while the
Agent migration is in progress.  This module is the only compatibility
boundary: it imports their declarations, validates them, turns them into real
``Plugin`` values, and supplies the old five-argument host call from
``PluginContext.data``.  Discovery and execution do not use the legacy
Catalog, Gateway, or Executor.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from pprint import pformat
from types import MappingProxyType, ModuleType
from typing import Any

from cyrene.tool_impl import NATIVE_TOOL_MODULES

from .plugin import Plugin, PluginContext, PluginPack

NATIVE_PLUGIN_PACK_ID = "cyrene_tools"
NATIVE_PLUGIN_PACK_DESCRIPTION = "Cyrene's built-in application tools."
USER_STANDALONE_PLUGIN_NAMES = frozenset({"Glob", "Grep", "Edit"})
CORE_PLUGIN_NAMES = frozenset({"Bash", "Read", "Write"})

# These names already have native implementations in the new protocol.  The
# direct tools are in the core pack and the others are standalone Plugins in
# plugin_impl, so adding their legacy implementations again would create an
# ambiguous Registry identity.
MIGRATED_NATIVE_PLUGIN_NAMES = frozenset(
    CORE_PLUGIN_NAMES | USER_STANDALONE_PLUGIN_NAMES
)

LEGACY_BOT_CONTEXT_KEY = "bot"
LEGACY_CHAT_ID_CONTEXT_KEY = "chat_id"
LEGACY_DB_PATH_CONTEXT_KEY = "db_path"
LEGACY_NOTIFY_STATE_CONTEXT_KEY = "notify_state"
RUN_CONTEXT_CONTEXT_KEY = "run_context"
LEGACY_HOST_CONTEXT_KEYS = (
    LEGACY_BOT_CONTEXT_KEY,
    LEGACY_CHAT_ID_CONTEXT_KEY,
    LEGACY_DB_PATH_CONTEXT_KEY,
    LEGACY_NOTIFY_STATE_CONTEXT_KEY,
)

_DEFAULT_TIMEOUT_SECONDS = 180.0
_RUN_CONTEXT_UNSET = object()
_TIMEOUT_OVERRIDES = {
    # This operation intentionally waits for a person to return control.
    "browser_request_takeover": 900.0,
    # The old Executor selected 420 seconds for high-quality generation.  A
    # Plugin has one timeout, so retain the larger safe bound for every call.
    "GenerateImage": 420.0,
}


@dataclass(frozen=True, slots=True)
class NativePluginLoadFailure:
    """One invalid or unavailable module in an atomic native Plugin load."""

    module_name: str
    error: str


class NativePluginLoadError(RuntimeError):
    """Raised when the native module inventory cannot be converted completely."""

    def __init__(self, failures: Iterable[NativePluginLoadFailure]) -> None:
        normalized = tuple(failures)
        if not normalized:
            raise ValueError("NativePluginLoadError requires at least one failure")
        self.failures = normalized
        detail = "; ".join(
            f"{failure.module_name}: {failure.error}" for failure in normalized
        )
        super().__init__(f"failed to load native Plugins: {detail}")


class NativePluginContextError(ValueError):
    """Raised when a legacy handler's host values were not supplied."""


@dataclass(frozen=True, slots=True)
class BuiltinPluginSeedResult:
    """Files considered while supplementing one editable user Plugin directory."""

    directory: Path
    created: tuple[Path, ...]
    existing: tuple[Path, ...]
    tool_files: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class LegacyToolHost:
    """The four host values required by every legacy five-argument handler."""

    bot: Any
    chat_id: Any
    db_path: Any
    notify_state: Any

    @classmethod
    def from_context(cls, context: PluginContext) -> LegacyToolHost:
        data = context.data
        missing = [key for key in LEGACY_HOST_CONTEXT_KEYS if key not in data]
        if missing:
            joined = ", ".join(missing)
            required = ", ".join(LEGACY_HOST_CONTEXT_KEYS)
            raise NativePluginContextError(
                "PluginContext.data is missing legacy host key(s): "
                f"{joined}. Required keys: {required}. Values may be empty."
            )
        return cls(
            bot=data[LEGACY_BOT_CONTEXT_KEY],
            chat_id=data[LEGACY_CHAT_ID_CONTEXT_KEY],
            db_path=data[LEGACY_DB_PATH_CONTEXT_KEY],
            notify_state=data[LEGACY_NOTIFY_STATE_CONTEXT_KEY],
        )


@dataclass(frozen=True, slots=True)
class LegacyPluginHandler:
    """One explicit, inspectable adapter around a legacy business handler."""

    native_handler: Callable[..., Any]
    module_name: str
    metadata: Mapping[str, Any]

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> Any:
        host = LegacyToolHost.from_context(context)
        raw_run_context = context.data.get(
            RUN_CONTEXT_CONTEXT_KEY,
            _RUN_CONTEXT_UNSET,
        )
        if raw_run_context is not _RUN_CONTEXT_UNSET and not isinstance(
            raw_run_context,
            Mapping,
        ):
            raise NativePluginContextError(
                f"PluginContext.data[{RUN_CONTEXT_CONTEXT_KEY!r}] must be a mapping"
            )

        binding = None
        if raw_run_context is not _RUN_CONTEXT_UNSET:
            # Import and bind inside the Agent worker.  ContextVars from the
            # request thread are deliberately never copied implicitly.
            from cyrene.agent.context import bind_run_context

            binding = bind_run_context(**dict(raw_run_context))
        try:
            value = self.native_handler(
                arguments,
                host.bot,
                host.chat_id,
                host.db_path,
                host.notify_state,
            )
            if inspect.isawaitable(value):
                return await value
            return value
        finally:
            if binding is not None:
                binding.reset()


@dataclass(frozen=True, slots=True)
class _NativeDeclaration:
    module_name: str
    definition: Mapping[str, Any]
    handler: Callable[..., Any]
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BuiltinToolRegistrationProvider:
    """A legacy module whose public registrar declares several built-in tools."""

    module_name: str
    registrar_name: str
    includes_metadata: bool = False


BUILTIN_MULTI_TOOL_MODULES = ("cyrene.tool_impl.plugins",)
BUILTIN_TOOL_REGISTRATION_PROVIDERS = (
    BuiltinToolRegistrationProvider(
        "cyrene.tool_impl.map.tools",
        "register_to",
    ),
    BuiltinToolRegistrationProvider(
        "cyrene.tool_impl.code.analysis",
        "register_to",
    ),
    BuiltinToolRegistrationProvider(
        "cyrene.tool_impl.code.git",
        "register_to",
    ),
    BuiltinToolRegistrationProvider(
        "cyrene.tool_impl.code.indexer",
        "register_to",
    ),
    BuiltinToolRegistrationProvider(
        "cyrene.tool_impl.office.kit",
        "register_all",
        includes_metadata=True,
    ),
)


def _function_name(definition: Mapping[str, Any]) -> str:
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise TypeError("tool definition must contain a function object")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("tool definition is missing function.name")
    return name


def _single_declaration(
    module: ModuleType,
    module_name: str,
) -> tuple[_NativeDeclaration, ...]:
    definition = getattr(module, "TOOL_DEF", None)
    handler = getattr(module, "handler", None)
    if not isinstance(definition, Mapping):
        raise TypeError("module must export a mapping as TOOL_DEF")
    if not callable(handler):
        raise TypeError("module must export a callable as handler")
    name = _function_name(definition)
    declared_name = getattr(module, "TOOL_NAME", None)
    if declared_name is not None and str(declared_name).strip() != name:
        raise ValueError(
            f"TOOL_NAME {declared_name!r} does not match function.name {name!r}"
        )
    metadata = getattr(module, "TOOL_METADATA", {})
    if not isinstance(metadata, Mapping):
        raise TypeError("TOOL_METADATA must be a mapping")
    return (
        _NativeDeclaration(
            module_name=module_name,
            definition=definition,
            handler=handler,
            metadata=metadata,
        ),
    )


def _multiple_declarations_from_values(
    module_name: str,
    definitions: Any,
    handlers: Any,
    metadata_by_name: Any,
) -> tuple[_NativeDeclaration, ...]:
    if (
        not isinstance(definitions, Sequence)
        or isinstance(definitions, (str, bytes, bytearray))
    ):
        raise TypeError("multi-tool module must export a sequence as TOOL_DEFS")
    if not isinstance(handlers, Mapping):
        raise TypeError("multi-tool module must export a mapping as TOOL_HANDLERS")
    if not isinstance(metadata_by_name, Mapping):
        raise TypeError("TOOL_METADATA must be a mapping keyed by tool name")

    declarations: list[_NativeDeclaration] = []
    definition_names: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, Mapping):
            raise TypeError("every TOOL_DEFS entry must be a mapping")
        name = _function_name(definition)
        if name in definition_names:
            raise ValueError(f"duplicate function.name in TOOL_DEFS: {name}")
        definition_names.add(name)
        handler = handlers.get(name)
        if not callable(handler):
            raise TypeError(f"TOOL_HANDLERS is missing callable handler for {name}")
        metadata = metadata_by_name.get(name, {})
        if not isinstance(metadata, Mapping):
            raise TypeError(f"TOOL_METADATA[{name!r}] must be a mapping")
        declarations.append(
            _NativeDeclaration(
                module_name=module_name,
                definition=definition,
                handler=handler,
                metadata=metadata,
            )
        )

    extra_handlers = sorted(str(name) for name in handlers if name not in definition_names)
    if extra_handlers:
        raise ValueError(
            "TOOL_HANDLERS contains names without TOOL_DEFS entries: "
            + ", ".join(extra_handlers)
        )
    extra_metadata = sorted(
        str(name) for name in metadata_by_name if name not in definition_names
    )
    if extra_metadata:
        raise ValueError(
            "TOOL_METADATA contains names without TOOL_DEFS entries: "
            + ", ".join(extra_metadata)
        )
    if not declarations:
        raise ValueError("TOOL_DEFS cannot be empty")
    return tuple(declarations)


def _multiple_declarations(
    module: ModuleType,
    module_name: str,
) -> tuple[_NativeDeclaration, ...]:
    return _multiple_declarations_from_values(
        module_name,
        getattr(module, "TOOL_DEFS", None),
        getattr(module, "TOOL_HANDLERS", None),
        getattr(module, "TOOL_METADATA", {}),
    )


def _module_declarations(
    module: ModuleType,
    module_name: str,
) -> tuple[_NativeDeclaration, ...]:
    has_single = hasattr(module, "TOOL_DEF") or hasattr(module, "handler")
    has_multiple = hasattr(module, "TOOL_DEFS") or hasattr(module, "TOOL_HANDLERS")
    if has_single and has_multiple:
        raise ValueError(
            "module must use either TOOL_DEF/handler or TOOL_DEFS/TOOL_HANDLERS"
        )
    if has_multiple:
        return _multiple_declarations(module, module_name)
    if has_single:
        return _single_declaration(module, module_name)
    raise ValueError(
        "module exports neither TOOL_DEF/handler nor TOOL_DEFS/TOOL_HANDLERS"
    )


def _provider_declarations(
    provider: BuiltinToolRegistrationProvider,
) -> tuple[_NativeDeclaration, ...]:
    module = importlib.import_module(provider.module_name)
    registrar = getattr(module, provider.registrar_name, None)
    if not callable(registrar):
        raise TypeError(
            f"module must export callable registrar {provider.registrar_name}"
        )
    definitions: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    if provider.includes_metadata:
        registrar(definitions, handlers, metadata)
    else:
        registrar(definitions, handlers)
    return _multiple_declarations_from_values(
        provider.module_name,
        definitions,
        handlers,
        metadata,
    )


def _allow_parallel(metadata: Mapping[str, Any]) -> bool:
    if "allow_parallel" in metadata:
        value = metadata["allow_parallel"]
        if not isinstance(value, bool):
            raise TypeError("TOOL_METADATA.allow_parallel must be a boolean")
        return value
    if "requires_order" in metadata:
        value = metadata["requires_order"]
        if not isinstance(value, bool):
            raise TypeError("TOOL_METADATA.requires_order must be a boolean")
        return not value
    # Absence of scheduling metadata remains conservative.
    return False


def _timeout_seconds(name: str, metadata: Mapping[str, Any]) -> float:
    value = metadata.get(
        "timeout_seconds",
        _TIMEOUT_OVERRIDES.get(name, _DEFAULT_TIMEOUT_SECONDS),
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("TOOL_METADATA.timeout_seconds must be a number")
    timeout = float(value)
    if timeout <= 0:
        raise ValueError("TOOL_METADATA.timeout_seconds must be greater than zero")
    return timeout


def _plugin_from_declaration(declaration: _NativeDeclaration) -> Plugin:
    function = declaration.definition.get("function")
    assert isinstance(function, Mapping)
    name = _function_name(declaration.definition)
    parameters = function.get("parameters") or {
        "type": "object",
        "properties": {},
    }
    if not isinstance(parameters, Mapping):
        raise TypeError("function.parameters must be a mapping")
    metadata = MappingProxyType(deepcopy(dict(declaration.metadata)))
    adapter = LegacyPluginHandler(
        native_handler=declaration.handler,
        module_name=declaration.module_name,
        metadata=metadata,
    )
    return Plugin(
        name=name,
        description=str(function.get("description") or "").strip(),
        input_schema=deepcopy(dict(parameters)),
        # A bound async method is recognized by PluginRuntime as coroutine
        # code, so its timeout covers the actual handler instead of merely the
        # creation of a coroutine object.
        handler=adapter.__call__,
        allow_parallel=_allow_parallel(metadata),
        timeout_seconds=_timeout_seconds(name, metadata),
    )


def _load_plugins(
    module_names: Iterable[str],
    registration_providers: Iterable[BuiltinToolRegistrationProvider],
) -> tuple[Plugin, ...]:
    declarations: list[_NativeDeclaration] = []
    failures: list[NativePluginLoadFailure] = []
    for raw_module_name in module_names:
        module_name = str(raw_module_name or "").strip()
        if not module_name:
            failures.append(NativePluginLoadFailure("<empty>", "empty module name"))
            continue
        try:
            module = importlib.import_module(module_name)
            declarations.extend(_module_declarations(module, module_name))
        except Exception as exc:
            failures.append(
                NativePluginLoadFailure(module_name, f"{type(exc).__name__}: {exc}")
            )

    for provider in registration_providers:
        if not isinstance(provider, BuiltinToolRegistrationProvider):
            failures.append(
                NativePluginLoadFailure(
                    "<registration-provider>",
                    "TypeError: provider must be a BuiltinToolRegistrationProvider",
                )
            )
            continue
        try:
            declarations.extend(_provider_declarations(provider))
        except Exception as exc:
            failures.append(
                NativePluginLoadFailure(
                    provider.module_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )

    plugins: list[Plugin] = []
    owners: dict[str, str] = {}
    for declaration in declarations:
        try:
            plugin = _plugin_from_declaration(declaration)
            previous_owner = owners.get(plugin.name)
            if previous_owner is not None:
                raise ValueError(
                    f"duplicate Plugin name {plugin.name!r}; already declared by "
                    f"{previous_owner}"
                )
            owners[plugin.name] = declaration.module_name
            plugins.append(plugin)
        except Exception as exc:
            failures.append(
                NativePluginLoadFailure(
                    declaration.module_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )

    if failures:
        raise NativePluginLoadError(failures)
    return tuple(plugins)


def load_native_plugins(
    module_names: Iterable[str] = NATIVE_TOOL_MODULES,
) -> tuple[Plugin, ...]:
    """Convert the one-module-per-tool ``NATIVE_TOOL_MODULES`` inventory."""

    return _load_plugins(module_names, ())


def load_builtin_plugins(
    *,
    native_module_names: Iterable[str] = NATIVE_TOOL_MODULES,
    multi_tool_module_names: Iterable[str] = BUILTIN_MULTI_TOOL_MODULES,
    registration_providers: Iterable[BuiltinToolRegistrationProvider] = (
        BUILTIN_TOOL_REGISTRATION_PROVIDERS
    ),
) -> tuple[Plugin, ...]:
    """Convert Cyrene's complete static built-in tool inventory.

    This includes the 132 one-module native declarations plus map, code,
    PowerPoint kit, and trusted-plugin authoring declarations.  It intentionally
    does not consult the old global Catalog.
    """

    return _load_plugins(
        (*tuple(native_module_names), *tuple(multi_tool_module_names)),
        registration_providers,
    )


def create_native_plugin_pack(
    *,
    exclude_names: Iterable[str] = MIGRATED_NATIVE_PLUGIN_NAMES,
) -> PluginPack:
    """Build the complete built-in pack, omitting already migrated names."""

    plugins = load_builtin_plugins()
    excluded = frozenset(str(name).strip() for name in exclude_names)
    available = {plugin.name for plugin in plugins}
    unknown = sorted(excluded - available)
    if unknown:
        raise NativePluginLoadError(
            (
                NativePluginLoadFailure(
                    "<exclusions>",
                    "unknown migrated Plugin name(s): " + ", ".join(unknown),
                ),
            )
        )
    return PluginPack(
        id=NATIVE_PLUGIN_PACK_ID,
        description=NATIVE_PLUGIN_PACK_DESCRIPTION,
        plugins=tuple(plugin for plugin in plugins if plugin.name not in excluded),
    )


@lru_cache(maxsize=1)
def _default_builtin_plugins() -> tuple[Plugin, ...]:
    return load_builtin_plugins()


def _legacy_adapter(plugin: Plugin) -> LegacyPluginHandler:
    adapter = getattr(plugin.handler, "__self__", None)
    if not isinstance(adapter, LegacyPluginHandler):
        raise TypeError(f"built-in Plugin has no legacy adapter: {plugin.name}")
    return adapter


async def invoke_builtin_tool(
    name: str,
    source_module: str,
    arguments: dict[str, Any],
    context: PluginContext,
) -> Any:
    """Run one seeded shim's default business handler without legacy routing."""

    normalized_name = str(name or "").strip()
    plugins = {plugin.name: plugin for plugin in _default_builtin_plugins()}
    plugin = plugins.get(normalized_name)
    if plugin is None:
        raise ValueError(f"built-in Plugin implementation is missing: {normalized_name}")
    adapter = _legacy_adapter(plugin)
    if adapter.module_name != str(source_module or "").strip():
        raise ValueError(
            f"built-in Plugin source changed for {normalized_name}: "
            f"expected {source_module}, found {adapter.module_name}"
        )
    return await plugin.handler(dict(arguments), context)


def _tool_module_filename(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_") or "tool"
    digest = sha256(str(name).encode("utf-8")).hexdigest()[:10]
    return f"tool_{slug}_{digest}.py"


def _plugin_shim_source(plugin: Plugin) -> str:
    adapter = _legacy_adapter(plugin)
    definition = pformat(
        plugin.tool_definition(),
        width=100,
        sort_dicts=False,
    )
    metadata = pformat(
        deepcopy(dict(adapter.metadata)),
        width=100,
        sort_dicts=False,
    )
    return f'''"""Editable built-in Plugin for {plugin.name}."""

from __future__ import annotations

from typing import Any

from agent.plugin import Plugin, PluginContext
from agent.plugin.native_tools import invoke_builtin_tool

TOOL_DEF = {definition}
TOOL_METADATA = {metadata}


async def handler(arguments: dict[str, Any], context: PluginContext) -> Any:
    """Edit this function to replace or wrap Cyrene's default implementation."""

    return await invoke_builtin_tool(
        {plugin.name!r},
        {adapter.module_name!r},
        arguments,
        context,
    )


plugin = Plugin(
    name=TOOL_DEF["function"]["name"],
    description=TOOL_DEF["function"]["description"],
    input_schema=TOOL_DEF["function"]["parameters"],
    handler=handler,
    allow_parallel={plugin.allow_parallel!r},
    timeout_seconds={plugin.timeout_seconds!r},
)

__all__ = ["TOOL_DEF", "TOOL_METADATA", "handler", "plugin"]
'''


_PACK_INITIALIZER_SOURCE = '''"""Editable Cyrene built-in Plugin pack.

Every ``tool_*.py`` file is user-owned and loaded automatically, so a Cyrene
upgrade can add missing tools without replacing this file or existing tools.
"""

from importlib import import_module
from pathlib import Path

from agent.plugin import Plugin, PluginPack

_plugins: list[Plugin] = []
_names: set[str] = set()
for _path in sorted(Path(__file__).parent.glob("tool_*.py")):
    _module = import_module(f"{__name__}.{_path.stem}")
    _plugin = getattr(_module, "plugin", None)
    if not isinstance(_plugin, Plugin):
        raise TypeError(f"{_path.name} must export Plugin as plugin")
    if _plugin.name in _names:
        raise ValueError(f"duplicate Plugin name in cyrene_tools: {_plugin.name}")
    _names.add(_plugin.name)
    _plugins.append(_plugin)

plugin_pack = PluginPack(
    id="cyrene_tools",
    description="Cyrene's built-in application tools.",
    plugins=tuple(_plugins),
)

__all__ = ["plugin_pack"]
'''

_EDIT_PLUGIN_SOURCE = '''"""Standalone Edit Plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent.plugin import Plugin, PluginContext


def _resolve_path(raw_path: Any, context: PluginContext) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("path cannot be empty")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if context.workspace is None:
        raise ValueError("a workspace is required for relative paths")
    return (Path(context.workspace).expanduser() / path).resolve()


def _edit_file(
    path: Path,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> int:
    content = path.read_text(encoding="utf-8")
    occurrences = content.count(old_string)
    if occurrences == 0:
        raise ValueError("old_string not found")
    if occurrences > 1 and not replace_all:
        raise ValueError("old_string matched multiple times; set replace_all=true")
    updated = (
        content.replace(old_string, new_string)
        if replace_all
        else content.replace(old_string, new_string, 1)
    )
    path.write_text(updated, encoding="utf-8")
    return occurrences if replace_all else 1


async def edit(arguments: dict[str, Any], context: PluginContext) -> str:
    path = _resolve_path(arguments["path"], context)
    replacements = await asyncio.to_thread(
        _edit_file,
        path,
        str(arguments["old_string"]),
        str(arguments["new_string"]),
        bool(arguments.get("replace_all", False)),
    )
    return f"Edited {path}. Replacements: {replacements}"


plugin = Plugin(
    name="Edit",
    description="Replace an exact string in a text file.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "old_string": {"type": "string", "minLength": 1},
            "new_string": {"type": "string"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring exactly one.",
            },
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    handler=edit,
    allow_parallel=False,
    timeout_seconds=30.0,
)


__all__ = ["edit", "plugin"]
'''

_GLOB_PLUGIN_SOURCE = '''"""Standalone Glob Plugin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from agent.plugin import Plugin, PluginContext

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000
_MAX_MATCHES = 200


def _workspace(context: PluginContext) -> Path:
    if context.workspace is None:
        raise ValueError("a workspace is required")
    workspace = Path(context.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    return workspace


def _scan(workspace: Path, pattern: str) -> list[str]:
    matches: list[str] = []
    deadline = time.monotonic() + _SCAN_SECONDS
    for index, candidate in enumerate(workspace.glob(pattern), start=1):
        if index > _MAX_CANDIDATES or time.monotonic() >= deadline:
            break
        try:
            relative = candidate.relative_to(workspace)
            candidate.resolve().relative_to(workspace)
        except (OSError, ValueError):
            continue
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        matches.append(str(relative))
        if len(matches) >= _MAX_MATCHES:
            break
    return sorted(matches)


async def glob(arguments: dict[str, Any], context: PluginContext) -> str:
    workspace = _workspace(context)
    matches = await asyncio.to_thread(_scan, workspace, str(arguments["pattern"]))
    return "\\n".join(matches) if matches else "No matches."


plugin = Plugin(
    name="Glob",
    description="Find files in the workspace using a glob pattern.",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Workspace-relative glob pattern, for example **/*.py.",
            }
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    handler=glob,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["glob", "plugin"]
'''

_GREP_PLUGIN_SOURCE = '''"""Standalone Grep Plugin."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from agent.plugin import Plugin, PluginContext

_IGNORED_PARTS = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__"}
_SCAN_SECONDS = 20.0
_MAX_CANDIDATES = 50_000
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_MATCHES = 200


def _workspace(context: PluginContext) -> Path:
    if context.workspace is None:
        raise ValueError("a workspace is required")
    workspace = Path(context.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    return workspace


def _search_root(workspace: Path, raw_path: Any) -> Path:
    value = str(raw_path or ".").strip() or "."
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("path must stay within the workspace") from exc
    return resolved


def _scan(
    workspace: Path,
    search_root: Path,
    file_pattern: str,
    content_pattern: re.Pattern[str],
) -> list[str]:
    matches: list[str] = []
    deadline = time.monotonic() + _SCAN_SECONDS
    for index, candidate in enumerate(search_root.glob(file_pattern), start=1):
        if index > _MAX_CANDIDATES or time.monotonic() >= deadline:
            break
        try:
            relative = candidate.relative_to(workspace)
            candidate.resolve().relative_to(workspace)
            if any(part in _IGNORED_PARTS for part in relative.parts):
                continue
            if not candidate.is_file() or candidate.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if content_pattern.search(line):
                matches.append(f"{relative}:{line_number}:{line}")
                if len(matches) >= _MAX_MATCHES:
                    return matches
    return matches


async def grep(arguments: dict[str, Any], context: PluginContext) -> str:
    workspace = _workspace(context)
    search_root = _search_root(workspace, arguments.get("path"))
    matches = await asyncio.to_thread(
        _scan,
        workspace,
        search_root,
        str(arguments.get("glob") or "**/*"),
        re.compile(str(arguments["pattern"])),
    )
    return "\\n".join(matches) if matches else "No matches."


plugin = Plugin(
    name="Grep",
    description="Search file contents by regex pattern inside the workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative directory or file root. Defaults to the workspace.",
            },
            "glob": {
                "type": "string",
                "minLength": 1,
                "description": "Glob used to select files below path. Defaults to **/*.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    handler=grep,
    allow_parallel=True,
    timeout_seconds=30.0,
)


__all__ = ["grep", "plugin"]
'''

_STANDALONE_PLUGIN_SOURCES = MappingProxyType(
    {
        "Edit": _EDIT_PLUGIN_SOURCE,
        "Glob": _GLOB_PLUGIN_SOURCE,
        "Grep": _GREP_PLUGIN_SOURCE,
    }
)


@lru_cache(maxsize=1)
def _seed_source_files() -> tuple[tuple[Path, str, str | None], ...]:
    plugins = {plugin.name: plugin for plugin in _default_builtin_plugins()}
    if not CORE_PLUGIN_NAMES <= plugins.keys():
        missing = sorted(CORE_PLUGIN_NAMES - plugins.keys())
        raise NativePluginLoadError(
            (NativePluginLoadFailure("<core>", "missing: " + ", ".join(missing)),)
        )
    if not USER_STANDALONE_PLUGIN_NAMES <= plugins.keys():
        missing = sorted(USER_STANDALONE_PLUGIN_NAMES - plugins.keys())
        raise NativePluginLoadError(
            (
                NativePluginLoadFailure(
                    "<standalone>",
                    "missing: " + ", ".join(missing),
                ),
            )
        )

    sources: list[tuple[Path, str, str | None]] = [
        (Path(NATIVE_PLUGIN_PACK_ID) / "__init__.py", _PACK_INITIALIZER_SOURCE, None)
    ]
    for name in sorted(plugins):
        if name in CORE_PLUGIN_NAMES:
            continue
        if name in USER_STANDALONE_PLUGIN_NAMES:
            relative = Path(f"{name.casefold()}.py")
            source = _STANDALONE_PLUGIN_SOURCES[name]
        else:
            relative = Path(NATIVE_PLUGIN_PACK_ID) / _tool_module_filename(name)
            source = _plugin_shim_source(plugins[name])
        sources.append((relative, source, name))
    return tuple(sources)


def seed_builtin_plugin_directory(
    directory: str | Path | None = None,
) -> BuiltinPluginSeedResult:
    """Supplement a user Plugin directory with editable built-in source files.

    Existing paths are never opened for writing.  Re-running this function is
    therefore safe after user edits, while a newer Cyrene build can add a newly
    introduced shim simply by supplying a previously absent path.
    """

    if directory is None:
        from .registry import default_plugin_impl_directory

        root = default_plugin_impl_directory()
    else:
        root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    pack_directory = root / NATIVE_PLUGIN_PACK_ID
    pack_directory.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    existing: list[Path] = []
    tool_files: dict[str, Path] = {}
    for relative, source, tool_name in _seed_source_files():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing.append(target)
            if tool_name is not None:
                tool_files[tool_name] = target
            continue
        compile(source, str(target), "exec")
        try:
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
        except FileExistsError:
            existing.append(target)
        else:
            created.append(target)
        if tool_name is not None:
            tool_files[tool_name] = target

    return BuiltinPluginSeedResult(
        directory=root,
        created=tuple(created),
        existing=tuple(existing),
        tool_files=MappingProxyType(tool_files),
    )


__all__ = [
    "BUILTIN_MULTI_TOOL_MODULES",
    "BuiltinPluginSeedResult",
    "BUILTIN_TOOL_REGISTRATION_PROVIDERS",
    "BuiltinToolRegistrationProvider",
    "CORE_PLUGIN_NAMES",
    "LEGACY_BOT_CONTEXT_KEY",
    "LEGACY_CHAT_ID_CONTEXT_KEY",
    "LEGACY_DB_PATH_CONTEXT_KEY",
    "LEGACY_HOST_CONTEXT_KEYS",
    "LEGACY_NOTIFY_STATE_CONTEXT_KEY",
    "RUN_CONTEXT_CONTEXT_KEY",
    "USER_STANDALONE_PLUGIN_NAMES",
    "LegacyPluginHandler",
    "LegacyToolHost",
    "MIGRATED_NATIVE_PLUGIN_NAMES",
    "NATIVE_PLUGIN_PACK_DESCRIPTION",
    "NATIVE_PLUGIN_PACK_ID",
    "NativePluginContextError",
    "NativePluginLoadError",
    "NativePluginLoadFailure",
    "create_native_plugin_pack",
    "load_builtin_plugins",
    "load_native_plugins",
    "invoke_builtin_tool",
    "seed_builtin_plugin_directory",
]

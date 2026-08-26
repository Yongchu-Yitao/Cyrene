"""Adapt canonical local business declarations to the Plugin protocol."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import Any

from agent.plugin import Plugin, PluginContext, PluginPack

_HOST_KEYS = ("bot", "chat_id", "db_path", "notify_state")
_DEFAULT_TIMEOUT_SECONDS = 180.0
_TIMEOUT_OVERRIDES = {
    "browser_request_takeover": 900.0,
    "GenerateImage": 420.0,
}
_UNSET = object()


@dataclass(frozen=True, slots=True)
class RegistrationProvider:
    module_name: str
    registrar_name: str
    includes_metadata: bool = False


@dataclass(frozen=True, slots=True)
class _Declaration:
    module_name: str
    definition: Mapping[str, Any]
    implementation: Callable[..., Any]
    metadata: Mapping[str, Any]


@contextmanager
def _implementation_context(context: PluginContext):
    missing = [key for key in _HOST_KEYS if key not in context.data]
    if missing:
        raise ValueError(
            "PluginContext.data is missing implementation host key(s): "
            + ", ".join(missing)
            + ". Required keys: "
            + ", ".join(_HOST_KEYS)
            + ". Values may be empty."
        )

    raw_run_context = context.data.get("run_context", _UNSET)
    if raw_run_context is not _UNSET and not isinstance(raw_run_context, Mapping):
        raise ValueError("PluginContext.data['run_context'] must be a mapping")

    binding = None
    if raw_run_context is not _UNSET:
        from cyrene.agent.context import bind_run_context

        binding = bind_run_context(**dict(raw_run_context))
    try:
        yield tuple(context.data[key] for key in _HOST_KEYS)
    finally:
        if binding is not None:
            binding.reset()


@dataclass(frozen=True, slots=True)
class _AsyncImplementationHandler:
    implementation: Callable[..., Any]

    async def __call__(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> Any:
        with _implementation_context(context) as host:
            value = self.implementation(
                dict(arguments),
                *host,
            )
            if inspect.isawaitable(value):
                return await value
            return value


@dataclass(frozen=True, slots=True)
class _SyncImplementationHandler:
    implementation: Callable[..., Any]

    def __call__(
        self,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> Any:
        with _implementation_context(context) as host:
            value = self.implementation(dict(arguments), *host)
        if not inspect.isawaitable(value):
            return value

        async def await_with_run_context() -> Any:
            with _implementation_context(context):
                return await value

        return await_with_run_context()


def _function_name(definition: Mapping[str, Any]) -> str:
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise TypeError("tool definition must contain a function object")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("tool definition is missing function.name")
    return name


def _single_declaration(module: ModuleType) -> _Declaration:
    definition = getattr(module, "TOOL_DEF", None)
    implementation = getattr(module, "handler", None)
    metadata = getattr(module, "TOOL_METADATA", {})
    if not isinstance(definition, Mapping):
        raise TypeError(f"{module.__name__} must export mapping TOOL_DEF")
    if not callable(implementation):
        raise TypeError(f"{module.__name__} must export callable handler")
    if not isinstance(metadata, Mapping):
        raise TypeError(f"{module.__name__}.TOOL_METADATA must be a mapping")
    declared_name = getattr(module, "TOOL_NAME", None)
    name = _function_name(definition)
    if declared_name is not None and str(declared_name).strip() != name:
        raise ValueError(
            f"{module.__name__}.TOOL_NAME does not match function.name {name!r}"
        )
    return _Declaration(module.__name__, definition, implementation, metadata)


def _provider_declarations(
    module: ModuleType,
    provider: RegistrationProvider,
) -> tuple[_Declaration, ...]:
    registrar = getattr(module, provider.registrar_name, None)
    if not callable(registrar):
        raise TypeError(
            f"{module.__name__} must export callable {provider.registrar_name}"
        )
    definitions: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    if provider.includes_metadata:
        registrar(definitions, handlers, metadata)
    else:
        registrar(definitions, handlers)
    return _declarations_from_values(
        module.__name__,
        definitions,
        handlers,
        metadata,
    )


def _declarations_from_values(
    module_name: str,
    definitions: Sequence[Mapping[str, Any]],
    handlers: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[_Declaration, ...]:
    if isinstance(definitions, (str, bytes, bytearray)):
        raise TypeError(f"{module_name} registrar definitions must be a sequence")
    declarations: list[_Declaration] = []
    names: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, Mapping):
            raise TypeError(f"{module_name} registrar returned a non-mapping definition")
        name = _function_name(definition)
        if name in names:
            raise ValueError(f"{module_name} registrar duplicated {name}")
        names.add(name)
        implementation = handlers.get(name)
        if not callable(implementation):
            raise TypeError(f"{module_name} registrar has no handler for {name}")
        tool_metadata = metadata.get(name, {})
        if not isinstance(tool_metadata, Mapping):
            raise TypeError(f"{module_name} metadata for {name} must be a mapping")
        declarations.append(
            _Declaration(module_name, definition, implementation, tool_metadata)
        )
    extra_handlers = sorted(str(name) for name in handlers if name not in names)
    extra_metadata = sorted(str(name) for name in metadata if name not in names)
    if extra_handlers:
        raise ValueError(
            f"{module_name} registrar returned undeclared handler(s): "
            + ", ".join(extra_handlers)
        )
    if extra_metadata:
        raise ValueError(
            f"{module_name} registrar returned undeclared metadata: "
            + ", ".join(extra_metadata)
        )
    if not declarations:
        raise ValueError(f"{module_name} registrar returned no tools")
    return tuple(declarations)


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


def _plugin(declaration: _Declaration) -> Plugin:
    function = declaration.definition["function"]
    assert isinstance(function, Mapping)
    name = _function_name(declaration.definition)
    parameters = function.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(parameters, Mapping):
        raise TypeError(f"{name} function.parameters must be a mapping")
    metadata = MappingProxyType(deepcopy(dict(declaration.metadata)))
    implementation = declaration.implementation
    is_async = inspect.iscoroutinefunction(implementation) or inspect.iscoroutinefunction(
        getattr(implementation, "__call__", None)
    )
    adapter = (
        _AsyncImplementationHandler(implementation)
        if is_async
        else _SyncImplementationHandler(implementation)
    )
    return Plugin(
        name=name,
        description=str(function.get("description") or "").strip(),
        input_schema=deepcopy(dict(parameters)),
        handler=adapter.__call__,
        allow_parallel=_allow_parallel(metadata),
        timeout_seconds=_timeout_seconds(name, metadata),
        metadata=metadata,
    )


def create_plugin_pack(
    *,
    package_name: str,
    pack_id: str,
    description: str,
    native_module_names: Iterable[str],
    registration_providers: Iterable[RegistrationProvider],
) -> PluginPack:
    """Load declarations stored inside one editable top-level Plugin pack."""

    declarations: list[_Declaration] = []
    for relative_name in native_module_names:
        module = importlib.import_module(
            f"{package_name}.{str(relative_name).strip()}"
        )
        declarations.append(_single_declaration(module))
    for provider in registration_providers:
        module = importlib.import_module(
            f"{package_name}.{provider.module_name}"
        )
        declarations.extend(_provider_declarations(module, provider))

    plugins: list[Plugin] = []
    owners: dict[str, str] = {}
    for declaration in declarations:
        plugin = _plugin(declaration)
        previous = owners.get(plugin.name)
        if previous is not None:
            raise ValueError(
                f"duplicate Plugin {plugin.name!r}: {previous} and "
                f"{declaration.module_name}"
            )
        owners[plugin.name] = declaration.module_name
        plugins.append(plugin)
    return PluginPack(
        id=pack_id,
        description=description,
        plugins=tuple(plugins),
    )


__all__ = [
    "RegistrationProvider",
    "create_plugin_pack",
]

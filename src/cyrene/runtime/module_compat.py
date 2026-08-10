"""Lazy import aliases for Python module paths that predate the package split.

The canonical source tree stays organized by domain while imports used by
older integrations continue to resolve to the exact canonical module object.
Returning the same object is important because test suites and extensions
commonly monkeypatch module globals.
"""

from __future__ import annotations

import sys
from importlib import abc, import_module, util
from types import CodeType, ModuleType
from typing import Any


LEGACY_MODULE_ALIASES: dict[str, str] = {
    "cyrene._buildinfo": "cyrene.runtime.buildinfo",
    "cyrene.adaptive_budget": "cyrene.agent.adaptive_budget",
    "cyrene.app_paths": "cyrene.runtime.paths",
    "cyrene.app_use": "cyrene.tooling.backends.app_use",
    "cyrene.attachments": "cyrene.runtime.attachments",
    "cyrene.backup": "cyrene.runtime.backup",
    "cyrene.behavior_learning": "cyrene.learning.engine",
    "cyrene.budget": "cyrene.agent.budget",
    "cyrene.cc_bridge": "cyrene.tooling.backends.claude_code_bridge",
    "cyrene.cc_learner": "cyrene.learning.claude_code",
    "cyrene.cc_terminal": "cyrene.tooling.backends.claude_code_terminal",
    "cyrene.config_store": "cyrene.runtime.config_store",
    "cyrene.context_debug": "cyrene.observability.context_debug",
    "cyrene.context_trace": "cyrene.observability.context_trace",
    "cyrene.conversations": "cyrene.runtime.memory.conversations",
    "cyrene.db": "cyrene.runtime.database",
    "cyrene.debug": "cyrene.observability.debug",
    "cyrene.entities": "cyrene.tool_impl.entity.store",
    "cyrene.inbox": "cyrene.runtime.inbox",
    "cyrene.integration_settings": "cyrene.runtime.integration_settings",
    "cyrene.io_utils": "cyrene.runtime.io",
    "cyrene.llm": "cyrene.model_runtime.messages",
    "cyrene.local_cli": "cyrene.runtime.host",
    "cyrene.mcp_manager": "cyrene.tooling.backends.mcp_manager",
    "cyrene.model_prices": "cyrene.model_runtime.pricing",
    "cyrene.modules.deep_research": "cyrene.agent.research",
    "cyrene.notifications": "cyrene.runtime.notifications",
    "cyrene.onboarding": "cyrene.runtime.onboarding",
    "cyrene.pattern": "cyrene.learning.facade",
    "cyrene.report_export": "cyrene.workbench.report_export",
    "cyrene.runtime_lifecycle": "cyrene.runtime.lifecycle",
    "cyrene.schedule_spec": "cyrene.runtime.schedule_spec",
    "cyrene.scheduler": "cyrene.runtime.scheduler",
    "cyrene.search": "cyrene.tooling.backends.search",
    "cyrene.searxng_manager": "cyrene.tooling.backends.searxng_manager",
    "cyrene.secret_redaction": "cyrene.runtime.secret_redaction",
    "cyrene.settings_store": "cyrene.runtime.settings_store",
    "cyrene.setup": "cyrene.runtime.setup",
    "cyrene.shell_runtime": "cyrene.tooling.backends.shell_runtime",
    "cyrene.shell_wake": "cyrene.runtime.shell_wake",
    "cyrene.shells": "cyrene.tooling.backends.shells",
    "cyrene.short_term": "cyrene.runtime.memory.short_term",
    "cyrene.simplexng_child": "cyrene.tooling.backends.simplexng_child",
    "cyrene.skills_registry": "cyrene.learning.skills",
    "cyrene.soul": "cyrene.runtime.memory.soul",
    "cyrene.task_lifecycle": "cyrene.runtime.task_lifecycle",
    "cyrene.updater": "cyrene.runtime.updater",
    "cyrene.version": "cyrene.runtime.version",
    "cyrene.workbench_chat_service": "cyrene.workbench.chat",
    "cyrene.workbench_context": "cyrene.workbench.context",
    "cyrene.workbench_inbox": "cyrene.workbench.inbox",
    "cyrene.workbench_knowledge_service": "cyrene.workbench.knowledge",
    "cyrene.workbench_memory_service": "cyrene.workbench.memory",
    "cyrene.workbench_runtime": "cyrene.workbench.runtime",
    "cyrene.workbench_store": "cyrene.workbench.store",
    "cyrene.workbench_task_context": "cyrene.workbench.task_context",
    "cyrene.workspace_changes": "cyrene.workbench.workspace_changes",
}

_LEGACY_NAMESPACE_PACKAGES = frozenset({"cyrene.modules"})
_EXECUTABLE_ALIASES = frozenset(
    {
        "cyrene.context_debug",
        "cyrene.local_cli",
        "cyrene.simplexng_child",
    }
)
_MODULE_METADATA = (
    "__name__",
    "__loader__",
    "__package__",
    "__spec__",
    "__file__",
    "__cached__",
)


def _attach_to_parent(alias: str, module: ModuleType) -> None:
    parent_name, separator, child_name = alias.rpartition(".")
    if separator and parent_name in sys.modules:
        setattr(sys.modules[parent_name], child_name, module)


def alias_module(alias: str, target: str) -> ModuleType:
    """Make ``alias`` resolve to the exact same module object as ``target``."""
    module = import_module(target)
    sys.modules[alias] = module
    _attach_to_parent(alias, module)
    return module


class _LegacyAliasLoader(abc.Loader):
    def __init__(self, alias: str, target: str) -> None:
        self.alias = alias
        self.target = target
        self._metadata: dict[str, Any] = {}

    def create_module(self, spec: Any) -> ModuleType:
        module = import_module(self.target)
        self._metadata = {
            name: getattr(module, name)
            for name in _MODULE_METADATA
            if hasattr(module, name)
        }
        return module

    def exec_module(self, module: ModuleType) -> None:
        # Import machinery temporarily assigns the alias spec to the canonical
        # module returned by create_module(). Restore its true identity.
        for name, value in self._metadata.items():
            setattr(module, name, value)
        sys.modules[self.alias] = module
        _attach_to_parent(self.alias, module)

    def get_filename(self, fullname: str) -> str:
        return f"<legacy-module-alias {fullname} -> {self.target}>"

    def get_code(self, fullname: str) -> CodeType:
        # runpy (``python -m``) asks for code instead of create_module().
        if fullname in _EXECUTABLE_ALIASES:
            source = (
                f"from {self.target} import main as _legacy_main\n"
                "_legacy_main()\n"
            )
        else:
            source = f"from {self.target} import *\n"
        return compile(source, self.get_filename(fullname), "exec")


class _LegacyNamespaceLoader(abc.Loader):
    def exec_module(self, module: ModuleType) -> None:
        return None


class _LegacyAliasFinder(abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: ModuleType | None = None,
    ) -> Any:
        if fullname in _LEGACY_NAMESPACE_PACKAGES:
            return util.spec_from_loader(
                fullname,
                _LegacyNamespaceLoader(),
                is_package=True,
            )
        canonical = LEGACY_MODULE_ALIASES.get(fullname)
        if canonical is None:
            return None
        return util.spec_from_loader(
            fullname,
            _LegacyAliasLoader(fullname, canonical),
        )


_FINDER = _LegacyAliasFinder()


def install_legacy_module_aliases() -> None:
    """Install the alias finder once without importing canonical targets."""
    if not any(finder is _FINDER for finder in sys.meta_path):
        sys.meta_path.insert(0, _FINDER)


__all__ = [
    "LEGACY_MODULE_ALIASES",
    "alias_module",
    "install_legacy_module_aliases",
]

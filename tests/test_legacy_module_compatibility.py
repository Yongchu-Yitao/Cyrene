from __future__ import annotations

import importlib
from pathlib import Path

import pytest


_LEGACY_MODULE_ALIASES = {
    "cyrene._buildinfo": "cyrene.runtime.buildinfo",
    "cyrene.adaptive_budget": "cyrene.agent.adaptive_budget",
    "cyrene.app_paths": "cyrene.runtime.paths",
    "cyrene.app_use": "cyrene.tooling.backends.app_use",
    "cyrene.attachments": "cyrene.runtime.attachments",
    "cyrene.backup": "cyrene.runtime.backup",
    "cyrene.behavior_learning": "cyrene.learning.engine",
    "cyrene.budget": "cyrene.agent.budget",
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
    "cyrene.workbench_memory_service": "cyrene.workbench.memory",
    "cyrene.workbench_runtime": "cyrene.workbench.runtime",
    "cyrene.workbench_store": "cyrene.workbench.store",
    "cyrene.workbench_task_context": "cyrene.workbench.task_context",
    "cyrene.workspace_changes": "cyrene.workbench.workspace_changes",
    "webui.workbench_chat_runs": "cyrene.workbench.chat_runs",
    "webui.workbench_goal_loop": "cyrene.workbench.goal_loop",
    "webui.workbench_notifications": "cyrene.workbench.notifications",
}


@pytest.mark.parametrize(
    ("legacy_name", "target_name"),
    sorted(_LEGACY_MODULE_ALIASES.items()),
)
def test_legacy_module_is_the_canonical_module(
    legacy_name: str,
    target_name: str,
):
    legacy = importlib.import_module(legacy_name)
    target = importlib.import_module(target_name)

    assert legacy is target


def test_monkeypatch_through_legacy_module_changes_canonical_module(monkeypatch):
    legacy = importlib.import_module("cyrene.db")
    target = importlib.import_module("cyrene.runtime.database")
    sentinel = object()

    monkeypatch.setattr(legacy, "_compatibility_probe", sentinel, raising=False)

    assert target._compatibility_probe is sentinel


def test_legacy_alias_preserves_canonical_module_metadata():
    legacy = importlib.import_module("cyrene.db")

    assert legacy.__name__ == "cyrene.runtime.database"
    assert legacy.__spec__.name == "cyrene.runtime.database"


def test_legacy_modules_do_not_require_top_level_shim_files():
    package_dir = Path(__file__).resolve().parents[1] / "src" / "cyrene"
    physical_launcher_exceptions = {"cyrene.local_cli"}

    for legacy_name in _LEGACY_MODULE_ALIASES:
        if legacy_name in physical_launcher_exceptions:
            continue
        relative_parts = legacy_name.split(".")[1:]
        module_path = package_dir.joinpath(*relative_parts).with_suffix(".py")
        assert not module_path.exists(), legacy_name

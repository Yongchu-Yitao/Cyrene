"""Mechanical checks for the package boundaries established by the refactor."""

from __future__ import annotations

import ast
from collections import Counter
import json
import subprocess
import sys
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PRIVATE_IMPORT_BUDGET = {
    "cyrene.agent": 7,
    "cyrene.agent.agent": 1,
    "cyrene.agent.budget": 1,
    "cyrene.agent.coordinator": 4,
    "cyrene.agent.message": 15,
    "cyrene.agent.planning": 1,
    "cyrene.agent.prompts": 20,
    "cyrene.agent.replies": 9,
    "cyrene.agent.round": 1,
    "cyrene.agent.session": 22,
    "cyrene.agent.state": 55,
    "cyrene.learning.engine": 2,
    "cyrene.subagent": 1,
    "cyrene.tool_impl.knowledge.list_library_items": 1,
    "cyrene.tooling.executor": 2,
    "cyrene.workbench.inbox": 2,
    "cyrene.workbench.memory": 1,
    "cyrene.workbench.task_context": 1,
}

CYRENE_TOP_LEVEL_DIRECTORIES = {
    "agent",
    "channels",
    "knowledge",
    "learning",
    "model_runtime",
    "observability",
    "runtime",
    "tool_impl",
    "tooling",
    "workbench",
}
CYRENE_TOP_LEVEL_FILES = {
    "__init__.py",
    "__main__.py",
    "browser.py",
    "call_llm.py",
    "cli.py",
    "cli_chat.py",
    "config.py",
    "local_cli.py",
    "memory.py",
    "subagent.py",
    "tools.py",
}


def test_cyrene_top_level_matches_final_architecture() -> None:
    package_dir = SRC_ROOT / "cyrene"
    directories = {
        path.name
        for path in package_dir.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    files = {
        path.name
        for path in package_dir.iterdir()
        if path.is_file() and path.suffix == ".py"
    }

    assert directories == CYRENE_TOP_LEVEL_DIRECTORIES
    assert files == CYRENE_TOP_LEVEL_FILES


@pytest.mark.parametrize(
    "package",
    ("cyrene.agent", "cyrene.learning", "cyrene.tooling"),
)
def test_public_package_facades_are_lazy(package: str) -> None:
    """Importing a facade must not initialize its implementation graph."""
    code = (
        "import json, sys\n"
        f"import {package}\n"
        f"print(json.dumps(sorted(name for name in sys.modules "
        f"if name == {package!r} or name.startswith({package!r} + '.'))))\n"
    )

    output = subprocess.check_output(
        [sys.executable, "-c", code],
        cwd=SRC_ROOT.parent,
        text=True,
    )

    assert json.loads(output) == [package]


def _source_modules() -> dict[str, tuple[Path, bool]]:
    modules: dict[str, tuple[Path, bool]] = {}
    for path in SRC_ROOT.rglob("*.py"):
        parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        if parts:
            modules[".".join(parts)] = (path, is_package)
    return modules


def _known_module(name: str, known: set[str]) -> str | None:
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return None


def _static_import_graph() -> dict[str, set[str]]:
    modules = _source_modules()
    known = set(modules)
    graph = {name: set() for name in known}

    for importer, (path, is_package) in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def add_import(name: str) -> None:
            imported = _known_module(name, known)
            if imported is not None and imported != importer:
                graph[importer].add(imported)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add_import(alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue

            if node.level:
                package = importer if is_package else importer.rpartition(".")[0]
                package_parts = package.split(".") if package else []
                parents = node.level - 1
                anchor = (
                    package_parts[: len(package_parts) - parents]
                    if parents <= len(package_parts)
                    else []
                )
                module_parts = (node.module or "").split(".") if node.module else []
                base = ".".join(anchor + module_parts)
            else:
                base = node.module or ""

            add_import(base)
            for alias in node.names:
                if alias.name != "*":
                    add_import(".".join(part for part in (base, alias.name) if part))

    return graph


def _multi_module_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return strongly connected components containing more than one module."""
    indexes: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    next_index = 0

    def visit(module: str) -> None:
        nonlocal next_index
        indexes[module] = low_links[module] = next_index
        next_index += 1
        stack.append(module)
        on_stack.add(module)

        for dependency in graph[module]:
            if dependency not in indexes:
                visit(dependency)
                low_links[module] = min(low_links[module], low_links[dependency])
            elif dependency in on_stack:
                low_links[module] = min(low_links[module], indexes[dependency])

        if low_links[module] != indexes[module]:
            return

        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module:
                break
        if len(component) > 1:
            cycles.append(sorted(component))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return sorted(cycles)


def test_source_tree_has_no_static_import_cycles() -> None:
    assert _multi_module_cycles(_static_import_graph()) == []


def _private_import_counts() -> Counter[str]:
    """Count explicit private imports in the in-scope application packages."""
    counts: Counter[str] = Counter()
    for package in ("cyrene", "webui"):
        for path in (SRC_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    not isinstance(node, ast.ImportFrom)
                    or node.level
                    or not node.module
                    or not node.module.startswith(("cyrene.", "webui."))
                ):
                    continue
                counts[node.module] += sum(
                    alias.name.startswith("_") and alias.name != "__all__"
                    for alias in node.names
                )
    return +counts


def test_private_import_budget_can_only_decrease() -> None:
    """Block new private dependencies while the compatibility debt is removed."""
    current = _private_import_counts()
    unexpected_sources = sorted(set(current) - set(PRIVATE_IMPORT_BUDGET))
    over_budget = {
        source: {"current": count, "budget": PRIVATE_IMPORT_BUDGET[source]}
        for source, count in current.items()
        if source in PRIVATE_IMPORT_BUDGET
        and count > PRIVATE_IMPORT_BUDGET[source]
    }

    assert unexpected_sources == []
    assert over_budget == {}
    assert sum(current.values()) <= sum(PRIVATE_IMPORT_BUDGET.values()) == 146


def test_agent_facade_private_export_budget_can_only_decrease() -> None:
    """Do not add more historical private names to the lazy agent facade."""
    from cyrene import agent

    exported_names = [
        name
        for names in agent._EXPORT_GROUPS.values()
        for name in names
    ]
    private_names = [name for name in exported_names if name.startswith("_")]

    assert len(private_names) <= 120

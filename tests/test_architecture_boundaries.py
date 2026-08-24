"""Mechanical checks for the package boundaries established by the refactor."""

from __future__ import annotations

import ast
from collections import Counter
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
REPOSITORY_ROOT = SRC_ROOT.parent
COMPLEXITY_BASELINE = REPOSITORY_ROOT / "project-notes" / "architecture-complexity-baseline.json"
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
    "cyrene.agent.state": 57,
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
    "agent_runtime",
    "channels",
    "custom_tools",
    "extensions",
    "hooks",
    "knowledge",
    "learning",
    "media",
    "model_runtime",
    "observability",
    "office",
    "plugins",
    "runtime",
    "terminal",
    "tool_impl",
    "tooling",
    "voice",
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

# Historical namespace-wide imports are migration debt. The set may shrink but
# no new module may join it.
IMPORT_STAR_ALLOWLIST = {
    "src/route/agent/browser.py",
    "src/route/agent/sessions.py",
    "src/route/backup.py",
    "src/route/learning.py",
    "src/route/memory.py",
    "src/route/notifications.py",
    "src/route/search.py",
    "src/route/system/shell.py",
    "src/route/system/updates.py",
    "src/route/tasks.py",
    "src/route/usage.py",
}

DYNAMIC_NAMESPACE_ALLOWLIST: set[str] = set()
JAVASCRIPT_IMPORT_STAR_ALLOWLIST = {
    "src/webui/build-jsx.mjs",
}
JAVASCRIPT_DYNAMIC_NAMESPACE_ALLOWLIST = {
    "electron/agent-cursor.js",
}
JAVASCRIPT_SERVICE_REGISTRY_ALLOWLIST = {
    "src/webui/frontend/entry/bootstrap.jsx",
    "src/webui/frontend/platform/api.jsx",
    "src/webui/frontend/platform/data-store.jsx",
    "src/webui/frontend/shared/i18n/translations.jsx",
}


def test_cyrene_top_level_matches_final_architecture() -> None:
    package_dir = SRC_ROOT / "cyrene"
    directories = {path.name for path in package_dir.iterdir() if path.is_dir() and path.name != "__pycache__"}
    files = {path.name for path in package_dir.iterdir() if path.is_file() and path.suffix == ".py"}

    assert directories == CYRENE_TOP_LEVEL_DIRECTORIES
    assert files == CYRENE_TOP_LEVEL_FILES


def _application_python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _relative_source(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def test_namespace_wide_import_budget_can_only_decrease() -> None:
    current: set[str] = set()
    for path in _application_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names) for node in ast.walk(tree)):
            current.add(_relative_source(path))

    assert current <= IMPORT_STAR_ALLOWLIST


def test_dynamic_namespace_injection_budget_can_only_decrease() -> None:
    current: set[str] = set()
    for path in _application_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        namespace_aliases = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            if isinstance(target, ast.Name)
            and (
                isinstance(node.value, ast.Attribute)
                and node.value.attr in {"__globals__", "__dict__"}
                or isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "globals"
            )
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not isinstance(function, ast.Attribute) or function.attr != "update":
                continue
            receiver = function.value
            direct_globals = (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and receiver.func.id == "globals"
            )
            private_namespace = (
                isinstance(receiver, ast.Attribute)
                and receiver.attr in {"__globals__", "__dict__"}
            )
            namespace_alias = (
                isinstance(receiver, ast.Name)
                and receiver.id in namespace_aliases
            )
            if direct_globals or private_namespace or namespace_alias:
                current.add(_relative_source(path))

    assert current <= DYNAMIC_NAMESPACE_ALLOWLIST


def _application_javascript_files() -> list[Path]:
    roots = (
        REPOSITORY_ROOT / "electron",
        REPOSITORY_ROOT / "src" / "webui" / "build",
        REPOSITORY_ROOT / "src" / "webui" / "frontend",
    )
    files = [REPOSITORY_ROOT / "src" / "webui" / "build-jsx.mjs"]
    for root in roots:
        for suffix in ("*.js", "*.jsx", "*.mjs"):
            files.extend(
                path
                for path in root.rglob(suffix)
                if "node_modules" not in path.relative_to(REPOSITORY_ROOT).parts
            )
    return sorted(set(files))


def test_javascript_namespace_import_budget_can_only_decrease() -> None:
    current = {
        _relative_source(path)
        for path in _application_javascript_files()
        if re.search(r"^\s*(?:import\s+\*\s+as|export\s+\*)", path.read_text(encoding="utf-8"), re.MULTILINE)
    }

    assert current <= JAVASCRIPT_IMPORT_STAR_ALLOWLIST


def test_javascript_dynamic_namespace_budget_can_only_decrease() -> None:
    dynamic_namespace = re.compile(
        r"Object\.assign\(\s*(?:globalThis|window)\s*,"
        r"|(?:globalThis|window)\s*\[[^\]\"']+\]\s*="
    )
    current = {
        _relative_source(path)
        for path in _application_javascript_files()
        if dynamic_namespace.search(path.read_text(encoding="utf-8"))
    }

    assert current <= JAVASCRIPT_DYNAMIC_NAMESPACE_ALLOWLIST


def test_javascript_service_registry_is_confined_to_composition_modules() -> None:
    frontend_root = REPOSITORY_ROOT / "src" / "webui" / "frontend"
    current = {
        _relative_source(path)
        for path in frontend_root.rglob("*.jsx")
        if 'window.CyreneUI.require(' in path.read_text(encoding="utf-8")
    }

    assert current <= JAVASCRIPT_SERVICE_REGISTRY_ALLOWLIST


def _large_python_functions(threshold: int) -> dict[str, int]:
    functions: dict[str, int] = {}

    class Collector(ast.NodeVisitor):
        def __init__(self, path: Path):
            self.path = path
            self.scope: list[str] = []

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            self.scope.append(node.name)
            lines = int(node.end_lineno or node.lineno) - node.lineno + 1
            if lines >= threshold:
                key = f"{_relative_source(self.path)}::{'.'.join(self.scope)}"
                functions[key] = lines
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

    for path in _application_python_files():
        collector = Collector(path)
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return functions


def test_large_python_function_budget_can_only_decrease() -> None:
    baseline = json.loads(COMPLEXITY_BASELINE.read_text(encoding="utf-8"))
    limits = baseline["large_functions"]
    current = _large_python_functions(int(baseline["threshold_lines"]))

    new_functions = sorted(set(current) - set(limits))
    grown_functions = {name: {"current": lines, "budget": limits[name]} for name, lines in current.items() if name in limits and lines > int(limits[name])}

    assert new_functions == []
    assert grown_functions == {}


def _private_cross_package_imports() -> set[str]:
    imports: set[str] = set()
    for path in _application_python_files():
        relative = _relative_source(path)
        parts = path.relative_to(SRC_ROOT).parts
        importer_package = ".".join(parts[:2]) if len(parts) > 1 else parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            imported_parts = node.module.split(".")
            imported_package = ".".join(imported_parts[:2])
            if importer_package == imported_package:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and alias.name != "__all__":
                    imports.add(f"{relative}::{node.module}::{alias.name}")
    return imports


def test_private_cross_package_imports_can_only_decrease() -> None:
    baseline = json.loads(COMPLEXITY_BASELINE.read_text(encoding="utf-8"))
    allowed = set(baseline["private_cross_package_imports"])

    assert _private_cross_package_imports() <= allowed


@pytest.mark.parametrize(
    "package",
    ("cyrene.agent", "cyrene.learning", "cyrene.tooling"),
)
def test_public_package_facades_are_lazy(package: str) -> None:
    """Importing a facade must not initialize its implementation graph."""
    code = f"import json, sys\nimport {package}\nprint(json.dumps(sorted(name for name in sys.modules if name == {package!r} or name.startswith({package!r} + '.'))))\n"

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
                anchor = package_parts[: len(package_parts) - parents] if parents <= len(package_parts) else []
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


def test_tool_executor_import_does_not_require_registry_import_order() -> None:
    """Remote tool registration imports progress reporting during catalog init."""
    code = "from cyrene.tooling.executor import _execute_tool, publish_tool_progress\nprint(_execute_tool.__name__, publish_tool_progress.__name__)\n"

    output = subprocess.check_output(
        [sys.executable, "-c", code],
        cwd=SRC_ROOT.parent,
        text=True,
    )

    assert output.strip() == "_execute_tool publish_tool_progress"


def _private_import_counts() -> Counter[str]:
    """Count explicit private imports in the in-scope application packages."""
    counts: Counter[str] = Counter()
    for package in ("cyrene", "webui"):
        for path in (SRC_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level or not node.module or not node.module.startswith(("cyrene.", "webui.")):
                    continue
                counts[node.module] += sum(alias.name.startswith("_") and alias.name != "__all__" for alias in node.names)
    return +counts


def test_private_import_budget_can_only_decrease() -> None:
    """Block new private dependencies while the compatibility debt is removed."""
    current = _private_import_counts()
    unexpected_sources = sorted(set(current) - set(PRIVATE_IMPORT_BUDGET))
    over_budget = {
        source: {"current": count, "budget": PRIVATE_IMPORT_BUDGET[source]}
        for source, count in current.items()
        if source in PRIVATE_IMPORT_BUDGET and count > PRIVATE_IMPORT_BUDGET[source]
    }

    assert unexpected_sources == []
    assert over_budget == {}
    assert sum(current.values()) <= sum(PRIVATE_IMPORT_BUDGET.values()) == 148


def test_agent_facade_private_export_budget_can_only_decrease() -> None:
    """Do not add more historical private names to the lazy agent facade."""
    from cyrene import agent

    exported_names = [name for names in agent._EXPORT_GROUPS.values() for name in names]
    private_names = [name for name in exported_names if name.startswith("_")]

    assert len(private_names) <= 120

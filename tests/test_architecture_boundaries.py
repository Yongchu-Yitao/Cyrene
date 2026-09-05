"""Mechanical checks for the package boundaries established by the refactor."""

from __future__ import annotations

import ast
from collections import Counter
import json
import re
import subprocess
import sys
from pathlib import Path



SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
REPOSITORY_ROOT = SRC_ROOT.parent
COMPLEXITY_BASELINE = REPOSITORY_ROOT / "project-notes" / "architecture-complexity-baseline.json"
PRIVATE_IMPORT_TOTAL_BUDGET = 148

CYRENE_TOP_LEVEL_DIRECTORIES = {
    "agents",
    "core",
    "model",
    "observability",
    "platform",
    "plugins",
    "workbench",
}
CYRENE_TOP_LEVEL_FILES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "cli_chat.py",
    "config.py",
    "local_cli.py",
    "localization.py",
    "path_policy.py",
    "simplexng_child.py",
}

# Existing late-bound configuration reads are isolated migration debt. Keep the
# boundary closed to every other platform import.
CORE_PLATFORM_IMPORT_ALLOWLIST = {
    "src/cyrene/core/session.py: cyrene.platform.settings_store",
    "src/cyrene/core/plugin/core_impl/bash.py: cyrene.platform.subprocess_environment",
    "src/cyrene/core/plugin/core_impl/permission_boundaries.py: cyrene.platform.paths",
}

WORKBENCH_DOMAIN_DIRECTORIES = {
    "application",
    "artifacts",
    "chat",
    "control",
    "core_adapter",
    "http",
    "persistence",
    "projects",
    "sessions",
    "ui",
    "webui",
    "workspaces",
}

# Historical namespace-wide imports are migration debt. The set may shrink but
# no new module may join it.
IMPORT_STAR_ALLOWLIST = {
    "src/cyrene/workbench/http/agent/sessions.py",
    "src/cyrene/workbench/http/backup.py",
    "src/cyrene/workbench/http/notifications.py",
    "src/cyrene/workbench/http/system/shell.py",
    "src/cyrene/workbench/http/system/updates.py",
    "src/cyrene/workbench/http/usage.py",
}

DYNAMIC_NAMESPACE_ALLOWLIST: set[str] = set()
JAVASCRIPT_IMPORT_STAR_ALLOWLIST = {
    "src/cyrene/workbench/webui/build-jsx.mjs",
}
JAVASCRIPT_DYNAMIC_NAMESPACE_ALLOWLIST = {
    "electron/agent-cursor.js",
}
JAVASCRIPT_SERVICE_REGISTRY_ALLOWLIST = {
    "src/cyrene/workbench/webui/frontend/entry/bootstrap.jsx",
    "src/cyrene/workbench/webui/frontend/platform/api.jsx",
    "src/cyrene/workbench/webui/frontend/platform/data-store.jsx",
    # Feature modules compose registered services for their own surfaces.
    "src/cyrene/workbench/webui/frontend/features/settings/custom-plugins.jsx",
    "src/cyrene/workbench/webui/frontend/features/settings/plugin-center-add.jsx",
    "src/cyrene/workbench/webui/frontend/shared/i18n/translations.jsx",
    "src/cyrene/workbench/webui/frontend/workbench-i18n.jsx",
}


def test_cyrene_top_level_matches_final_architecture() -> None:
    package_dir = SRC_ROOT / "cyrene"
    directories = {path.name for path in package_dir.iterdir() if path.is_dir() and path.name != "__pycache__"}
    files = {path.name for path in package_dir.iterdir() if path.is_file() and path.suffix == ".py"}

    assert directories == CYRENE_TOP_LEVEL_DIRECTORIES
    assert files == CYRENE_TOP_LEVEL_FILES


def test_workbench_root_contains_only_domain_packages() -> None:
    workbench_dir = SRC_ROOT / "cyrene" / "workbench"
    directories = {
        path.name
        for path in workbench_dir.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    python_files = {
        path.name for path in workbench_dir.iterdir() if path.suffix == ".py"
    }

    assert directories == WORKBENCH_DOMAIN_DIRECTORIES
    assert python_files == {"__init__.py"}


def test_core_does_not_depend_on_product_or_workbench_layers() -> None:
    forbidden_roots = {
        "agent",
        "route",
        "webui",
        "fastapi",
        "starlette",
        "cyrene.plugins",
        "cyrene.workbench",
        "cyrene.platform",
        "cyrene.model",
        "cyrene.observability",
    }
    violations: list[str] = []
    core_root = SRC_ROOT / "cyrene" / "core"
    for path in core_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if any(
                    module == root or module.startswith(f"{root}.")
                    for root in forbidden_roots
                ):
                    violations.append(f"{_relative_source(path)}: {module}")

    assert set(violations) <= CORE_PLATFORM_IMPORT_ALLOWLIST


def _application_python_files() -> list[Path]:
    return sorted(
        path
        for path in SRC_ROOT.rglob("*.py")
        if "tests" not in path.relative_to(SRC_ROOT).parts
    )


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
        REPOSITORY_ROOT / "src" / "cyrene" / "workbench" / "webui" / "build",
        REPOSITORY_ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend",
    )
    files = [REPOSITORY_ROOT / "src" / "cyrene" / "workbench" / "webui" / "build-jsx.mjs"]
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
    frontend_root = REPOSITORY_ROOT / "src" / "cyrene" / "workbench" / "webui" / "frontend"
    current = {
        _relative_source(path)
        for path in frontend_root.rglob("*.jsx")
        if 'window.CyreneUI.require(' in path.read_text(encoding="utf-8")
    }

    assert current <= JAVASCRIPT_SERVICE_REGISTRY_ALLOWLIST


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


def _source_modules() -> dict[str, tuple[Path, bool]]:
    modules: dict[str, tuple[Path, bool]] = {}
    for path in SRC_ROOT.rglob("*.py"):
        if "tests" in path.relative_to(SRC_ROOT).parts:
            continue
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

        # Function-local imports are deferred dependency lookups, not static
        # module initialization edges. Only module-scope imports participate
        # in this cycle check.
        for node in tree.body:
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


def test_plugin_execution_import_does_not_require_registry_import_order() -> None:
    """Plugin execution services must not depend on registry import order."""
    code = "from cyrene.core.plugin.execution import invoke_plugin, publish_plugin_progress\nprint(invoke_plugin.__name__, publish_plugin_progress.__name__)\n"

    output = subprocess.check_output(
        [sys.executable, "-c", code],
        cwd=SRC_ROOT.parent,
        text=True,
    )

    assert output.strip() == "invoke_plugin publish_plugin_progress"


def _private_import_counts() -> Counter[str]:
    """Count explicit private imports in the in-scope application packages."""
    counts: Counter[str] = Counter()
    for package in ("cyrene", "webui"):
        for path in (SRC_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level or not node.module or not node.module.startswith(("cyrene.", "cyrene.workbench.webui.")):
                    continue
                counts[node.module] += sum(alias.name.startswith("_") and alias.name != "__all__" for alias in node.names)
    return +counts


def test_private_import_budget_can_only_decrease() -> None:
    """Block new private cross-package dependencies."""
    current = _private_import_counts()
    assert sum(current.values()) <= PRIVATE_IMPORT_TOTAL_BUDGET

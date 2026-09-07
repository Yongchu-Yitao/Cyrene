"""Inspect imports inside functions/conditions as well as module-level imports."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


def domain(module: str) -> str:
    parts = module.split(".")
    if parts[:2] == ["cyrene", "workbench"]:
        return ".".join(parts[:3])
    if parts[:3] == ["cyrene", "plugins", "builtin"]:
        return ".".join(parts[:4])
    return ".".join(parts[:2])


def import_edges(source: str, filename: str) -> set[tuple[str, str, str]]:
    """Return importer, resolved module, imported name; resolve relative imports."""
    module = filename.removeprefix("src/").removesuffix(".py").replace("/", ".")
    package = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
    edges = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            edges.update((module, alias.name, "") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            target = ".".join([*prefix, *([node.module] if node.module else [])])
            edges.update((module, target if node.module else target + "." + alias.name, alias.name) for alias in node.names)
    return edges


def boundary_debt(source: str, filename: str) -> set[str]:
    result = set()
    for importer, target, name in import_edges(source, filename):
        origin, destination = domain(importer), domain(target)
        if origin == destination or not target.startswith("cyrene."):
            continue
        private = name.startswith("_") and name != "__all__"
        sibling_plugin = origin.startswith("cyrene.plugins.builtin.") and destination.startswith("cyrene.plugins.builtin.")
        if private or sibling_plugin:
            result.add(f"{filename}::{target}::{name}")
    return result


def collect(read_source: Callable[[Path], str] | None = None) -> set[str]:
    read_source = read_source or (lambda path: path.read_text())
    return set().union(*(boundary_debt(read_source(path), path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "src").rglob("*.py"))))

"""Regenerate the ratcheted Python-function complexity baseline.

Run this only after reviewing a refactor that reduces or renames existing
large functions. The architecture test rejects new large functions and growth
above the reviewed line count recorded here.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
OUTPUT = ROOT / "project-notes" / "architecture-complexity-baseline.json"
LARGE_FUNCTION_LINES = 100


class FunctionCollector(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.scope: list[str] = []
        self.functions: dict[str, int] = {}

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        lines = int(node.end_lineno or node.lineno) - node.lineno + 1
        if lines >= LARGE_FUNCTION_LINES:
            relative = self.path.relative_to(ROOT).as_posix()
            self.functions[f"{relative}::{'.'.join(self.scope)}"] = lines
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def collect_large_functions() -> dict[str, int]:
    functions: dict[str, int] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        collector = FunctionCollector(path)
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        functions.update(collector.functions)
    return dict(sorted(functions.items()))


def collect_private_cross_package_imports() -> list[str]:
    imports: set[str] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        parts = path.relative_to(SOURCE_ROOT).parts
        importer_package = ".".join(parts[:2]) if len(parts) > 1 else parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            imported_package = ".".join(node.module.split(".")[:2])
            if importer_package == imported_package:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and alias.name != "__all__":
                    imports.add(f"{relative}::{node.module}::{alias.name}")
    return sorted(imports)


def main() -> None:
    payload = {
        "threshold_lines": LARGE_FUNCTION_LINES,
        "large_functions": collect_large_functions(),
        "private_cross_package_imports": collect_private_cross_package_imports(),
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

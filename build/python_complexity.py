"""AST complexity budgets. Baselines ratchet down; growth requires explicit review.

Measures decisions (including boolean operands/comprehensions), control nesting,
function spans, and class/module size. Nested functions own their decisions.
This is a deterministic structural metric, not a runtime performance estimate.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'project-notes/python-structure-baseline.json'
LIMITS = {'lines': 99, 'decisions': 20, 'nesting': 5, 'methods': 25, 'fields': 25, 'module_lines': 1000}
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
CONTROLS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match, ast.IfExp)


def function_metrics(node: ast.AST) -> dict[str, int]:
    decisions = 0
    nesting = 0

    def visit(current: ast.AST, depth: int) -> None:
        nonlocal decisions, nesting
        if isinstance(current, SCOPES):
            return
        if isinstance(current, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
            decisions += 1
        elif isinstance(current, ast.BoolOp):
            decisions += len(current.values) - 1
        elif isinstance(current, ast.comprehension):
            decisions += 1 + len(current.ifs)
        elif isinstance(current, ast.match_case):
            decisions += 1 + int(current.guard is not None)
        depth += int(isinstance(current, CONTROLS))
        nesting = max(nesting, depth)
        for child in ast.iter_child_nodes(current):
            visit(child, depth)

    body = getattr(node, 'body', ())
    for child in ([body] if isinstance(body, ast.AST) else body):
        visit(child, 0)
    return {'lines': node.end_lineno - node.lineno + 1, 'decisions': decisions, 'nesting': nesting}


def analyze_source(source: str, filename: str) -> dict[str, dict[str, int]]:
    tree = ast.parse(source, filename=filename)
    result = {filename: {'module_lines': len(source.splitlines())}}

    counts: dict[str, int] = {}

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, SCOPES):
            scope = (*scope, '<lambda>' if isinstance(node, ast.Lambda) else node.name)
            key = filename + '::' + '.'.join(scope)
            count = counts.get(key, 0) + 1
            counts[key] = count
            if count > 1:
                key += f'#{count}'
            if isinstance(node, ast.ClassDef):
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                # Includes writes from closures inside methods, but excludes nested classes.
                fields: set[str] = set()
                def collect_fields(current: ast.AST) -> None:
                    if isinstance(current, ast.ClassDef):
                        return
                    if (isinstance(current, ast.Attribute) and isinstance(current.ctx, ast.Store)
                            and isinstance(current.value, ast.Name) and current.value.id == 'self'):
                        fields.add(current.attr)
                    for child in ast.iter_child_nodes(current):
                        collect_fields(child)
                for method in methods:
                    collect_fields(method)
                result[key] = {'methods': len(methods), 'fields': len(fields)}
            else:
                result[key] = function_metrics(node)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return result


def collect() -> dict[str, dict[str, int]]:
    result = {}
    for path in sorted((ROOT / 'src').rglob('*.py')):
        result.update(analyze_source(path.read_text(encoding='utf-8'), path.relative_to(ROOT).as_posix()))
    return result


def exceptions(metrics: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {key: excess for key, values in sorted(metrics.items())
            if (excess := {name: value for name, value in values.items() if value > LIMITS[name]})}


def violations(current: dict, baseline: dict) -> list[str]:
    return [f'{key}: {name} {value} > {max(LIMITS[name], baseline.get(key, {}).get(name, 0))}'
            for key, values in current.items() for name, value in values.items()
            if value > max(LIMITS[name], baseline.get(key, {}).get(name, 0))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true', help='Create first reviewed baseline; refuses overwrite')
    parser.add_argument('--ratchet', action='store_true', help='Tighten baseline only after all budgets pass')
    parser.add_argument("--ownership-report", type=Path, help="Write domain, state-write and resource-call evidence")
    args = parser.parse_args()
    if args.ownership_report:
        from python_ownership_report import write_report
        write_report(ROOT, args.ownership_report)
    current = collect()
    if args.initialize:
        with BASELINE.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(exceptions(current), indent=2) + '\n')
        return
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    failures = violations(current, baseline)
    if failures:
        raise SystemExit('\n'.join(failures))
    if args.ratchet:
        BASELINE.write_text(json.dumps(exceptions(current), indent=2) + '\n', encoding='utf-8')
    print(f'Python complexity: {len(current)} scopes checked; no budget growth')


if __name__ == '__main__':
    main()

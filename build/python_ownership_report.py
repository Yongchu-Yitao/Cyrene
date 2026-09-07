"""Static ownership clues, not proof that resources leak or writes race.

Domain totals survive moving functions within a domain. Only explicit self
attribute writes are called owned writes; ambiguous collection receivers remain
unresolved. Resource calls are syntactic candidates, not allocation accounting.
"""
from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

MUTATORS = frozenset({'append', 'extend', 'insert', 'update', 'clear', 'pop', 'popitem', 'add', 'discard', 'remove', 'setdefault'})
CREATORS = frozenset({'create_task', 'ensure_future', 'Thread', 'Process', 'subscribe', 'add_listener', 'open', 'connect'})
RELEASERS = frozenset({'cancel', 'close', 'aclose', 'join', 'unsubscribe', 'remove_listener', 'shutdown'})
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def domain_for(filename: str) -> str:
    parts = Path(filename).parts
    if parts[:4] == ('src', 'cyrene', 'plugins', 'builtin') and len(parts) > 4:
        return '/'.join(parts[:5])
    if parts[:3] == ('src', 'cyrene', 'workbench') and len(parts) > 3:
        return '/'.join(parts[:4])
    return '/'.join(parts[:3])


def inspect_source(source: str, filename: str) -> list[dict]:
    rows = []

    def inspect(node, owner):
        if isinstance(node, SCOPES):
            owner = (*owner, getattr(node, 'name', '<lambda>'))
            if not isinstance(node, ast.ClassDef):
                row = {'scope': filename + '::' + '.'.join(owner), 'line': node.lineno,
                       'owned_writes': [], 'unresolved_mutations': [],
                       'resource_creation_candidates': [], 'resource_release_candidates': []}
                scan(node, row, root=True)
                if any(row[key] for key in tuple(row)[2:]):
                    rows.append(row)
        for child in ast.iter_child_nodes(node):
            inspect(child, owner)

    def scan(node, row, *, root=False):
        if not root and isinstance(node, SCOPES):
            return
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            target = ast.unparse(node)
            if target.startswith('self.'):
                row['owned_writes'].append({'line': node.lineno, 'target': target})
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            name = callee.rsplit('.', 1)[-1]
            if name in MUTATORS:
                key = 'owned_writes' if callee.startswith('self.') else 'unresolved_mutations'
                row[key].append({'line': node.lineno, 'target': callee})
            if name in CREATORS:
                row['resource_creation_candidates'].append({'line': node.lineno, 'call': callee})
            if name in RELEASERS:
                row['resource_release_candidates'].append({'line': node.lineno, 'call': callee})
        for child in ast.iter_child_nodes(node):
            scan(child, row)

    inspect(ast.parse(source, filename), ())
    return rows


def collect_ownership(root: Path) -> dict:
    domains: dict[str, Counter] = {}
    scopes = []
    for path in sorted((root / 'src').rglob('*.py')):
        filename = path.relative_to(root).as_posix()
        totals = domains.setdefault(domain_for(filename), Counter())
        totals['modules'] += 1
        rows = inspect_source(path.read_text(encoding='utf-8'), filename)
        scopes.extend(rows)
        for row in rows:
            for key in ('owned_writes', 'unresolved_mutations', 'resource_creation_candidates', 'resource_release_candidates'):
                totals[key] += len(row[key])
    return {'interpretation': __doc__, 'domains': domains, 'scopes': scopes}


def write_report(root: Path, destination: Path) -> None:
    destination.write_text(json.dumps(collect_ownership(root), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

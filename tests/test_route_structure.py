"""Contract and ownership tests for the centralized HTTP adapter package."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

from route.registry import register_routes


ROOT = Path(__file__).resolve().parents[1]
ROUTE_DECORATOR = re.compile(
    r"@(?:router|app)\.(get|post|put|patch|delete|websocket)"
    r"\(\s*[\"']([^\"']+)"
)
EXPECTED_ROUTE_CONTRACT_SHA256 = (
    "89ebaca9a514afbc64922abd83e2c9ec9ffe6ae7bcbe835730968a9248423e44"
)


def _declared_routes(root: Path) -> list[str]:
    routes: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        routes.extend(
            f"{method.upper()} {route_path}"
            for method, route_path in ROUTE_DECORATOR.findall(text)
        )
    return routes


def _registered_routes(db_path: Path) -> set[str]:
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(db_path))
    routes: set[str] = set()
    for registered in app.routes:
        if isinstance(registered, APIRoute):
            routes.update(
                f"{method} {registered.path}"
                for method in registered.methods or ()
                if method not in {"HEAD", "OPTIONS"}
            )
        elif isinstance(registered, APIWebSocketRoute):
            routes.add(f"WEBSOCKET {registered.path}")
    return routes


def test_route_package_owns_the_complete_public_contract():
    routes = _declared_routes(ROOT / "src" / "route")

    assert len(routes) == 318
    assert len(routes) == len(set(routes)), "duplicate method/path declaration"
    assert (
        hashlib.sha256("\n".join(sorted(routes)).encode()).hexdigest()
        == EXPECTED_ROUTE_CONTRACT_SHA256
    )


def test_registry_installs_every_declared_route_once(tmp_path):
    declared = set(_declared_routes(ROOT / "src" / "route"))
    code_routes = {
        contract
        for contract in declared
        if contract.split(" ", 1)[1]
        in {"/file", "/format", "/diff", "/git-diff"}
    }
    declared.difference_update(code_routes)
    declared.update(
        f"{method} /api/code{path}"
        for method, path in (contract.split(" ", 1) for contract in code_routes)
    )

    assert _registered_routes(tmp_path / "route-contract.sqlite3") == declared


def test_fastapi_route_declarations_do_not_leak_back_into_runtime_packages():
    leaked = [
        path
        for package in (ROOT / "src" / "cyrene", ROOT / "src" / "webui")
        for path in package.rglob("*.py")
        if ROUTE_DECORATOR.search(path.read_text(encoding="utf-8"))
    ]

    assert leaked == []


def test_removed_webui_route_modules_are_not_referenced():
    old_import = re.compile(
        r"(?:webui\.routes|webui\.api_(?:models|errors)|"
        r"webui\.workspace_validation|cyrene\.channels\.wechat\.web)"
    )
    references: list[Path] = []
    for package in (ROOT / "src", ROOT / "tests"):
        for path in package.rglob("*.py"):
            if old_import.search(path.read_text(encoding="utf-8")):
                references.append(path)

    assert references == []

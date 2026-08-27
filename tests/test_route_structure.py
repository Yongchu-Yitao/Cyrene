"""Contract and ownership tests for the centralized HTTP adapter package."""

from __future__ import annotations

import ast
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
EXPECTED_ROUTE_CONTRACT_SHA256 = "8a8215f85af67674c4b2a306bf517c367a69d81e230912c954b50af331faa2e3"
EXPECTED_REGISTERED_ROUTE_COUNT = 403
STANDALONE_HTTP_APPS = {
    ROOT
    / "src"
    / "agent"
    / "plugin"
    / "plugin_impl"
    / "cyrene_office"
    / "gateway.py"
}


def _declared_routes(root: Path) -> list[str]:
    routes: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        routes.extend(f"{method.upper()} {route_path}" for method, route_path in ROUTE_DECORATOR.findall(text))
    return routes


def _registered_routes(db_path: Path) -> set[str]:
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(db_path))
    routes: set[str] = set()
    for registered in app.routes:
        if isinstance(registered, APIRoute):
            routes.update(f"{method} {registered.path}" for method in registered.methods or () if method not in {"HEAD", "OPTIONS"})
        elif isinstance(registered, APIWebSocketRoute):
            routes.add(f"WEBSOCKET {registered.path}")
    return routes


def test_plugin_application_host_owns_the_complete_public_contract(tmp_path):
    # Core route files are only the static part of the application. Plugin
    # packs attach process routes through PluginApplicationHost, so the
    # registered FastAPI surface is the authoritative public contract.
    routes = _registered_routes(tmp_path / "route-contract.sqlite3")

    assert len(routes) == EXPECTED_REGISTERED_ROUTE_COUNT
    assert len(routes) == len(set(routes)), "duplicate method/path declaration"
    assert {
        "GET /api/projects/{project_id}/memory-prompt",
        "PATCH /api/projects/{project_id}/memory-prompt",
        "POST /api/projects/{project_id}/memory-prompt/restore",
        "POST /api/workbench/chats/{chat_id}/memory-learning",
        "GET /api/workbench/chats/{chat_id}/agent-config-options",
        "PATCH /api/workbench/chats/{chat_id}/trace",
        "POST /api/voice/asr",
        "POST /api/voice/tts",
    } <= set(routes)
    assert hashlib.sha256("\n".join(sorted(routes)).encode()).hexdigest() == EXPECTED_ROUTE_CONTRACT_SHA256


def test_registry_installs_every_declared_route_once(tmp_path):
    declared = set(_declared_routes(ROOT / "src" / "route"))
    code_routes = {contract for contract in declared if contract.split(" ", 1)[1] in {"/file", "/format", "/diff", "/git-diff"}}
    declared.difference_update(code_routes)
    declared.update(f"{method} /api/code{path}" for method, path in (contract.split(" ", 1) for contract in code_routes))

    registered = _registered_routes(tmp_path / "route-contract.sqlite3")
    assert declared <= registered
    assert len(registered) == EXPECTED_REGISTERED_ROUTE_COUNT


def test_optional_route_groups_are_registered_by_their_plugin_packs():
    chat_composition = (
        ROOT / "src" / "route" / "workbench" / "chat_routes" / "chats.py"
    ).read_text(encoding="utf-8")
    settings_composition = (
        ROOT / "src" / "route" / "settings" / "general.py"
    ).read_text(encoding="utf-8")
    voice_application = (
        ROOT
        / "src"
        / "agent"
        / "plugin"
        / "plugin_impl"
        / "cyrene_voice"
        / "application.py"
    ).read_text(encoding="utf-8")
    model_application = (
        ROOT
        / "src"
        / "agent"
        / "plugin"
        / "plugin_impl"
        / "cyrene_model"
        / "application.py"
    ).read_text(encoding="utf-8")

    assert "register_voice_routes" not in chat_composition
    assert "register_oauth_routes" not in settings_composition
    assert "register_workbench_voice_routes(context.router" in voice_application
    assert "from route.voice" not in voice_application
    assert "from route.workbench.chat_routes.voice_routes" not in voice_application
    assert "register_oauth_routes(context.router)" in model_application


def test_fastapi_route_declarations_do_not_leak_back_into_runtime_packages():
    leaked = [
        path
        for package in (ROOT / "src" / "cyrene", ROOT / "src" / "webui")
        for path in package.rglob("*.py")
        if path not in STANDALONE_HTTP_APPS and ROUTE_DECORATOR.search(path.read_text(encoding="utf-8"))
    ]

    assert leaked == []
    # The Office add-in gateway is a separate loopback HTTPS application with
    # its own port, TLS material, lifecycle, and WebSocket. Keep that explicit
    # application boundary from becoming a general runtime-package exception.
    assert all(path.is_file() for path in STANDALONE_HTTP_APPS)
    assert all(ROUTE_DECORATOR.search(path.read_text(encoding="utf-8")) for path in STANDALONE_HTTP_APPS)


def test_route_adapters_do_not_use_wildcard_imports():
    offenders: list[Path] = []
    for path in (ROOT / "src" / "route").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names) for node in ast.walk(tree)):
            offenders.append(path.relative_to(ROOT))

    assert offenders == []


def _route_contract(
    route_dir: Path,
    module_names: set[str],
) -> set[tuple[str, str, str]]:
    actual: set[tuple[str, str, str]] = set()
    for path in (route_dir / name for name in module_names):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func,
                    ast.Attribute,
                ):
                    continue
                if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                path_value = decorator.args[0].value
                if isinstance(path_value, str) and path_value.startswith("/api/workbench/"):
                    actual.add((decorator.func.attr, path_value, node.name))
    return actual


def test_split_chat_route_contract_is_stable():
    route_dir = ROOT / "src" / "route" / "workbench" / "chat_routes"
    expected = {
        ("get", "/api/workbench/pinned-resources", "api_workbench_pinned_resources"),
        ("post", "/api/workbench/pinned-resources", "api_workbench_pin_resource"),
        ("delete", "/api/workbench/pinned-resources/{resource_id}", "api_workbench_unpin_resource"),
        ("get", "/api/workbench/chats", "api_workbench_list_chats"),
        ("get", "/api/workbench/quick-chat/targets", "api_workbench_quick_chat_targets"),
        ("post", "/api/workbench/chats", "api_workbench_create_chat"),
        ("get", "/api/workbench/slash-commands", "api_workbench_slash_commands"),
        ("get", "/api/workbench/chats/{chat_id}/side-agents", "api_workbench_list_side_agents"),
        ("post", "/api/workbench/chats/{chat_id}/side-agents", "api_workbench_create_side_agent"),
        ("get", "/api/workbench/chats/{chat_id}", "api_workbench_get_chat"),
        ("patch", "/api/workbench/chats/{chat_id}", "api_workbench_update_chat"),
        ("patch", "/api/workbench/chats/{chat_id}/trace", "api_workbench_patch_chat_trace"),
        ("get", "/api/workbench/chats/{chat_id}/agent-config-options", "api_workbench_agent_config_options"),
        ("get", "/api/workbench/chat-groups", "api_workbench_chat_groups"),
        ("put", "/api/workbench/chat-groups", "api_workbench_replace_chat_groups"),
        ("post", "/api/workbench/chat-groups/metadata", "api_workbench_chat_group_metadata"),
        ("delete", "/api/workbench/chats/{chat_id}", "api_workbench_delete_chat"),
        ("post", "/api/workbench/chats/{chat_id}/fork", "api_workbench_chat_fork"),
        ("post", "/api/workbench/chats/{chat_id}/to-task", "api_workbench_chat_to_task"),
    }
    split_modules = {
        "pinned_routes.py",
        "collection_routes.py",
        "context_catalog_routes.py",
        "side_agents_routes.py",
        "detail_routes.py",
        "agent_config_routes.py",
        "groups_routes.py",
        "delete_routes.py",
        "fork_routes.py",
        "to_task_routes.py",
    }
    assert _route_contract(route_dir, split_modules) == expected


def test_split_chat_run_route_contract_is_stable():
    route_dir = ROOT / "src" / "route" / "workbench" / "chat_routes"
    modules = {
        "run_stream_routes.py",
        "run_send_routes.py",
        "run_respond_routes.py",
        "run_action_routes.py",
        "run_answer_routes.py",
    }
    assert _route_contract(route_dir, modules) == {
        ("get", "/api/workbench/chats/{chat_id}/run-stream", "api_workbench_chat_run_stream"),
        ("post", "/api/workbench/chats/{chat_id}/interrupt", "api_workbench_chat_interrupt"),
        ("post", "/api/workbench/chats/{chat_id}/guidance", "api_workbench_chat_guidance"),
        ("post", "/api/workbench/chats/{chat_id}/messages", "api_workbench_chat_send"),
        (
            "post",
            "/api/workbench/chats/{chat_id}/agent-requests/{request_id}/respond",
            "api_workbench_agent_request_respond",
        ),
        ("post", "/api/workbench/chats/{chat_id}/actions", "api_workbench_chat_action"),
        ("post", "/api/workbench/chats/{chat_id}/answer", "api_workbench_chat_answer"),
    }


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

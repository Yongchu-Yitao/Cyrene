from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "src" / "cyrene" / "plugins"
IMPLEMENTATION_ROOT = PLUGIN_ROOT / "builtin"
PROTOCOL_PURE_PACKS = tuple(
    path.name
    for path in sorted(IMPLEMENTATION_ROOT.iterdir())
    if path.is_dir()
    and path.name.startswith("cyrene_")
    and (path / "__init__.py").is_file()
)


def _implementation_sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in IMPLEMENTATION_ROOT.rglob("*.py")
    }


def test_editable_plugins_do_not_import_deleted_tool_runtime_facades() -> None:
    forbidden = (
        "cyrene.tooling.runtime_api",
        "cyrene.tooling.runtime_support",
        "cyrene.tool_impl",
    )
    offenders = {
        str(path.relative_to(ROOT)): token
        for path, source in _implementation_sources().items()
        for token in forbidden
        if token in source
    }
    assert offenders == {}


def test_native_runtime_is_an_implementation_not_a_legacy_reexport() -> None:
    source = (PLUGIN_ROOT / "native_runtime.py").read_text(encoding="utf-8")
    assert "require_plugin_execution" in source
    assert "PluginContext.workspace" in source
    assert "cyrene.tooling" not in source


def test_mutating_remote_and_browser_plugins_rely_on_central_review() -> None:
    files = (
        IMPLEMENTATION_ROOT / "cyrene_remote" / "files.py",
        IMPLEMENTATION_ROOT / "cyrene_remote" / "harness.py",
        IMPLEMENTATION_ROOT / "cyrene_remote" / "jobs.py",
        IMPLEMENTATION_ROOT / "cyrene_browser" / "browser_upload_files.py",
    )
    forbidden = (
        "request_scope_elevation",
        "request_read_elevation",
        "request_destructive_confirmation",
        "request_external_upload_confirmation",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert '"read_only": False' in source
        assert not any(token in source for token in forbidden)


def test_protocol_pure_packs_have_no_legacy_runtime_adapter_or_contextvars() -> None:
    forbidden = (
        "cyrene.agent.context",
        "bind_run_context",
        "RegistrationProvider",
        "create_plugin_pack",
    )
    for pack_name in PROTOCOL_PURE_PACKS:
        pack = IMPLEMENTATION_ROOT / pack_name
        assert not (pack / "_runtime.py").exists()
        for path in pack.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not any(token in source for token in forbidden), path


def test_protocol_pure_pack_handlers_accept_arguments_and_plugin_context_only() -> None:
    offenders: dict[str, tuple[str, ...]] = {}
    for pack_name in PROTOCOL_PURE_PACKS:
        pack = IMPLEMENTATION_ROOT / pack_name
        for path in pack.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            definitions = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            handlers: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "handler"
            ]
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                if not any(
                    isinstance(target, ast.Name) and target.id == "handler"
                    for target in node.targets
                ) or not isinstance(node.value, ast.Name):
                    continue
                implementation = definitions.get(node.value.id)
                if implementation is not None:
                    handlers.append(implementation)
            for node in handlers:
                positional = (*node.args.posonlyargs, *node.args.args)
                valid = (
                    len(positional) == 2
                    and node.args.vararg is None
                    and node.args.kwarg is None
                    and not node.args.kwonlyargs
                )
                if not valid:
                    offenders[str(path.relative_to(ROOT))] = tuple(
                        argument.arg for argument in positional
                    )
    assert offenders == {}

"""Plugin-owned HTTP adapters for the unified Plugin Center.

The extension service remains the single implementation of discovery and
installation.  Each native Plugin pack mounts only the routes for the kind it
owns, which keeps application capabilities aligned with PluginPack activation
without reviving the legacy, application-global Extension Center router.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.config import TEMP_DIR
from cyrene.localization import localized

from .extension_service import (
    ExtensionService,
    audit_records,
    get_extension_service,
    source_settings,
    update_source_settings,
)

PluginCenterKind = Literal["skill", "mcp", "cli", "toolchain"]
PluginLifecycleKind = Literal["skill", "mcp", "cli", "toolchain", "agent"]

logger = logging.getLogger(__name__)

_LIST_KEYS: dict[PluginCenterKind, str] = {
    "skill": "skills",
    "mcp": "mcp",
    "cli": "cli",
    "toolchain": "toolchains",
}

PLUGIN_CENTER_PROJECTIONS: dict[PluginCenterKind, dict[str, Any]] = {
    "skill": {
        "mode": "resource_gateway",
        "runtime_pack": "cyrene_skills",
        "resource": "skills",
        "activation": "progressive_disclosure",
    },
    "mcp": {
        "mode": "dynamic_plugin_packs",
        "runtime_pack_prefix": "mcp.",
        "activation": "connection",
    },
    "cli": {
        "mode": "process_environment",
        "runtime_pack": "cyrene_cli",
        "environment": "agent_process_environment",
        "activation": "tree_hook_and_path",
    },
    "toolchain": {
        "mode": "process_environment",
        "runtime_pack": "cyrene_extensions",
        "environment": "agent_process_environment",
        "activation": "path",
    },
}


def _error_response(exc: Exception, *, status_code: int | None = None) -> JSONResponse:
    if status_code is None:
        status_code = 502 if isinstance(exc, httpx.HTTPError) else 400
    raw = str(exc)
    known = {
        "request body must be an object": (
            "Request body must be an object.",
            "请求体必须是对象。",
            "invalid_request",
        ),
        "No file provided": (
            "No file was provided.",
            "未提供文件。",
            "file_required",
        ),
        "File too large (max 8 MB)": (
            "The file is too large (maximum 8 MB).",
            "文件过大（最大 8 MB）。",
            "file_too_large",
        ),
        "Skill upload must be a .md or .zip file": (
            "A Skill upload must be a .md or .zip file.",
            "技能上传文件必须是 .md 或 .zip。",
            "unsupported_file_type",
        ),
        "extension_id is required": (
            "extension_id is required.",
            "必须提供 extension_id。",
            "extension_id_required",
        ),
        "request must be an object": (
            "Request must be an object.",
            "请求必须是对象。",
            "invalid_request",
        ),
        "enabled must be a boolean": (
            "enabled must be a boolean.",
            "enabled 必须是布尔值。",
            "invalid_enabled",
        ),
        "url is required": (
            "A URL is required.",
            "必须提供 URL。",
            "url_required",
        ),
        "version is required": (
            "A version is required.",
            "必须提供版本。",
            "version_required",
        ),
        "task not found": (
            "The extension task was not found.",
            "未找到该扩展任务。",
            "extension_task_not_found",
        ),
    }
    if raw in known:
        en, zh, code = known[raw]
    elif isinstance(exc, httpx.HTTPError):
        en, zh, code = (
            "The extension service could not be reached.",
            "无法连接扩展服务。",
            "extension_service_unavailable",
        )
    elif isinstance(exc, (ValueError, KeyError)):
        en, zh, code = (
            "The Plugin Center request is invalid.",
            "插件中心请求无效。",
            "plugin_center_request_invalid",
        )
    else:
        en, zh, code = (
            "The Plugin Center operation failed.",
            "插件中心操作失败。",
            "plugin_center_operation_failed",
        )
    logger.info("Plugin Center request failed [%s]: %s", code, raw, exc_info=True)
    return JSONResponse(
        {"ok": False, "error": localized(en, zh), "code": code},
        status_code=status_code,
    )


async def _json_object(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError("request body must be an object")
    return dict(payload)


def _is_installed(item: Mapping[str, Any]) -> bool:
    return str(item.get("observed_state") or "").strip().lower() not in {
        "",
        "missing",
        "not_installed",
    }


def _items_for_kind(
    service: ExtensionService,
    kind: PluginCenterKind,
    *,
    installed_only: bool = True,
) -> list[dict[str, Any]]:
    state = service.list_extensions()
    raw_items = state.get(_LIST_KEYS[kind], []) if isinstance(state, dict) else []
    return [
        dict(item)
        for item in raw_items
        if isinstance(item, Mapping) and (not installed_only or _is_installed(item))
    ]


def _install_request(kind: PluginCenterKind, item: Mapping[str, Any]) -> dict[str, Any] | None:
    version = str(item.get("version") or item.get("recommended_version") or "")
    if kind == "skill":
        repository = item.get("clone_url") or item.get("repository")
        if isinstance(repository, Mapping):
            repository = repository.get("url")
        url = str(repository or "").strip()
        return {"url": url, "subdirs": []} if url.startswith("https://") else None
    if kind == "mcp":
        remote = next(
            (value for value in (item.get("installable_remotes") or []) if isinstance(value, Mapping)),
            None,
        )
        if remote is not None:
            return {
                "version": version,
                "remote": dict(remote),
                "source": {
                    "type": "mcp-registry",
                    "id": item.get("id"),
                    "version": version,
                },
            }
        package = next(
            (value for value in (item.get("installable_packages") or []) if isinstance(value, Mapping)),
            None,
        )
        if package is not None:
            return {
                "version": version,
                "package": dict(package),
                "source": {
                    "type": "mcp-registry-package",
                    "id": item.get("id"),
                    "version": version,
                },
            }
        return None

    spec_keys = (
        "name",
        "kind",
        "manager",
        "tool",
        "ref",
        "version",
        "recommended_version",
        "executables",
        "version_args",
        "description",
        "icon",
        "publisher",
        "risk",
        "backend",
        "verified",
    )
    spec = {key: item[key] for key in spec_keys if key in item}
    request: dict[str, Any] = {"version": version or "latest", "spec": spec}
    ref = str(item.get("ref") or "").strip()
    if ref:
        request["ref"] = ref
    return request


def _search_item(
    kind: PluginCenterKind,
    item: Mapping[str, Any],
    installed_ids: set[str],
) -> dict[str, Any]:
    result = dict(item)
    extension_id = str(result.get("id") or "").strip()
    result["kind"] = kind
    result["id"] = extension_id
    result["name"] = str(result.get("name") or extension_id)
    result["description"] = str(result.get("description") or "")
    installed = extension_id in installed_ids
    request = None if installed else _install_request(kind, result)
    fallback = None if installed else result.get("fallback_request")
    reason = "already_installed" if installed else str(result.get("reason_code") or "")
    if request is None and fallback is None and not reason:
        reason = "unsupported_registry_type" if kind == "mcp" else "not_installable"
    result.update(
        {
            "installed": installed,
            "install_request": request,
            "installable": bool(request) and result.get("installable") is not False,
            "inspect_required": kind == "skill" and bool(request),
            "fallback_request": fallback,
            "reason_code": reason,
        }
    )
    return result


def _kind_tasks(
    service: ExtensionService,
    kind: PluginLifecycleKind,
) -> list[dict[str, Any]]:
    return [
        dict(task)
        for task in service.tasks.list()
        if isinstance(task, Mapping) and str(task.get("kind") or "") == kind
    ]


async def _install_uploaded_skill(service: ExtensionService, request: Request) -> dict[str, Any]:
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise ValueError("No file provided")
    content = await upload.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("File too large (max 8 MB)")
    filename = Path(str(getattr(upload, "filename", "") or "skill.md")).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".zip"}:
        raise ValueError("Skill upload must be a .md or .zip file")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cyrene-skill-upload-", dir=TEMP_DIR) as directory:
        temporary_path = Path(directory) / filename
        temporary_path.write_bytes(content)
        return service.install_local_skill(temporary_path, actor="user")


def _register_catalog_routes(
    router: APIRouter,
    *,
    prefix: str,
    kind: PluginCenterKind,
    owner_pack: str,
    extensions: ExtensionService,
    projection: Mapping[str, Any],
    installed_only: bool = True,
) -> None:
    @router.get(prefix)
    async def api_plugin_center_list():
        try:
            return {
                "kind": kind,
                "owner_pack": owner_pack,
                "projection": dict(projection),
                "items": _items_for_kind(
                    extensions,
                    kind,
                    installed_only=installed_only,
                ),
                "tasks": _kind_tasks(extensions, kind),
            }
        except Exception as exc:
            return _error_response(exc)

    @router.get(f"{prefix}/search")
    async def api_plugin_center_search(
        q: str = "",
        advanced: bool = False,
        cursor: str = "",
    ):
        try:
            payload = await extensions.search(
                kind,
                q,
                advanced=advanced,
                cursor=cursor if kind == "mcp" else "",
            )
            installed_ids = {
                str(item.get("id") or "")
                for item in _items_for_kind(extensions, kind)
            }
            raw_results = payload.get("results", []) if isinstance(payload, dict) else []
            return {
                "kind": kind,
                "owner_pack": owner_pack,
                "results": [
                    _search_item(kind, item, installed_ids)
                    for item in raw_results
                    if isinstance(item, Mapping)
                ],
                "source": payload.get("source", "") if isinstance(payload, dict) else "",
                "next_cursor": payload.get("next_cursor", "") if isinstance(payload, dict) else "",
            }
        except Exception as exc:
            return _error_response(exc)


def _register_extension_mutation_routes(
    router: APIRouter,
    *,
    prefix: str,
    kind: PluginLifecycleKind,
    extensions: ExtensionService,
) -> None:
    @router.post(f"{prefix}/install")
    async def api_plugin_center_install(request: Request):
        try:
            payload = await _json_object(request)
            extension_id = str(payload.get("extension_id") or "").strip()
            install_request = payload.get("request", {})
            if not extension_id:
                raise ValueError("extension_id is required")
            if not isinstance(install_request, dict):
                raise ValueError("request must be an object")
            task = extensions.start_install(
                kind,
                extension_id,
                dict(install_request),
                actor="user",
            )
            return {"ok": True, "task": task, "task_id": task.get("id", "")}
        except Exception as exc:
            return _error_response(exc)

    @router.put(f"{prefix}/{{extension_id:path}}/enabled")
    async def api_plugin_center_enabled(extension_id: str, request: Request):
        try:
            payload = await _json_object(request)
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            return await extensions.set_extension_enabled(
                kind,
                extension_id,
                enabled,
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)

    @router.delete(f"{prefix}/{{extension_id:path}}")
    async def api_plugin_center_uninstall(extension_id: str, version: str = ""):
        try:
            return await extensions.uninstall(
                kind,
                extension_id,
                version=version,
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)


def _register_task_routes(
    router: APIRouter,
    *,
    prefix: str,
    kind: PluginLifecycleKind,
    extensions: ExtensionService,
) -> None:
    @router.get(f"{prefix}/tasks/{{task_id}}")
    async def api_plugin_center_task(task_id: str):
        task = extensions.tasks.get(task_id)
        if task is None or str(task.get("kind") or "") != kind:
            return _error_response(ValueError("task not found"), status_code=404)
        return {"task": task}

    @router.post(f"{prefix}/tasks/{{task_id}}/cancel")
    async def api_plugin_center_cancel_task(task_id: str):
        task = extensions.tasks.get(task_id)
        if task is None or str(task.get("kind") or "") != kind:
            return _error_response(ValueError("task not found"), status_code=404)
        cancelled = extensions.tasks.cancel(task_id)
        return {"ok": cancelled, "task": extensions.tasks.get(task_id)}


def _register_skill_routes(
    router: APIRouter,
    *,
    prefix: str,
    extensions: ExtensionService,
) -> None:
    @router.post(f"{prefix}/inspect")
    async def api_plugin_center_inspect_skill(request: Request):
        try:
            payload = await _json_object(request)
            url = str(payload.get("url") or "").strip()
            if not url:
                raise ValueError("url is required")
            return await extensions.inspect_skill_source(url)
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/import")
    async def api_plugin_center_import_skill(request: Request):
        try:
            payload = await _json_object(request)
            return extensions.install_local_skill(
                str(payload.get("path") or ""),
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/upload")
    async def api_plugin_center_upload_skill(request: Request):
        try:
            return await _install_uploaded_skill(extensions, request)
        except Exception as exc:
            return _error_response(exc)


def _source_health_targets(settings: Mapping[str, Any]) -> dict[str, str]:
    targets = {
        "github": "https://api.github.com/rate_limit",
        "npm": str(
            settings.get("npm_registry") or "https://registry.npmjs.org"
        ).rstrip("/")
        + "/-/ping",
        "pip": str(
            settings.get("pip_index_url") or "https://pypi.org/simple"
        ).rstrip("/"),
        "mcp": str(
            settings.get("mcp_registry_url")
            or "https://registry.modelcontextprotocol.io"
        ).rstrip("/")
        + "/v0.1/health",
    }
    skill_catalog = str(settings.get("skill_catalog_url") or "").strip()
    if skill_catalog:
        targets["skills"] = skill_catalog
    return targets


async def _test_extension_sources(
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    from cyrene.runtime.network_proxy import scoped_proxy_url

    token = str(settings.get("github_token") or "")
    github_headers = {"Authorization": f"Bearer {token}"} if token else None
    checks: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        proxy=scoped_proxy_url("extensions") or None,
    ) as client:
        for name, url in _source_health_targets(settings).items():
            try:
                response = await client.get(
                    url,
                    headers=github_headers if name == "github" else None,
                )
                checks[name] = {
                    "ok": response.status_code < 500,
                    "status": response.status_code,
                    "url": url,
                }
            except httpx.HTTPError as exc:
                logger.info(
                    "Plugin Center source health check failed [source=%s url=%s]",
                    name,
                    url,
                    exc_info=True,
                )
                checks[name] = {
                    "ok": False,
                    "error": localized(
                        "The source could not be reached.",
                        "无法连接该来源。",
                    ),
                    "code": "extension_source_unreachable",
                    "url": url,
                }
    return {
        "ok": all(check.get("ok") for check in checks.values()),
        "checks": checks,
    }


def _register_overview_routes(
    router: APIRouter,
    *,
    owner_pack: str,
    extensions: ExtensionService,
) -> None:
    async def overview_payload():
        try:
            state = extensions.list_extensions()
            result = dict(state) if isinstance(state, Mapping) else {}
            result["owner_pack"] = owner_pack
            return result
        except Exception as exc:
            return _error_response(exc)

    router.add_api_route(
        "/api/plugin-center",
        overview_payload,
        methods=["GET"],
        name="plugin_center_list",
    )
    router.add_api_route(
        "/api/plugin-center/overview",
        overview_payload,
        methods=["GET"],
        name="plugin_center_overview",
    )


def _register_toolchain_extra_routes(
    router: APIRouter,
    *,
    prefix: str,
    extensions: ExtensionService,
) -> None:
    @router.get(f"{prefix}/{{extension_id}}/versions")
    async def api_plugin_center_toolchain_versions(extension_id: str):
        try:
            return await extensions.list_versions("toolchain", extension_id)
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/{{extension_id}}/default")
    async def api_plugin_center_toolchain_default(
        extension_id: str,
        request: Request,
    ):
        try:
            payload = await _json_object(request)
            version = str(payload.get("version") or "").strip()
            if not version:
                raise ValueError("version is required")
            return await extensions.set_default_version(
                extension_id,
                version,
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)


def _register_binding_routes(
    router: APIRouter,
    *,
    prefix: str,
    extensions: ExtensionService,
) -> None:
    @router.post(f"{prefix}/{{extension_id}}/bind")
    async def api_plugin_center_bind(
        extension_id: str,
        request: Request,
    ):
        try:
            payload = await _json_object(request)
            return extensions.bind_system_executable(
                extension_id,
                str(payload.get("path") or ""),
            )
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/{{extension_id}}/unbind")
    async def api_plugin_center_unbind(extension_id: str):
        try:
            return extensions.unbind_system_executable(extension_id)
        except Exception as exc:
            return _error_response(exc)


def _register_agent_catalog_routes(
    router: APIRouter,
    *,
    prefix: str,
    owner_pack: str,
    extensions: ExtensionService,
) -> None:
    @router.get(prefix)
    async def api_plugin_center_agents():
        try:
            listing = extensions.agent_listing()
            return {
                "kind": "agent",
                "owner_pack": owner_pack,
                **(dict(listing) if isinstance(listing, Mapping) else {}),
                "tasks": _kind_tasks(extensions, "agent"),
            }
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/install-proposals")
    async def api_plugin_center_agent_proposal(request: Request):
        try:
            payload = await _json_object(request)
            requested_version = str(
                payload.get("requestedVersion")
                or payload.get("requested_version")
                or ""
            )
            return await extensions.create_agent_install_proposal(
                payload.get("source"),
                requested_version,
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)

    @router.post(f"{prefix}/install-proposals/{{proposal_id}}/confirm")
    async def api_plugin_center_agent_confirm(proposal_id: str):
        try:
            return await extensions.confirm_agent_install_proposal(
                proposal_id,
                actor="user",
            )
        except Exception as exc:
            return _error_response(exc)


def _register_global_task_routes(
    router: APIRouter,
    *,
    extensions: ExtensionService,
) -> None:
    prefix = "/api/plugin-center/tasks"

    @router.get(prefix)
    async def api_plugin_center_tasks():
        return {"tasks": extensions.tasks.list()}

    @router.get(f"{prefix}/{{task_id}}")
    async def api_plugin_center_task(task_id: str):
        task = extensions.tasks.get(task_id)
        if task is None:
            return _error_response(ValueError("task not found"), status_code=404)
        return {"task": task}

    @router.post(f"{prefix}/{{task_id}}/cancel")
    async def api_plugin_center_cancel_task(task_id: str):
        if extensions.tasks.get(task_id) is None:
            return _error_response(ValueError("task not found"), status_code=404)
        cancelled = extensions.tasks.cancel(task_id)
        return {"ok": cancelled, "task": extensions.tasks.get(task_id)}


def _register_admin_routes(
    router: APIRouter,
    *,
    source_get: Callable[..., dict[str, Any]],
    source_update: Callable[[dict[str, Any]], dict[str, Any]],
    audit_get: Callable[[int], list[dict[str, Any]]],
    source_test: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
) -> None:
    @router.get("/api/plugin-center/sources")
    async def api_plugin_center_sources():
        try:
            return source_get()
        except Exception as exc:
            return _error_response(exc)

    @router.put("/api/plugin-center/sources")
    async def api_plugin_center_update_sources(request: Request):
        try:
            return source_update(await _json_object(request))
        except Exception as exc:
            return _error_response(exc)

    @router.post("/api/plugin-center/sources/test")
    async def api_plugin_center_test_sources():
        try:
            return await source_test(source_get(include_secret=True))
        except Exception as exc:
            return _error_response(exc)

    @router.get("/api/plugin-center/audit")
    async def api_plugin_center_audit(limit: int = 200):
        try:
            return {"records": audit_get(limit)}
        except Exception as exc:
            return _error_response(exc)


def register_plugin_center_extension_routes(
    router: APIRouter,
    *,
    owner_pack: str = "cyrene_extensions",
    service: ExtensionService | None = None,
    source_get: Callable[..., dict[str, Any]] = source_settings,
    source_update: Callable[[dict[str, Any]], dict[str, Any]] = update_source_settings,
    audit_get: Callable[[int], list[dict[str, Any]]] = audit_records,
    source_test: Callable[
        [Mapping[str, Any]], Awaitable[dict[str, Any]]
    ] = _test_extension_sources,
) -> None:
    """Mount the cross-kind Plugin Center API owned by cyrene_extensions."""

    extensions = service or get_extension_service()
    toolchain_prefix = "/api/plugin-center/toolchain"
    agent_prefix = "/api/plugin-center/agent"
    _register_overview_routes(
        router,
        owner_pack=owner_pack,
        extensions=extensions,
    )
    _register_catalog_routes(
        router,
        prefix=toolchain_prefix,
        kind="toolchain",
        owner_pack=owner_pack,
        extensions=extensions,
        projection=PLUGIN_CENTER_PROJECTIONS["toolchain"],
        installed_only=False,
    )
    _register_extension_mutation_routes(
        router,
        prefix=toolchain_prefix,
        kind="toolchain",
        extensions=extensions,
    )
    _register_task_routes(
        router,
        prefix=toolchain_prefix,
        kind="toolchain",
        extensions=extensions,
    )
    _register_toolchain_extra_routes(
        router,
        prefix=toolchain_prefix,
        extensions=extensions,
    )
    _register_binding_routes(
        router,
        prefix=toolchain_prefix,
        extensions=extensions,
    )
    _register_agent_catalog_routes(
        router,
        prefix=agent_prefix,
        owner_pack=owner_pack,
        extensions=extensions,
    )
    _register_extension_mutation_routes(
        router,
        prefix=agent_prefix,
        kind="agent",
        extensions=extensions,
    )
    _register_task_routes(
        router,
        prefix=agent_prefix,
        kind="agent",
        extensions=extensions,
    )
    _register_global_task_routes(router, extensions=extensions)
    _register_admin_routes(
        router,
        source_get=source_get,
        source_update=source_update,
        audit_get=audit_get,
        source_test=source_test,
    )


def register_plugin_center_routes(
    router: APIRouter,
    *,
    kind: PluginCenterKind,
    owner_pack: str,
    service: ExtensionService | None = None,
) -> None:
    """Mount the Plugin Center slice owned by one native Plugin pack."""

    if kind not in _LIST_KEYS:
        raise ValueError(f"unsupported Plugin Center kind: {kind}")
    extensions = service or get_extension_service()
    prefix = f"/api/plugin-center/{kind}"
    projection = dict(PLUGIN_CENTER_PROJECTIONS[kind])
    _register_catalog_routes(
        router,
        prefix=prefix,
        kind=kind,
        owner_pack=owner_pack,
        extensions=extensions,
        projection=projection,
    )
    _register_extension_mutation_routes(
        router,
        prefix=prefix,
        kind=kind,
        extensions=extensions,
    )
    _register_task_routes(
        router,
        prefix=prefix,
        kind=kind,
        extensions=extensions,
    )
    if kind == "skill":
        _register_skill_routes(router, prefix=prefix, extensions=extensions)
    if kind == "cli":
        _register_binding_routes(router, prefix=prefix, extensions=extensions)


__all__ = [
    "PLUGIN_CENTER_PROJECTIONS",
    "PluginCenterKind",
    "PluginLifecycleKind",
    "register_plugin_center_extension_routes",
    "register_plugin_center_routes",
]

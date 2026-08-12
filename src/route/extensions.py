"""Extension Center HTTP routes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.extensions.service import (
    audit_records,
    get_extension_service,
    source_settings,
    update_source_settings,
)


def _error(exc: Exception, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


def register_extension_routes(router: APIRouter, _bot: Any, _db_path: str) -> None:
    service = get_extension_service()

    @router.get("/api/extensions")
    async def api_extensions():
        return service.list_extensions()

    @router.get("/api/extensions/search")
    async def api_search_extensions(kind: str, q: str = "", advanced: bool = False, cursor: str = ""):
        try:
            return await service.search(kind, q, advanced=advanced, cursor=cursor)
        except httpx.HTTPError as exc:
            return _error(RuntimeError(f"Extension source request failed: {exc}"), 502)
        except Exception as exc:
            return _error(exc)

    @router.get("/api/extensions/{kind}/{extension_id}/versions")
    async def api_extension_versions(kind: str, extension_id: str):
        try:
            return await service.list_versions(kind, extension_id)
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/skills/inspect")
    async def api_inspect_skill_source(request: Request):
        try:
            body = await request.json()
            return await service.inspect_skill_source(str(body.get("url") or ""))
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/install")
    async def api_install_extension(request: Request):
        try:
            body = await request.json()
            kind = str(body.pop("kind", ""))
            extension_id = str(body.pop("extension_id", ""))
            task = service.start_install(kind, extension_id, body, actor="user")
            return {"ok": True, "task": task}
        except Exception as exc:
            return _error(exc)

    @router.delete("/api/extensions/{kind}/{extension_id}")
    async def api_uninstall_extension(kind: str, extension_id: str, version: str = ""):
        try:
            return await service.uninstall(kind, extension_id, version=version, actor="user")
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/toolchains/{extension_id}/default")
    async def api_set_default_toolchain(extension_id: str, request: Request):
        try:
            body = await request.json()
            return await service.set_default_version(extension_id, str(body.get("version") or ""), actor="user")
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/{kind}/{extension_id}/enabled")
    async def api_set_extension_enabled(kind: str, extension_id: str, request: Request):
        try:
            body = await request.json()
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            return await service.set_extension_enabled(kind, extension_id, enabled, actor="user")
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/bind")
    async def api_bind_system_extension(request: Request):
        try:
            body = await request.json()
            return service.bind_system_executable(str(body.get("extension_id") or ""), str(body.get("path") or ""))
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/unbind")
    async def api_unbind_system_extension(request: Request):
        body = await request.json()
        return service.unbind_system_executable(str(body.get("extension_id") or ""))

    @router.get("/api/extensions/tasks")
    async def api_extension_tasks():
        return {"tasks": service.tasks.list()}

    @router.get("/api/extensions/tasks/{task_id}")
    async def api_extension_task(task_id: str):
        task = service.tasks.get(task_id)
        return task or JSONResponse({"ok": False, "error": "task not found"}, status_code=404)

    @router.post("/api/extensions/tasks/{task_id}/cancel")
    async def api_cancel_extension_task(task_id: str):
        return {"ok": service.tasks.cancel(task_id)}

    @router.get("/api/extensions/sources")
    async def api_extension_sources():
        return source_settings()

    @router.put("/api/extensions/sources")
    async def api_update_extension_sources(request: Request):
        try:
            body = await request.json()
            return update_source_settings(body)
        except Exception as exc:
            return _error(exc)

    @router.post("/api/extensions/sources/test")
    async def api_test_extension_sources():
        settings = source_settings(include_secret=True)
        checks = {}
        targets = {
            "github": "https://api.github.com/rate_limit",
            "npm": str(settings.get("npm_registry") or "https://registry.npmjs.org").rstrip("/") + "/-/ping",
            "pip": str(settings.get("pip_index_url") or "https://pypi.org/simple").rstrip("/"),
            "mcp": str(settings.get("mcp_registry_url") or "https://registry.modelcontextprotocol.io").rstrip("/") + "/v0.1/health",
        }
        if settings.get("skill_catalog_url"):
            targets["skills"] = str(settings["skill_catalog_url"])
        headers = {"Authorization": f"Bearer {settings['github_token']}"} if settings.get("github_token") else {}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            for name, url in targets.items():
                try:
                    response = await client.get(url, headers=headers if name == "github" else None)
                    checks[name] = {"ok": response.status_code < 500, "status": response.status_code, "url": url}
                except Exception as exc:
                    checks[name] = {"ok": False, "error": str(exc), "url": url}
        return {"ok": all(item.get("ok") for item in checks.values()), "checks": checks}

    @router.get("/api/extensions/audit")
    async def api_extension_audit(limit: int = 200):
        return {"records": audit_records(limit)}

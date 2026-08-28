"""Plugin Center and Hook management routes owned by cyrene_cli."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from agent.plugin import PluginApplicationContext
from agent.plugin.plugin_impl.cyrene_extensions.extension_plugin_center import register_plugin_center_routes
from agent.plugin.plugin_impl.cyrene_extensions.extension_service import application_extension_service
from cyrene.localization import localized

from .service import CLIPluginService

logger = logging.getLogger(__name__)


def _error(exc: Exception, status_code: int = 400) -> JSONResponse:
    if not isinstance(exc, ValueError):
        logger.exception("CLI Plugin Center request failed", exc_info=exc)
    message = localized(
        "The CLI Hook was not found." if status_code == 404 else "The CLI Hook configuration is invalid.",
        "未找到 CLI Hook。" if status_code == 404 else "CLI Hook 配置无效。",
    ) if isinstance(exc, ValueError) else localized(
        "The CLI Hook request failed.",
        "CLI Hook 请求失败。",
    )
    return JSONResponse(
        {
            "ok": False,
            "code": "cli_hook_not_found" if status_code == 404 else "cli_hook_request_failed",
            "error": message,
        },
        status_code=status_code,
    )


async def _payload(request: Request) -> dict[str, Any]:
    value = await request.json()
    if not isinstance(value, dict):
        raise ValueError(localized(
            "The request body must be an object.",
            "请求正文必须是对象。",
        ))
    return dict(value)


def register_cli_hook_routes(
    context: PluginApplicationContext,
    service: CLIPluginService,
) -> None:
    prefix = "/api/plugin-center/cli/hooks"

    @context.router.get(prefix)
    async def api_cli_hooks():
        return service.hook_listing()

    @context.router.post(prefix)
    async def api_create_cli_hook(request: Request):
        try:
            return service.save_hook(await _payload(request))
        except Exception as exc:
            return _error(exc)

    @context.router.post(f"{prefix}/generate")
    async def api_generate_cli_hook(request: Request):
        try:
            return service.request_hook_generation(await _payload(request))
        except Exception as exc:
            return _error(exc)

    @context.router.post(f"{prefix}/{{hook_id}}/regenerate")
    async def api_regenerate_cli_hook(hook_id: str, request: Request):
        try:
            return service.retry_hook_generation(hook_id, await _payload(request))
        except ValueError as exc:
            return _error(exc, 404 if "not found" in str(exc).lower() else 400)
        except Exception as exc:
            return _error(exc)

    @context.router.put(f"{prefix}/{{hook_id}}")
    async def api_update_cli_hook(hook_id: str, request: Request):
        try:
            return service.save_hook(await _payload(request), hook_id=hook_id)
        except Exception as exc:
            return _error(exc)

    @context.router.delete(f"{prefix}/{{hook_id}}")
    async def api_delete_cli_hook(hook_id: str):
        try:
            return service.delete_hook(hook_id)
        except ValueError as exc:
            return _error(exc, 404)

    @context.router.post(f"{prefix}/{{hook_id}}/enabled")
    async def api_set_cli_hook_enabled(hook_id: str, request: Request):
        try:
            return service.set_hook_enabled(
                hook_id,
                (await _payload(request)).get("enabled"),
            )
        except Exception as exc:
            return _error(exc)

    @context.router.post(f"{prefix}/{{hook_id}}/test")
    async def api_test_cli_hook(hook_id: str, request: Request):
        try:
            return await service.test_hook(hook_id, await _payload(request))
        except ValueError as exc:
            return _error(exc, 404)
        except Exception as exc:
            return _error(exc)

    @context.router.get(f"{prefix}/audit")
    async def api_cli_hook_audit(limit: int = 200):
        return service.hook_audit(limit)

    @context.router.post(f"{prefix}/proposals/{{proposal_id}}/decision")
    async def api_decide_cli_hook_proposal(proposal_id: str, request: Request):
        try:
            return service.decide_hook_proposal(
                proposal_id,
                (await _payload(request)).get("approve"),
            )
        except ValueError as exc:
            return _error(exc, 404 if "not found" in str(exc) else 400)

    @context.router.post("/api/plugin-center/cli/{extension_id}/configure-hook")
    async def api_configure_cli_hook(extension_id: str):
        try:
            return service.configure_installed_cli(extension_id)
        except ValueError as exc:
            return _error(exc, 404)
        except Exception as exc:
            return _error(exc)


def setup_plugin_center(context: PluginApplicationContext) -> CLIPluginService:
    extensions = application_extension_service(context)
    if extensions is None:
        raise RuntimeError("cyrene_extensions application pack is unavailable")
    service = CLIPluginService(extensions)
    context.provide("cli", service)
    # Hook management paths must precede the generic CLI ``{extension_id:path}``
    # mutation routes or Starlette will treat ``hooks/<id>`` as an extension id.
    register_cli_hook_routes(context, service)
    register_plugin_center_routes(
        context.router,
        kind="cli",
        owner_pack="cyrene_cli",
        service=service,
    )
    return service


def setup_application(context: PluginApplicationContext) -> None:
    setup_plugin_center(context)
    context.expose_frontend("cli")

    from .config_agent import shutdown_background_tasks

    context.on_shutdown(shutdown_background_tasks)


__all__ = ["register_cli_hook_routes", "setup_application", "setup_plugin_center"]

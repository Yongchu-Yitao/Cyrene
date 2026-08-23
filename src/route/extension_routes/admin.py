from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.extensions.application_service import ExtensionApplicationError, ExtensionApplicationService
from route.extension_routes.errors import extension_error


def register_admin_routes(router: APIRouter, service: ExtensionApplicationService) -> None:
    @router.get("/api/extensions/tasks")
    async def api_extension_tasks():
        return service.tasks()

    @router.get("/api/extensions/tasks/{task_id}")
    async def api_extension_task(task_id: str):
        task = service.task(task_id)
        return task or JSONResponse({"ok": False, "error": "task not found"}, status_code=404)

    @router.post("/api/extensions/tasks/{task_id}/cancel")
    async def api_cancel_extension_task(task_id: str):
        return service.cancel_task(task_id)

    @router.get("/api/extensions/sources")
    async def api_extension_sources():
        return service.sources()

    @router.put("/api/extensions/sources")
    async def api_update_extension_sources(request: Request):
        body = await request.json()
        try:
            return service.update_sources(body)
        except ExtensionApplicationError as exc:
            return extension_error(exc)

    @router.post("/api/extensions/sources/test")
    async def api_test_extension_sources():
        return await service.test_sources()

    @router.get("/api/extensions/audit")
    async def api_extension_audit(limit: int = 200):
        return service.audit(limit)

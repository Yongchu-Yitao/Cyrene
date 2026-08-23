from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.learning.application_service import LearningApplicationService


def register_skill_command_routes(
    router: APIRouter, service: LearningApplicationService
) -> None:
    @router.post("/api/learned-skills/{skill_id}/update")
    async def api_update_learned_skill(skill_id: str, request: Request):
        payload = await request.json()
        updates = payload.get("updates") if isinstance(payload, dict) else None
        reason = str((payload or {}).get("reason") or "Manual skill edit.")
        result = await service.update_skill(
            skill_id, updates if isinstance(updates, dict) else {}, reason
        )
        if result is None:
            return JSONResponse(
                {"error": "skill not found or invalid payload"}, status_code=404
            )
        return {"ok": True, "skill": result}

    @router.post("/api/learned-skills/{skill_id}/rollback")
    async def api_rollback_learned_skill(skill_id: str, request: Request):
        payload = await request.json()
        result = await service.rollback(skill_id, int((payload or {}).get("version") or 0))
        if not result.get("ok"):
            return JSONResponse(result, status_code=404)
        return result

    @router.post("/api/learned-skills/{skill_id}/patches/{patch_id}/apply")
    async def api_apply_learned_skill_patch(skill_id: str, patch_id: str):
        result = await service.apply_patch(skill_id, patch_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/api/learned-skills/{skill_id}/patches/{patch_id}/reject")
    async def api_reject_learned_skill_patch(skill_id: str, patch_id: str):
        if not await service.reject_patch(skill_id, patch_id):
            return JSONResponse({"error": "patch not found"}, status_code=404)
        return {"ok": True}

    @router.post("/api/learned-skills/{skill_id}/activate")
    async def api_activate_learned_skill(skill_id: str):
        return {"ok": await service.activate(skill_id)}

    @router.post("/api/learned-skills/{skill_id}/deprecate")
    async def api_deprecate_learned_skill(skill_id: str):
        return {"ok": await service.deprecate(skill_id)}

    @router.post("/api/learned-skills/{skill_id}/delete")
    async def api_delete_learned_skill(skill_id: str):
        return {"ok": await service.delete(skill_id)}

    @router.post("/api/learned-skills/{skill_id}/run")
    async def api_run_learned_skill(skill_id: str):
        return await service.run(skill_id)

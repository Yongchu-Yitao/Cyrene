"""HTTP routes owned by the Skills Plugin pack."""

from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from cyrene.localization import localized
from cyrene.workbench.http.errors import localized_error_response

from .application_service import (
    LearningApplicationError,
    LearningApplicationService,
)


def register_learning_routes(
    router: APIRouter,
    service: LearningApplicationService,
) -> None:
    @router.get("/api/tool-chain-media")
    async def api_tool_chain_media(path: str = ""):
        try:
            target, media_type = await service.media_file(path)
        except LearningApplicationError as exc:
            return JSONResponse(
                {"error": exc.message, "code": exc.code},
                status_code=exc.status_code,
            )
        return FileResponse(target, media_type=media_type)

    @router.get("/api/evolution")
    async def api_evolution(project: str = "", compact: bool = False):
        del compact
        return await service.evolution(project)

    @router.get("/api/learned-skills")
    async def api_learned_skills(project: str = ""):
        return await service.learned_skills(project)

    @router.get("/api/tool-chains")
    async def api_tool_chains(project: str = "", limit: int = 80):
        return await service.chains(project, limit)

    @router.get("/api/skill-candidates")
    async def api_skill_candidates(project: str = "", status: str = "all"):
        return await service.candidates(project, status)

    @router.post("/api/skill-candidates/{candidate_id}/decision")
    async def api_skill_candidate_decision(candidate_id: str, request: Request):
        try:
            payload = await request.json()
        except JSONDecodeError:
            payload = {}
        result = await service.decide_candidate(
            candidate_id,
            str((payload or {}).get("decision") or ""),
        )
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    @router.get("/api/learned-skills/{skill_id}")
    async def api_learned_skill_detail(skill_id: str):
        skill = await service.skill(skill_id)
        if skill is None:
            return localized_error_response(
                "Skill not found.",
                "未找到技能。",
                404,
                "skill_not_found",
            )
        return {"skill": skill}

    @router.get("/api/learned-skills/{skill_id}/versions")
    async def api_learned_skill_versions(skill_id: str):
        return {"versions": await service.versions(skill_id)}

    @router.get("/api/learned-skills/{skill_id}/patches")
    async def api_learned_skill_patches(skill_id: str, status: str = "all"):
        return {"patches": await service.patches(skill_id, status)}

    @router.get("/api/learned-skills/{skill_id}/runs")
    async def api_learned_skill_runs(skill_id: str, limit: int = 50):
        return {"runs": await service.runs(skill_id, limit)}

    @router.post("/api/learned-skills/{skill_id}/update")
    async def api_update_learned_skill(skill_id: str, request: Request):
        payload = await request.json()
        updates = payload.get("updates") if isinstance(payload, dict) else None
        reason = str(
            (payload or {}).get("reason")
            or localized("Manual skill edit.", "手动编辑技能。")
        )
        result = await service.update_skill(
            skill_id,
            updates if isinstance(updates, dict) else {},
            reason,
        )
        if result is None:
            return localized_error_response(
                "Skill not found or the update is invalid.",
                "未找到技能或更新内容无效。",
                404,
                "skill_update_invalid",
            )
        return {"ok": True, "skill": result}

    @router.post("/api/learned-skills/{skill_id}/rollback")
    async def api_rollback_learned_skill(skill_id: str, request: Request):
        payload = await request.json()
        result = await service.rollback(
            skill_id,
            int((payload or {}).get("version") or 0),
        )
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
            return localized_error_response(
                "Patch not found.",
                "未找到补丁。",
                404,
                "skill_patch_not_found",
            )
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

    @router.post("/api/learning/process")
    async def api_learning_process(project: str = "", turn_id: str = ""):
        return await service.process(project, turn_id)

    @router.post("/api/learning/rebuild")
    async def api_learning_rebuild(project: str = ""):
        return await service.rebuild(project)


__all__ = ["register_learning_routes"]

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.learning.application_service import LearningApplicationService


def register_skill_query_routes(
    router: APIRouter, service: LearningApplicationService
) -> None:
    @router.get("/api/learned-skills/{skill_id}")
    async def api_learned_skill_detail(skill_id: str):
        skill = await service.skill(skill_id)
        if skill is None:
            return JSONResponse({"error": "skill not found"}, status_code=404)
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

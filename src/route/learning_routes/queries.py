from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from cyrene.learning.application_service import (
    LearningApplicationError,
    LearningApplicationService,
)


def register_learning_query_routes(
    router: APIRouter, service: LearningApplicationService
) -> None:
    @router.get("/api/tool-chain-media")
    async def api_tool_chain_media(path: str = ""):
        try:
            target, media_type = await service.media_file(path)
        except LearningApplicationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
        return FileResponse(target, media_type=media_type)

    @router.get("/api/evolution")
    async def api_evolution(project: str = "", compact: bool = False):
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
        except JSONDecodeError:  # Empty legacy request bodies resolve to an empty decision.
            payload = {}
        result = await service.decide_candidate(
            candidate_id, str((payload or {}).get("decision") or "")
        )
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

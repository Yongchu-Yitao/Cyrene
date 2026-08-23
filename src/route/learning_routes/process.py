from fastapi import APIRouter

from cyrene.learning.application_service import LearningApplicationService


def register_learning_process_routes(
    router: APIRouter, service: LearningApplicationService
) -> None:
    @router.post("/api/learning/process")
    async def api_learning_process(project: str = "", turn_id: str = ""):
        return await service.process(project, turn_id)

    @router.post("/api/learning/rebuild")
    async def api_learning_rebuild(project: str = ""):
        return await service.rebuild(project)

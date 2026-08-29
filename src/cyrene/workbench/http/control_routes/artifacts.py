"""Control artifact and chat-attachment download routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.control.control_services import ControlArtifactQueryService
from cyrene.workbench.http.control_schemas import ControlArtifactListResponse, ControlArtifactSummary
from cyrene.workbench.http.control_routes.common import COMMON_ERRORS, control_call, control_sync, file_response


def register_artifact_routes(router: APIRouter, service: ControlArtifactQueryService) -> None:
    @router.get("/v1/control/tasks/{task_id}/artifacts", response_model=ControlArtifactListResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_list_artifacts")
    async def control_list_artifacts(task_id: str):
        result = await control_call(service.list(task_id))
        if isinstance(result, JSONResponse):
            return result
        artifacts = []
        for item in result:
            if not isinstance(item, dict):
                continue
            artifact_id = str(item.get("id") or "")
            artifacts.append(ControlArtifactSummary(
                id=artifact_id, task_id=task_id,
                name=str(item.get("name") or item.get("path") or ""),
                type=str(item.get("type") or ""), created_at=str(item.get("createdAt") or ""),
                size=int(item["size"]) if item.get("size") is not None else None,
                download_url=f"/v1/control/tasks/{task_id}/artifacts/{artifact_id}",
            ))
        return ControlArtifactListResponse(artifacts=artifacts)

    @router.get(
        "/v1/control/tasks/{task_id}/artifacts/{artifact_id}",
        responses={**COMMON_ERRORS, 200: {"content": {"application/octet-stream": {}}}},
        tags=["Control"], operation_id="control_v1_read_artifact",
    )
    async def control_read_artifact(task_id: str, artifact_id: str):
        result = control_sync(lambda: service.download(task_id, artifact_id))
        return result if isinstance(result, JSONResponse) else file_response(result.path, result.filename, result.media_type)

    @router.get(
        "/v1/control/chats/{chat_id}/attachments/{attachment_id}",
        responses={**COMMON_ERRORS, 200: {"content": {"application/octet-stream": {}}}},
        tags=["Control"], operation_id="control_v1_read_chat_attachment",
    )
    async def control_read_chat_attachment(chat_id: str, attachment_id: str):
        result = await control_call(service.chat_attachment(chat_id, attachment_id))
        return result if isinstance(result, JSONResponse) else file_response(result.path, result.filename, result.media_type)


__all__ = ["register_artifact_routes"]

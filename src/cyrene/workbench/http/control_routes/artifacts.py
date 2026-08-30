"""Control chat-attachment download routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.control.control_services import ControlArtifactQueryService
from cyrene.workbench.http.control_routes.common import COMMON_ERRORS, control_call, file_response


def register_artifact_routes(router: APIRouter, service: ControlArtifactQueryService) -> None:
    @router.get(
        "/v1/control/chats/{chat_id}/attachments/{attachment_id}",
        responses={**COMMON_ERRORS, 200: {"content": {"application/octet-stream": {}}}},
        tags=["Control"], operation_id="control_v1_read_chat_attachment",
    )
    async def control_read_chat_attachment(chat_id: str, attachment_id: str):
        result = await control_call(service.chat_attachment(chat_id, attachment_id))
        return result if isinstance(result, JSONResponse) else file_response(result.path, result.filename, result.media_type)


__all__ = ["register_artifact_routes"]

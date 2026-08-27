"""Task event and artifact adapters."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse

from cyrene.workbench.task_services import TaskSessionNotFoundError
from route.errors import localized_error_response
from route.workbench.task_session_routes.context import TaskSessionRouteContext

logger = logging.getLogger(__name__)


def register_event_artifact_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.get("/api/task-sessions/{session_id}/events")
    async def api_workbench_session_events(session_id: str):
        try:
            return {"events": context.tasks.events(session_id)}
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )

    @router.get("/api/task-sessions/{session_id}/artifacts")
    async def api_workbench_session_artifacts(session_id: str):
        try:
            return {"artifacts": await context.artifacts.list(session_id)}
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )

    @router.get("/api/task-sessions/{session_id}/artifacts/{artifact_id}/download")
    async def api_workbench_download_artifact(session_id: str, artifact_id: str):
        try:
            download = context.artifacts.download(session_id, artifact_id)
        except TaskSessionNotFoundError:
            return localized_error_response(
                "Task session not found.",
                "未找到任务会话。",
                404,
                "task_session_not_found",
            )
        except LookupError:
            logger.info("Task artifact %s was not found", artifact_id, exc_info=True)
            return localized_error_response(
                "Artifact not found.",
                "未找到产物。",
                404,
                "artifact_not_found",
            )
        except ValueError:
            logger.warning("Invalid task artifact download request", exc_info=True)
            return localized_error_response(
                "The artifact download request is invalid.",
                "产物下载请求无效。",
                400,
                "invalid_artifact_request",
            )
        except FileNotFoundError:
            logger.info("Task artifact file %s is unavailable", artifact_id, exc_info=True)
            return localized_error_response(
                "The artifact file is unavailable.",
                "产物文件不可用。",
                404,
                "artifact_file_unavailable",
            )
        return FileResponse(download.path, filename=download.filename, media_type=download.media_type)

    return {"task_events": api_workbench_session_events, "task_artifacts": api_workbench_session_artifacts}

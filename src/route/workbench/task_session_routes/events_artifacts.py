"""Task event and artifact adapters."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from cyrene.workbench.task_services import TaskSessionNotFoundError
from route.workbench.task_session_routes.context import TaskSessionRouteContext


def register_event_artifact_routes(router: APIRouter, context: TaskSessionRouteContext):
    @router.get("/api/task-sessions/{session_id}/events")
    async def api_workbench_session_events(session_id: str):
        try:
            return {"events": context.tasks.events(session_id)}
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)

    @router.get("/api/task-sessions/{session_id}/artifacts")
    async def api_workbench_session_artifacts(session_id: str):
        try:
            return {"artifacts": await context.artifacts.list(session_id)}
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)

    @router.get("/api/task-sessions/{session_id}/artifacts/{artifact_id}/download")
    async def api_workbench_download_artifact(session_id: str, artifact_id: str):
        try:
            download = context.artifacts.download(session_id, artifact_id)
        except TaskSessionNotFoundError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return FileResponse(download.path, filename=download.filename, media_type=download.media_type)

    return {"task_events": api_workbench_session_events, "task_artifacts": api_workbench_session_artifacts}

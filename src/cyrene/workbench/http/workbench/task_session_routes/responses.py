"""Transport mapping shared by task-session route slices."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from cyrene.workbench.tasks.task_execution_service import TaskExecutionResponse


def service_response(result: Any):
    if isinstance(result, TaskExecutionResponse):
        payload = dict(result.payload)
        if result.status_code >= 400 and payload.get("error") and not payload.get("code"):
            payload["code"] = {
                400: "invalid_request",
                401: "authentication_required",
                403: "permission_denied",
                404: "not_found",
                409: "conflict",
                429: "rate_limited",
                500: "internal_server_error",
                502: "upstream_failure",
                503: "service_unavailable",
                504: "request_timeout",
            }.get(result.status_code, "request_failed")
        return JSONResponse(payload, status_code=result.status_code)
    return result

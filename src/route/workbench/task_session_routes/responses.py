"""Transport mapping shared by task-session route slices."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from cyrene.workbench.task_execution_service import TaskExecutionResponse


def service_response(result: Any):
    if isinstance(result, TaskExecutionResponse):
        return JSONResponse(result.payload, status_code=result.status_code)
    return result

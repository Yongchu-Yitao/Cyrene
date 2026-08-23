"""Control run query and command routes."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from cyrene.workbench.control_services import ControlRunService
from route import schemas as workbench_schemas
from route.control_schemas import (
    ControlGuidanceRequest, ControlGuidanceResponse, ControlInterruptResponse,
    ControlRunEventsResponse, ControlRunResponse,
)
from route.control_routes.common import COMMON_ERRORS, control_call, control_sync, run_event, run_response


def register_run_routes(router: APIRouter, service: ControlRunService) -> None:
    @router.get("/v1/control/runs/{run_id}", response_model=ControlRunResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_get_run")
    async def control_get_run(run_id: str):
        result = control_sync(lambda: service.replayable(run_id))
        return result if isinstance(result, JSONResponse) else run_response(result)

    @router.get("/v1/control/runs/{run_id}/events", response_model=ControlRunEventsResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_list_run_events")
    async def control_list_run_events(run_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)):
        page = control_sync(lambda: service.events(run_id, after=after, limit=limit))
        if isinstance(page, JSONResponse):
            return page
        return ControlRunEventsResponse(
            run_id=page.run_id, events=[run_event(item) for item in page.events],
            next_cursor=page.next_cursor, completed=page.completed, truncated=page.truncated,
        )

    @router.post("/v1/control/runs/{run_id}/guidance", response_model=ControlGuidanceResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_guide_run")
    async def control_guide_run(run_id: str, request: ControlGuidanceRequest):
        result = await control_call(service.guide(run_id, workbench_schemas.ChatGuidanceBody(message=request.message, clientRequestId=request.request_id or None)))
        return result if isinstance(result, JSONResponse) else ControlGuidanceResponse(**result)

    @router.post("/v1/control/runs/{run_id}/interrupt", response_model=ControlInterruptResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_interrupt_run")
    async def control_interrupt_run(run_id: str):
        result = await control_call(service.interrupt(run_id))
        if isinstance(result, JSONResponse):
            return result
        return ControlInterruptResponse(interrupted=result.interrupted, run_id=result.run_id, status=result.status)


__all__ = ["register_run_routes"]

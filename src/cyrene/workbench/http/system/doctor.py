"""Authenticated host-owned Doctor routes, independent of Plugin routes."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class DoctorRequest(BaseModel):
    project_id: str = Field(default="", max_length=120)
    chat_id: str = Field(default="", max_length=120)
    job_id: str = Field(default="", max_length=120)
    incident_id: str = Field(default="", max_length=80)
    client_code: str = Field(default="", pattern=r"^(|frontend_error|unhandled_rejection|network_error|http_[45][0-9]{2}|ui_error)$")
    language: str = "zh"


class AnalysisRequest(BaseModel):
    description: str = Field(default="", max_length=4000)


class RepairRequest(BaseModel):
    finding_id: str
    action_index: int = Field(default=0, ge=0, le=10)


def register_doctor_routes(router: APIRouter, service):
    async def invoke(operation):
        try:
            return await operation
        except FileNotFoundError:
            raise HTTPException(404, "Doctor report not found") from None
        except (ValueError, RuntimeError) as exc:
            from cyrene.platform.doctor.evidence import redact
            raise HTTPException(409, redact(str(exc))) from None

    @router.post("/api/doctor/reports")
    async def diagnose(request: DoctorRequest):
        return await invoke(service.diagnose(request.model_dump(exclude={"language"}), language=request.language))

    @router.get("/api/doctor/reports/{identifier}")
    async def get_report(identifier: str):
        try:
            result = service.get(identifier)
            return service.public_plan(result) if identifier.startswith("repair_") else result
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Doctor report not found") from None

    @router.post("/api/doctor/reports/{identifier}/analysis")
    async def analyze(identifier: str, request: AnalysisRequest | None = None):
        return await invoke(service.start_analysis(identifier, description=request.description if request else None))

    @router.delete("/api/doctor/reports/{identifier}/analysis")
    async def cancel(identifier: str):
        return await invoke(service.cancel_analysis(identifier))

    @router.post("/api/doctor/reports/{identifier}/probe")
    async def probe(identifier: str):
        return await invoke(service.probe_model(identifier))

    @router.post("/api/doctor/reports/{identifier}/repair-plan")
    async def plan(identifier: str, request: RepairRequest):
        return await invoke(service.plan_repair(identifier, request.finding_id, request.action_index))

    @router.post("/api/doctor/repairs/{identifier}/apply")
    async def apply(identifier: str):
        return await invoke(service.apply_repair(identifier))

    @router.post("/api/doctor/repairs/{identifier}/rollback")
    async def rollback(identifier: str):
        return await invoke(service.rollback_repair(identifier))

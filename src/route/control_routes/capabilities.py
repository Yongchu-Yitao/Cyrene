"""Control capability discovery route."""

from fastapi import APIRouter

from route.control_schemas import ControlCapabilitiesResponse, ControlFeature


CONTROL_OPERATIONS = [
    "capabilities.read", "projects.list", "chats.list", "chats.create", "chats.read",
    "chats.send", "runs.read", "runs.events", "runs.guide", "runs.interrupt",
    "tasks.list", "tasks.create", "tasks.read", "tasks.dispatch", "tasks.approve_plan",
    "tasks.run_step", "tasks.pause", "tasks.resume", "tasks.cancel", "approvals.respond",
    "artifacts.list", "artifacts.read", "attachments.read",
]


def register_capability_routes(router: APIRouter) -> None:
    @router.get(
        "/v1/control/capabilities", response_model=ControlCapabilitiesResponse,
        tags=["Control"], operation_id="control_v1_get_capabilities",
    )
    async def control_capabilities() -> ControlCapabilitiesResponse:
        return ControlCapabilitiesResponse(
            remote_transport_available=True,
            durable_run_events=True,
            operations=list(CONTROL_OPERATIONS),
            features=[
                ControlFeature(name="chat_runs", available=True, detail="Detached chat runs with cursor-addressable replay."),
                ControlFeature(name="durable_run_events", available=True, detail="Run metadata and events survive process restarts for seven days."),
                ControlFeature(name="remote_gateway", available=True, detail="Paired-device E2EE gateway with typed grants."),
                ControlFeature(name="remote_desktop", available=False, detail="Optional WebRTC takeover is not implemented."),
            ],
        )


__all__ = ["register_capability_routes"]

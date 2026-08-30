"""Control capability discovery route."""

from fastapi import APIRouter

from cyrene.localization import localized
from cyrene.workbench.http.control_schemas import ControlCapabilitiesResponse, ControlFeature


CONTROL_OPERATIONS = [
    "capabilities.read", "projects.list", "chats.list", "chats.create", "chats.read",
    "chats.send", "runs.read", "runs.events", "runs.guide", "runs.interrupt",
    "goals.read", "goals.update", "goals.confirm", "goals.pause", "goals.resume",
    "goals.abort", "goals.accept", "approvals.respond", "attachments.read",
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
                ControlFeature(
                    name="chat_runs",
                    available=True,
                    detail=localized(
                        "Detached chat runs with cursor-addressable replay.",
                        "支持游标定位回放的分离式对话运行。",
                    ),
                ),
                ControlFeature(
                    name="durable_run_events",
                    available=True,
                    detail=localized(
                        "Run metadata and events survive process restarts for seven days.",
                        "运行元数据与事件会跨进程重启保留七天。",
                    ),
                ),
                ControlFeature(
                    name="remote_gateway",
                    available=True,
                    detail=localized(
                        "Paired-device E2EE gateway with typed grants.",
                        "带类型化授权的配对设备端到端加密网关。",
                    ),
                ),
                ControlFeature(
                    name="remote_desktop",
                    available=False,
                    detail=localized(
                        "Optional WebRTC takeover is not implemented.",
                        "尚未实现可选的 WebRTC 接管。",
                    ),
                ),
            ],
        )


__all__ = ["register_capability_routes"]

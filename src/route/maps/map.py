"""Map-related HTTP adapters for the Web UI."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.conversation_context_service import SessionStateRepository


def register_map_routes(
    router: APIRouter,
    states: SessionStateRepository,
) -> None:
    @router.get("/api/map/pins")
    async def get_map_pins(session_id: str = ""):
        """Return all map pins and routes from the session state.

        Without ``session_id`` this reads the default session (legacy UI);
        with it, the per-session state file (workbench conversations).
        """
        state = states.read_map(session_id.strip())
        return JSONResponse({
            "pins": state.get("map_pins", []),
            "routes": state.get("map_routes", []),
        })

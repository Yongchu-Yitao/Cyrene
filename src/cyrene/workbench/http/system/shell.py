"""SPA shell and bootstrap routes."""

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from cyrene.workbench.artifacts.presentation_service import PresentationQueryService

_APP_DIR = Path(__file__).resolve().parents[2] / "webui" / "static" / "app"


def register_shell_routes(
    router: APIRouter,
    queries: PresentationQueryService,
    app_dir: Path = _APP_DIR,
) -> None:
    # ---- SPA root ----

    @router.get("/", response_class=HTMLResponse)
    async def spa_root(request: Request):
        return FileResponse(
            app_dir / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # ---- UI bootstrap data ----

    @router.get("/api/ui-data")
    async def api_ui_data(tz: str = ""):
        return await queries.ui_data(tz)

    @router.get("/api/dashboard")
    async def api_dashboard(tz: str = ""):
        return await queries.dashboard(tz)

"""SPA shell and bootstrap routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_shell_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- SPA root ----

    @router.get("/", response_class=HTMLResponse)
    async def spa_root(request: Request):
        return FileResponse(
            _APP_DIR / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # ---- UI bootstrap data ----

    @router.get("/api/ui-data")
    async def api_ui_data(tz: str = ""):
        return await _build_ui_data(tz)

    @router.get("/api/dashboard")
    async def api_dashboard(tz: str = ""):
        return await _build_dashboard(_resolve_ui_tz(tz))

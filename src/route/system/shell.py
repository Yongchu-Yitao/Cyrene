"""SPA shell and bootstrap routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_shell_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- SPA root ----

    @router.get("/", response_class=HTMLResponse)
    async def spa_root(request: Request):
        ui_mode = getattr(request.app.state, "ui_mode", "workbench")
        # Self-contained surfaces (e.g. the quick-chat window at
        # ?surface=quick-chat) render the same regardless of the main UI shell —
        # don't redirect them to the legacy shell or the surface param is lost.
        if (
            ui_mode == "legacy"
            and request.query_params.get("shell") != "legacy"
            and not request.query_params.get("surface")
        ):
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/?shell=legacy")
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

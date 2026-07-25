"""Scheduled task routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_task_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Scheduled tasks ----

    @router.get("/api/tasks")
    async def api_list_tasks():
        from cyrene import db as cy_db
        tasks = await cy_db.get_all_tasks(_db_path)
        return {"tasks": tasks}

    @router.post("/api/tasks")
    async def api_create_task(request: Request):
        from cyrene import db as cy_db
        from cyrene.schedule_spec import compute_next_run
        body = await request.json()
        stype = body["schedule_type"]
        svalue = body["schedule_value"]
        # REST API 端不允许创建 full_access 任务 ——
        # 用户需通过 chat agent 的 schedule_task 工具创建（会弹出确认对话框）
        permission_mode = "workspace_only"

        # Compute next_run if not provided by the frontend. An invalid schedule
        # is a 400 — never silently schedule for "now".
        next_run = body.get("next_run", "")
        if not next_run:
            try:
                next_run = compute_next_run(stype, svalue)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)

        task_id = await cy_db.create_task(
            _db_path,
            chat_id=body.get("chat_id", _CHAT_ID),
            prompt=body["prompt"],
            schedule_type=stype,
            schedule_value=svalue,
            next_run=next_run,
            permission_mode=permission_mode,
        )
        tasks = await cy_db.get_all_tasks(_db_path)
        return {"ok": True, "id": task_id, "tasks": tasks}

    @router.put("/api/tasks/{task_id}")
    async def api_update_task(task_id: str, request: Request):
        from cyrene import db as cy_db
        from cyrene.schedule_spec import compute_next_run
        body = await request.json()
        # Build SET clause dynamically from provided fields
        sets = []
        vals = []

        # If schedule_type or schedule_value changed, recalculate next_run.
        # An invalid schedule is a 400 rather than a silently-dropped update.
        stype = body.get("schedule_type")
        svalue = body.get("schedule_value")
        if stype and svalue and "next_run" not in body:
            try:
                body["next_run"] = compute_next_run(stype, svalue)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)

        # permission_mode 不可通过 REST API 修改 ——
        # 需通过 chat agent 的 schedule_task 工具重新创建（会弹出确认对话框）
        for field in ("prompt", "schedule_type", "schedule_value", "next_run", "status"):
            if field in body:
                sets.append(f"{field} = ?")
                vals.append(body[field])
        if sets:
            import aiosqlite
            async with aiosqlite.connect(_db_path) as db:
                await db.execute(
                    f"UPDATE scheduled_tasks SET {', '.join(sets)} WHERE id = ?",
                    (*vals, task_id),
                )
                await db.commit()
        tasks = await cy_db.get_all_tasks(_db_path)
        return {"ok": True, "tasks": tasks}

    @router.delete("/api/tasks/{task_id}")
    async def api_delete_task(task_id: str):
        from cyrene import db as cy_db
        await cy_db.delete_task(_db_path, task_id)
        tasks = await cy_db.get_all_tasks(_db_path)
        return {"ok": True, "tasks": tasks}

    @router.post("/api/shutdown")
    async def api_shutdown():
        """Shutdown the daemon."""
        import os as _os
        _os._exit(0)

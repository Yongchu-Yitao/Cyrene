"""FastAPI adapters for durable Workbench goal-loop execution."""

# The goal-loop service namespace is bound below; endpoint names intentionally
# resolve through that service facade.
# ruff: noqa: F821

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from route import schemas as api_models
from cyrene.workbench import goal_loop as _service

# The execution engine remains a UI-independent service.  Bind its private
# operations into this adapter namespace so the moved handlers retain their
# established behavior without putting FastAPI decorators back in the service.
globals().update({
    name: value
    for name, value in vars(_service).items()
    if not name.startswith("__")
})


def register_goal_loop_routes(router: APIRouter, app: Any, db_path: str) -> GoalLoopManager:
    manager = GoalLoopManager(str(db_path))
    _MANAGERS[str(db_path)] = manager
    app.state.goal_loop_manager = manager

    @router.post("/api/task-sessions/{session_id}/goal-loop/preview")
    async def preview_goal_loop(
        session_id: str, body_model: api_models.GoalLoopPreviewBody
    ):
        from cyrene.workbench import runtime as R

        body = api_models.body_dict(body_model)
        limits, error = _validate_limits(body)
        if not limits:
            return JSONResponse({"error": error}, status_code=400)
        try:
            _payload, project, session = _read_session(session_id)
        except KeyError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if str(session.get("status") or "") != "planning":
            return JSONResponse({"error": "只有计划确认阶段可以启动持续执行。", "code": "invalid_status"}, status_code=409)
        try:
            base_revision = int(body.get("basePlanDefinitionRevision"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid basePlanDefinitionRevision"}, status_code=400)
        if base_revision != int(session.get("planDefinitionRevision") or 0):
            return JSONResponse({"error": "计划已发生变化，请重新打开配置。", "code": "stale_plan_revision"}, status_code=409)
        try:
            current_run = await _get_run_by_session(db_path, session_id)
            # Check draft storage before an expensive planning-agent call. This
            # also clears expired rows while the database is known to be writable.
            await _execute(
                db_path,
                "DELETE FROM goal_loop_drafts WHERE expires_at < ?",
                (_utc_iso(),),
            )
        except Exception as exc:
            if not _sqlite_storage_busy(exc):
                raise
            logger.warning("Goal-loop preview storage is busy for session %s", session_id)
            return _storage_busy_response()
        if current_run and str(current_run.get("status") or "") not in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "该任务已有持续执行实例。", "code": "goal_loop_exists"}, status_code=409)

        goal = str(limits["goal"])
        goal_changed = goal.strip() != str(session.get("goal") or "").strip()
        draft_session = json.loads(_json_dumps(session))
        draft_session["goal"] = goal
        if goal_changed:
            draft_session["plan"] = []
            draft_session["acceptanceCriteria"] = []
            plan, acceptance, from_llm, _operation = await R._workbench_generate_plan_steps(
                draft_session,
                project,
                feedback="目标已由用户在持续执行配置中更新，请基于新目标重新生成完整计划。",
                requested_operation="replace",
            )
        else:
            plan = json.loads(_json_dumps(session.get("plan") or []))
            draft_session["plan"] = plan
            # Existing criteria are the user's explicit success contract. Do
            # not silently replace them with newly generated criteria when the
            # objective itself is unchanged: an LLM can introduce contradictory
            # requirements and make a correct result impossible to accept.
            existing_acceptance = [
                {
                    **json.loads(_json_dumps(item)),
                    "status": "pending",
                }
                for item in (session.get("acceptanceCriteria") or [])
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if existing_acceptance:
                acceptance = existing_acceptance
                from_llm = False
            else:
                acceptance, from_llm = await R._workbench_generate_acceptance_criteria(draft_session, project)
        if not plan:
            return JSONResponse({"error": "无法生成可执行计划。"}, status_code=503)
        if not acceptance:
            return JSONResponse({"error": "无法生成验收条件。"}, status_code=503)

        draft_id = f"goal_draft_{uuid.uuid4().hex[:16]}"
        now = _utc_now()
        expires_at = now + timedelta(minutes=30)
        try:
            await _execute(
                db_path,
                """
                INSERT INTO goal_loop_drafts
                (id, session_id, project_id, base_plan_revision, goal, goal_changed,
                 plan_json, acceptance_json, limits_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    session_id,
                    str(project.get("id") or ""),
                    base_revision,
                    goal,
                    1 if goal_changed else 0,
                    _json_dumps(plan),
                    _json_dumps(acceptance),
                    _json_dumps(limits),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        except Exception as exc:
            if not _sqlite_storage_busy(exc):
                raise
            logger.warning(
                "Goal-loop preview could not persist draft for session %s",
                session_id,
            )
            return _storage_busy_response()
        return {
            "ok": True,
            "draftId": draft_id,
            "goalChanged": goal_changed,
            "goal": goal,
            "plan": plan,
            "acceptanceCriteria": acceptance,
            "limits": limits,
            "planSource": "llm" if from_llm else "fallback",
            "expiresAt": expires_at.isoformat(),
        }

    @router.post("/api/task-sessions/{session_id}/goal-loop/start")
    async def start_goal_loop(
        session_id: str, body_model: api_models.GoalLoopStartBody
    ):
        async with manager.start_lock:
            return await _start_goal_loop_impl(session_id, body_model)

    async def _start_goal_loop_impl(
        session_id: str, body_model: api_models.GoalLoopStartBody
    ):
        body = api_models.body_dict(body_model)
        draft_id = str(body.get("draftId") or "").strip()
        draft = await _fetch_one(
            db_path,
            "SELECT * FROM goal_loop_drafts WHERE id = ? AND session_id = ?",
            (draft_id, session_id),
        )
        if not draft:
            existing = await _get_run_by_session(db_path, session_id)
            if existing and str(existing.get("status") or "") not in _TERMINAL_STATUSES | {"cancelled"}:
                return JSONResponse(
                    {"error": "该任务已有持续执行实例。", "code": "goal_loop_exists"},
                    status_code=409,
                )
            return JSONResponse({"error": "目标配置草稿不存在或已过期。", "code": "draft_not_found"}, status_code=404)
        try:
            if datetime.fromisoformat(str(draft["expires_at"])) <= _utc_now():
                return JSONResponse({"error": "目标配置草稿已过期，请重新生成。", "code": "draft_expired"}, status_code=409)
        except ValueError:
            return JSONResponse({"error": "目标配置草稿无效。"}, status_code=409)
        limits = _json_loads(draft.get("limits_json"), {})
        plan = _json_loads(draft.get("plan_json"), [])
        acceptance = _json_loads(draft.get("acceptance_json"), [])
        try:
            _payload, project, session = _read_session(session_id)
        except KeyError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        base_revision = int(draft.get("base_plan_revision") or 0)
        if base_revision != int(session.get("planDefinitionRevision") or 0):
            return JSONResponse({"error": "计划已发生变化，请重新生成目标配置。", "code": "stale_plan_revision"}, status_code=409)
        existing = await _get_run_by_session(db_path, session_id)
        if existing and str(existing.get("status") or "") not in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "该任务已有持续执行实例。", "code": "goal_loop_exists"}, status_code=409)
        if existing:
            await _execute(db_path, "DELETE FROM goal_runs WHERE id = ?", (str(existing["id"]),))

        next_revision = base_revision + (1 if bool(draft.get("goal_changed")) else 0)
        run_id = f"goal_run_{uuid.uuid4().hex[:16]}"
        now = _utc_iso()
        await _execute(
            db_path,
            """
            INSERT INTO goal_runs
            (id, session_id, project_id, objective, status, phase,
             plan_definition_revision, current_step_id, permission_mode,
             reflection_mode, max_active_seconds, max_repair_rounds,
             active_seconds, active_started_at, repair_round,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, 'running', 'executing', ?, NULL, ?, ?, ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                run_id,
                session_id,
                str(project.get("id") or ""),
                str(draft.get("goal") or ""),
                next_revision,
                str(limits.get("permissionMode") or "auto"),
                str(limits.get("reflectionMode") or "proactive"),
                int(limits.get("maxActiveSeconds") or 7200),
                int(limits.get("maxRepairRounds") or 3),
                now,
                now,
                now,
            ),
        )
        run = await _get_run_by_id(db_path, run_id)

        def apply(_payload: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
            fresh["goal"] = str(draft.get("goal") or "")
            fresh["plan"] = plan
            fresh["acceptanceCriteria"] = acceptance
            fresh["status"] = "running"
            fresh["planRevision"] = int(fresh.get("planRevision") or 0) + 1
            fresh["planDefinitionRevision"] = next_revision
            fresh["approvedPlanDefinitionRevision"] = next_revision
            fresh["goalLoop"] = _public_run(run)
            fresh["agentReply"] = "持续执行已启动，Agent 将执行计划并循环返工直到验收通过或达到退出条件。"
            fresh.setdefault("events", []).append({
                "id": R._short_id("event"),
                "type": "GoalLoopStarted",
                "createdAt": now,
                "body": "用户确认启动持续执行到验收通过。",
            })

        from cyrene.workbench import runtime as R

        try:
            payload, project, session = _write_session(session_id, apply)
        except Exception:
            # The run row is only a reservation until its Workbench projection
            # is durable. Remove it on failure so a retry is not rejected by a
            # phantom running instance.
            try:
                await _execute(db_path, "DELETE FROM goal_runs WHERE id = ?", (run_id,))
            except Exception:
                logger.exception("Failed to roll back unprojected goal-loop run %s", run_id)
            raise
        await _execute(db_path, "DELETE FROM goal_loop_drafts WHERE id = ?", (draft_id,))
        await _event(db_path, run_id, "started", payload={"limits": limits})
        if run:
            await _publish(run)
        manager.register_run(run_id, session_id)
        if manager.wake(run_id) is False:
            paused = await _set_inactive_status(
                db_path,
                run,
                "paused",
                phase="paused",
                stop_reason="run_conflict",
            )
            if paused:
                await manager._sync_projection(
                    paused,
                    message="任务已有其他运行，持续执行未启动并已安全暂停。",
                )
            payload, project, session = _read_session(session_id)
            return JSONResponse(
                {
                    "error": "该任务已有正在执行的请求，请等待完成或先停止它。",
                    "code": "task_run_in_progress",
                },
                status_code=409,
            )
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(run), **payload}

    @router.get("/api/task-sessions/{session_id}/goal-loop")
    async def get_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run:
            return {"ok": True, "goalLoop": None}
        events = await _fetch_all(
            db_path,
            "SELECT * FROM goal_run_events WHERE run_id = ? ORDER BY id DESC LIMIT 100",
            (str(run["id"]),),
        )
        return {
            "ok": True,
            "goalLoop": _public_run(run),
            "events": [
                {
                    "id": item["id"],
                    "type": item["event_type"],
                    "stepId": item.get("step_id") or "",
                    "payload": _json_loads(item.get("payload_json"), {}),
                    "createdAt": item.get("created_at") or "",
                }
                for item in reversed(events)
            ],
        }

    @router.post("/api/task-sessions/{session_id}/goal-loop/pause")
    async def pause_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") != "running":
            return JSONResponse({"error": "没有正在运行的持续任务。"}, status_code=409)
        manager.interrupt(session_id, reason="user_paused")
        interrupt_active_run(session_id=session_id)
        paused = await _set_inactive_status(db_path, run, "paused", phase="paused", stop_reason="user_paused")
        if paused:
            await _event(db_path, str(run["id"]), "paused")
            await manager._sync_projection(paused, message="持续执行已暂停，当前进度已保留。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(paused), **payload}

    @router.post("/api/task-sessions/{session_id}/goal-loop/resume")
    async def resume_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") not in _RESUMABLE_STATUSES:
            return JSONResponse({"error": "当前持续任务不能恢复。"}, status_code=409)
        now = _utc_iso()
        resumed = await _update_run(
            db_path,
            str(run["id"]),
            status="running",
            phase="executing",
            active_started_at=now,
            stop_reason=None,
            last_error=None,
            lease_owner=None,
            lease_until=None,
        )

        def apply(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
            for step in fresh.get("plan") or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("status") or "") == "running":
                    step["status"] = "pending"
                    step["startedAt"] = None
                # Give every not-yet-finished step a fresh per-step failure budget
                # so a resume after a stuck-step block is not blocked again at once.
                if str(step.get("status") or "") not in {"completed", "done", "skipped"}:
                    step["goalLoopAttempts"] = 0
            fresh["status"] = "running"
            fresh["goalLoop"] = _public_run(resumed)
            fresh["agentReply"] = "持续执行已恢复。"

        payload, project, session = _write_session(session_id, apply)
        if resumed:
            await _event(db_path, str(run["id"]), "resumed")
            await _publish(resumed)
            manager.register_run(str(run["id"]), session_id)
            if manager.wake(str(run["id"])) is False:
                paused = await _set_inactive_status(
                    db_path,
                    resumed,
                    "paused",
                    phase="paused",
                    stop_reason="run_conflict",
                )
                if paused:
                    await manager._sync_projection(
                        paused,
                        message="任务已有其他运行，持续执行未恢复并已安全暂停。",
                    )
                payload, project, session = _read_session(session_id)
                return JSONResponse(
                    {
                        "error": "该任务已有正在执行的请求，请等待完成或先停止它。",
                        "code": "task_run_in_progress",
                    },
                    status_code=409,
                )
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(resumed), **payload}

    @router.post("/api/task-sessions/{session_id}/goal-loop/cancel")
    async def cancel_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "没有可取消的持续任务。"}, status_code=409)
        manager.interrupt(session_id, reason="user_cancelled")
        interrupt_active_run(session_id=session_id)
        cancelled = await _set_inactive_status(
            db_path, run, "cancelled", phase="cancelled", stop_reason="user_cancelled"
        )
        if cancelled:
            await _event(db_path, str(run["id"]), "cancelled")
            await manager._sync_projection(cancelled, message="持续执行已取消，当前进度和文件改动已保留。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(cancelled), **payload}

    @router.patch("/api/task-sessions/{session_id}/goal-loop/limits")
    async def update_goal_loop_limits(
        session_id: str, body_model: api_models.GoalLoopLimitsBody
    ):
        body = api_models.body_dict(body_model)
        run = await _get_run_by_session(db_path, session_id)
        if not run:
            return JSONResponse({"error": "持续任务不存在。"}, status_code=404)
        try:
            max_hours = float(body.get("maxRuntimeHours", int(run["max_active_seconds"]) / 3600))
            max_repairs = int(body.get("maxRepairRounds", run["max_repair_rounds"]))
        except (TypeError, ValueError):
            return JSONResponse({"error": "退出条件格式无效。"}, status_code=400)
        if max_hours < 0.5 or max_hours > 24 or max_repairs < 0 or max_repairs > 10:
            return JSONResponse({"error": "退出条件超出允许范围。"}, status_code=400)
        reflection_mode = str(body.get("reflectionMode") or run.get("reflection_mode") or "proactive")
        if reflection_mode not in _REFLECTION_MODES:
            return JSONResponse({"error": "深度思考强度无效。"}, status_code=400)
        updated = await _update_run(
            db_path,
            str(run["id"]),
            max_active_seconds=int(max_hours * 3600),
            max_repair_rounds=max_repairs,
            reflection_mode=reflection_mode,
        )
        if updated:
            await manager._sync_projection(updated, message="持续执行限制已更新。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(updated), **payload}

    manager.control_adapter = {
        "get": get_goal_loop,
        "pause": pause_goal_loop,
        "resume": resume_goal_loop,
        "cancel": cancel_goal_loop,
    }
    return manager


__all__ = ["register_goal_loop_routes"]

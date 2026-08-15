"""Workbench task-session routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *
from route import schemas as api_models
from route.errors import error_response


def register_task_session_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
) -> dict[str, Any]:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    def agent_run_error_response(exc: _WorkbenchAgentRunError) -> JSONResponse:
        return JSONResponse(
            {"error": exc.message, "code": exc.code},
            status_code=exc.status_code,
        )

    def finalize_host_actions_after_reply(
        session_id: str,
        client_request_id: str = "",
    ) -> None:
        """Release deferred host actions only after this route persisted reply state."""
        from cyrene.runtime.host_actions import finalize_origin

        asyncio.create_task(finalize_origin(
            session_id,
            "",
            origin_run_id=client_request_id,
        ))

    def apply_task_model_preference(
        session_id: str,
        body: dict[str, Any],
        session: dict[str, Any],
    ):
        requested_model = str(body.get("model") or "").strip()
        selected_key = requested_model or str(
            session.get("modelSelectionId") or ""
        ).strip()
        if not selected_key:
            return None

        from cyrene.runtime.settings_store import get_models

        selected_candidate = next(
            (
                candidate
                for candidate in (get_models() or [])
                if selected_key
                in {
                    str(candidate.get("id") or "").strip(),
                    str(candidate.get("model") or "").strip(),
                    str(candidate.get("name") or "").strip(),
                }
            ),
            None,
        )
        if selected_candidate is None:
            if requested_model:
                return JSONResponse(
                    {"error": "configured model not found"},
                    status_code=400,
                )
            return None

        from cyrene.model_runtime.client import set_session_model_preference

        requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        selected_effort = requested_effort or str(
            session.get("reasoningEffort")
            or selected_candidate.get("reasoning_effort")
            or ""
        ).strip().lower()
        selected_model_id = str(
            selected_candidate.get("id") or selected_key
        ).strip()
        selected_model_name = str(
            selected_candidate.get("model")
            or selected_candidate.get("name")
            or selected_key
        ).strip()
        set_session_model_preference(
            session_id,
            selected_candidate,
            selected_effort,
        )
        session["modelSelectionId"] = selected_model_id
        session["model"] = selected_model_name
        session["reasoningEffort"] = selected_effort
        return None

    async def migrate_legacy_artifacts_if_needed(
        payload: dict[str, Any],
        project: dict[str, Any],
        session: dict[str, Any],
    ) -> None:
        if int(session.get("legacyArtifactModelMigrationVersion") or 0) >= 1:
            return
        before = int(session.get("legacyArtifactModelMigrationVersion") or 0)
        await _workbench_backfill_referenced_file_artifacts(
            project, session, _utc_now_iso(),
        )
        if int(session.get("legacyArtifactModelMigrationVersion") or 0) != before:
            _write_workbench_store(payload)

    @router.get("/api/task-sessions/{session_id}")
    async def api_workbench_get_session(session_id: str):
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return {
            "projectId": project.get("id") if project else "",
            "project": _workbench_project_shell(project),
            "session": session,
        }

    @router.get("/api/task-sessions/{session_id}/files/diff")
    async def api_workbench_file_diff(session_id: str, path: str = ""):
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        workspace_root = _workbench_workspace_root(project)
        recorded = _workbench_recorded_diff_for_path(session, path, workspace_root)
        if recorded and recorded.get("has_changes"):
            return recorded
        try:
            result = await _workbench_git_diff_for_path(workspace_root, path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except TimeoutError as exc:
            return JSONResponse({"error": str(exc)}, status_code=504)
        except RuntimeError:
            logger.exception(
                "Failed to compute Workbench diff for session %s", session_id
            )
            return error_response("Diff failed", 500, "workbench_diff_failed")
        if recorded and not (result.get("has_changes") and result.get("source") == "git"):
            return recorded
        return result

    @router.get("/api/task-sessions/{session_id}/workspace/exists")
    async def api_workbench_workspace_exists(session_id: str, path: str = ""):
        """Validate a context-file path for the per-step '相关文件' editor: confirm
        it resolves INSIDE the project workspace and exists. Returns the workspace-
        relative path so the client stores a normalized reference."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        root = _workbench_workspace_root(project)
        if not root:
            return JSONResponse({"error": "no workspace configured"}, status_code=400)
        raw = str(path or "").strip()
        if not raw:
            return {"exists": False, "path": "", "isDir": False}
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve()
            rel = resolved.relative_to(root).as_posix()
        except (ValueError, OSError):
            return JSONResponse({"exists": False, "path": raw, "error": "路径不在工作区内"}, status_code=400)
        exists = resolved.exists()
        return {"exists": exists, "path": rel, "isDir": resolved.is_dir() if exists else False}

    @router.patch("/api/task-sessions/{session_id}/plan")
    async def api_workbench_mutate_plan(
        session_id: str, body_model: api_models.PlanMutationBody
    ):
        body = api_models.body_dict(body_model)
        operation = str(body.get("operation") or "").strip().lower()
        requested_revision = body_model.basePlanRevision

        with _WORKBENCH_STORE_LOCK:
            payload = _read_workbench_store()
            project, session = _workbench_find_session(payload, session_id)
            if not session or not project:
                return JSONResponse({"error": "session not found"}, status_code=404)
            current_revision = int(session.get("planDefinitionRevision") or 0)
            if requested_revision != current_revision:
                return JSONResponse(
                    {"error": "计划已发生变化，请刷新后重试。", "code": "stale_plan_revision"},
                    status_code=409,
                )
            if is_session_running(session_id) or str(session.get("status") or "") in ("running", "waiting_for_user"):
                return JSONResponse(
                    {"error": "Agent 正在执行，暂时不能修改计划。", "code": "plan_running"},
                    status_code=409,
                )

            plan = _workbench_normalize_plan(
                session.get("plan"),
                task_id=session_id,
            )
            by_id = {
                str(step.get("id") or ""): step
                for step in plan
                if isinstance(step, dict)
            }
            structure_operation = operation in ("add", "reorder", "set_dependencies")
            fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
            if operation == "update" and any(
                field in fields for field in ("title", "description", "dependsOn")
            ):
                structure_operation = True
            if structure_operation and _workbench_plan_has_started(plan):
                return JSONResponse(
                    {"error": "计划已经开始执行，只能编辑尚未运行步骤的命令和上下文。", "code": "plan_started"},
                    status_code=409,
                )

            if operation == "add":
                step_input = body.get("step") if isinstance(body.get("step"), dict) else {}
                title = str(step_input.get("title") or "").strip()
                if not title:
                    return JSONResponse({"error": "步骤标题不能为空。", "code": "empty_step_title"}, status_code=400)
                if len(plan) >= 12:
                    return JSONResponse({"error": "执行计划最多包含 12 个步骤。", "code": "plan_too_large"}, status_code=400)
                new_step = _workbench_new_plan_step(
                    title[:160],
                    str(step_input.get("description") or "").strip()[:4000],
                    len(plan) + 1,
                    session_id,
                )
                new_step["dependsOn"] = _workbench_dependency_ids(step_input.get("dependsOn"))
                plan.append(new_step)
            elif operation == "update":
                step_id = str(body.get("stepId") or "").strip()
                target = by_id.get(step_id)
                if not target:
                    return JSONResponse({"error": "步骤不存在。", "code": "step_not_found"}, status_code=404)
                allowed_fields = {"title", "description", "dependsOn", "promptOverride", "contextFiles"}
                if any(field not in allowed_fields for field in fields):
                    return JSONResponse({"error": "包含不允许修改的步骤字段。", "code": "invalid_step_fields"}, status_code=400)
                if str(target.get("status") or "pending") != "pending":
                    return JSONResponse({"error": "只能编辑尚未运行的步骤。", "code": "step_started"}, status_code=409)
                if "title" in fields:
                    title = str(fields.get("title") or "").strip()
                    if not title:
                        return JSONResponse({"error": "步骤标题不能为空。", "code": "empty_step_title"}, status_code=400)
                    target["title"] = title[:160]
                if "description" in fields:
                    target["description"] = str(fields.get("description") or "").strip()[:4000]
                if "dependsOn" in fields:
                    target["dependsOn"] = _workbench_dependency_ids(fields.get("dependsOn"))
                if "promptOverride" in fields:
                    target["promptOverride"] = str(fields.get("promptOverride") or "")[:12000]
                if "contextFiles" in fields:
                    context_files = fields.get("contextFiles")
                    if not isinstance(context_files, list):
                        return JSONResponse({"error": "contextFiles must be a list"}, status_code=400)
                    target["contextFiles"] = context_files[:30]
            elif operation == "set_dependencies":
                step_id = str(body.get("stepId") or "").strip()
                target = by_id.get(step_id)
                if not target:
                    return JSONResponse({"error": "步骤不存在。", "code": "step_not_found"}, status_code=404)
                target["dependsOn"] = _workbench_dependency_ids(body.get("dependsOn"))
            elif operation == "delete":
                step_id = str(body.get("stepId") or "").strip()
                target = by_id.get(step_id)
                if not target:
                    return JSONResponse({"error": "步骤不存在。", "code": "step_not_found"}, status_code=404)
                if str(target.get("status") or "pending") != "pending":
                    return JSONResponse({"error": "只能删除尚未运行的步骤。", "code": "step_started"}, status_code=409)
                dependent_titles = [
                    str(step.get("title") or "")
                    for step in plan
                    if step_id in _workbench_dependency_ids(step.get("dependsOn"))
                ]
                if dependent_titles:
                    return JSONResponse(
                        {
                            "error": "该步骤仍被以下步骤依赖：" + "、".join(dependent_titles),
                            "code": "step_has_dependents",
                        },
                        status_code=409,
                    )
                plan = [step for step in plan if str(step.get("id") or "") != step_id]
            elif operation == "reorder":
                ordered_ids = _workbench_dependency_ids(body.get("orderedStepIds"))
                current_ids = [str(step.get("id") or "") for step in plan]
                if len(ordered_ids) != len(current_ids) or set(ordered_ids) != set(current_ids):
                    return JSONResponse({"error": "步骤顺序与当前计划不一致。", "code": "invalid_reorder"}, status_code=400)
                plan = [by_id[step_id] for step_id in ordered_ids]
            else:
                return JSONResponse({"error": "unsupported plan operation"}, status_code=400)

            plan = _workbench_normalize_plan(plan, task_id=session_id)
            valid, error_message, error_code = _workbench_validate_plan_graph(plan)
            if not valid:
                return JSONResponse(
                    {"error": error_message, "code": error_code},
                    status_code=400,
                )

            now = _utc_now_iso()
            session["plan"] = plan
            session["planRevision"] = int(session.get("planRevision") or 0) + 1
            session["planDefinitionRevision"] = current_revision + 1
            session["approvedPlanDefinitionRevision"] = None
            if str(session.get("status") or "") == "waiting_for_approval":
                session["status"] = "planning"
                session["agentReply"] = "计划已修改，请重新确认后执行。"
            session["events"] = list(session.get("events") or []) + [{
                "id": _short_id("event"),
                "type": "PlanUpdatedEvent",
                "createdAt": now,
                "body": {
                    "add": "新增执行步骤。",
                    "update": "更新执行步骤。",
                    "set_dependencies": "更新步骤依赖。",
                    "delete": "删除执行步骤。",
                    "reorder": "调整执行步骤顺序。",
                }.get(operation, "更新执行计划。"),
            }]
            session["updatedAt"] = now
            project["updatedAt"] = now
            payload["activeSessionId"] = session_id
            _write_workbench_store(payload)
            return {"ok": True, "project": project, "session": session, **payload}

    @router.patch("/api/task-sessions/{session_id}")
    async def api_workbench_update_session(
        session_id: str, body_model: api_models.SessionUpdateBody
    ):
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        prev_status = str(session.get("status") or "")
        requested_status = str(body.get("status") or "")
        if requested_status == "paused" and prev_status not in {
            "running",
            "waiting_for_user",
        }:
            return JSONResponse(
                {
                    "error": "only an active task can be paused",
                    "code": "invalid_status_transition",
                },
                status_code=409,
            )
        # A title coming through this user-facing endpoint is a manual edit (the
        # agent uses the set_task_goal tool, never HTTP) — lock it so the agent can
        # no longer override the title the user chose.
        if "title" in body and str(body.get("title") or "").strip():
            session["titleLocked"] = True
        for field in (
            "title", "goal", "status", "priority", "agentReply", "summary", "kind",
            "approvedPlanDefinitionRevision",
        ):
            if field in body:
                session[field] = body[field]
        for field in ("constraints", "events", "runs", "artifacts", "acceptanceCriteria"):
            if isinstance(body.get(field), list):
                session[field] = body[field]
        if isinstance(body.get("acceptanceCriteria"), list):
            # Editing a criterion invalidates the previous independent verdict.
            # Move the task back to review so the user can run验收 again instead
            # of leaving it stuck in the old failed branch.
            if prev_status == "failed":
                session["status"] = "review"
                session["verifyReason"] = ""
                session["recommendReflection"] = False
                session["agentReply"] = "验收条件已修改，请重新验收。"
        _workbench_prune_non_file_artifacts(session)
        if isinstance(body.get("plan"), list):
            previous_definition = _workbench_plan_definition_signature(session.get("plan"))
            next_plan = _workbench_normalize_plan(body["plan"], task_id=session_id)
            valid, error_message, error_code = _workbench_validate_plan_graph(next_plan)
            if not valid:
                return JSONResponse(
                    {"error": error_message, "code": error_code},
                    status_code=400,
                )
            session["plan"] = next_plan
            session["planRevision"] = int(session.get("planRevision") or 0) + 1
            if _workbench_plan_definition_signature(next_plan) != previous_definition:
                session["planDefinitionRevision"] = int(
                    session.get("planDefinitionRevision") or 0
                ) + 1
                session["approvedPlanDefinitionRevision"] = None
        if isinstance(body.get("init"), dict):
            session["init"] = {**(session.get("init") or {}), **body["init"]}
        now = _utc_now_iso()
        _workbench_mark_completed_if_acceptance_passed(session, now=now)
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        next_status = str(session.get("status") or "")
        if next_status != prev_status and next_status in ("done", "completed", "failed", "blocked", "paused", "review"):
            status_titles = {
                "done": "任务完成",
                "completed": "任务完成",
                "failed": "任务失败",
                "blocked": "任务阻塞",
                "paused": "任务已暂停",
                "review": "任务待验收",
            }
            status_labels = {
                "done": "已完成",
                "completed": "已完成",
                "failed": "失败",
                "blocked": "阻塞",
                "paused": "已暂停",
                "review": "待验收",
            }
            append_notification(
                title=status_titles.get(next_status, "任务状态更新"),
                body=f"任务「{session.get('title') or '未命名任务'}」当前状态：{status_labels.get(next_status, next_status)}。",
                tab="system" if next_status != "review" else "comment",
                project_ref=project.get("id"),
                source="task_status",
                source_label="任务",
                link_label=str(session.get("title") or ""),
                meta={"sessionId": session_id, "status": next_status},
            )
        return {"ok": True, "project": project, "session": session, **payload}

    @router.delete("/api/task-sessions/{session_id}")
    async def api_workbench_delete_session(session_id: str):
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)

        # Stop every writer before removing the entity. Otherwise a normal Agent
        # request can finish against its stale snapshot and resurrect the deleted
        # session through the document store's three-way merge.
        interrupt_active_run(session_id=session_id)
        from cyrene.workbench import goal_loop as goal_loop_service

        goal_run = await goal_loop_service._get_run_by_session(_db_path, session_id)
        if goal_run and str(goal_run.get("status") or "") not in {
            "review", "completed", "cancelled"
        }:
            cancelled = await goal_loop_service._set_inactive_status(
                _db_path,
                goal_run,
                "cancelled",
                phase="cancelled",
                stop_reason="session_deleted",
            )
            if cancelled:
                await goal_loop_service._event(
                    _db_path,
                    str(goal_run["id"]),
                    "cancelled",
                    payload={"reason": "session_deleted"},
                )
                await goal_loop_service._publish(cancelled)
            manager = goal_loop_service._MANAGERS.get(str(_db_path))
            worker = manager.tasks.get(str(goal_run["id"])) if manager else None
            if worker is not None and not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        for _attempt in range(100):
            if not is_session_running(session_id):
                break
            await asyncio.sleep(0.05)
        if is_session_running(session_id):
            return JSONResponse(
                {
                    "error": "任务仍在停止中，请稍后重试删除。",
                    "code": "session_still_running",
                },
                status_code=409,
            )
        await clear_session_id(session_id=session_id)

        with _WORKBENCH_STORE_LOCK:
            payload = _read_workbench_store()
            project, session = _workbench_find_session(payload, session_id)
            if not session or not project:
                return {"ok": True, **payload}
            project["sessions"] = [
                item
                for item in project.get("sessions", [])
                if str(item.get("id") or "") != session_id
            ]
            now = _utc_now_iso()
            project["updatedAt"] = now
            if str(payload.get("activeSessionId") or "") == session_id:
                remaining = project.get("sessions") or []
                payload["activeSessionId"] = remaining[0]["id"] if remaining else ""
            _write_workbench_store(payload)
        return {"ok": True, **payload}

    @router.post("/api/task-sessions/{session_id}/plan/generate")
    async def api_workbench_generate_plan(
        session_id: str, body_model: api_models.PlanGenerateBody
    ):
        """Generate a REAL execution plan for a task session.

        The agent reads the session goal + constraints and explores the project
        workspace, then returns ordered steps (all ``pending`` — nothing is run
        or pre-completed here). Drives the idle → planning transition.
        """
        body = api_models.body_dict(body_model)
        goal = str(body.get("goal") or "").strip()
        feedback = str(body.get("feedback") or "").strip()
        auto_start = bool(body.get("autoStart"))
        requested_operation = str(body.get("operation") or "auto").strip().lower()
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        base_plan_revision = int(session.get("planRevision") or 0)
        requested_base_revision = body.get("basePlanRevision")
        if requested_base_revision is not None:
            try:
                requested_base_revision = int(requested_base_revision)
            except (TypeError, ValueError):
                return JSONResponse({"error": "invalid basePlanRevision"}, status_code=400)
            if requested_base_revision != base_plan_revision:
                return JSONResponse(
                    {"error": "计划已发生变化，请基于最新计划重试。", "code": "stale_plan_revision"},
                    status_code=409,
                )

        if goal:
            session["goal"] = goal
            merged = list(session.get("constraints") or [])
            for item in await _workbench_extract_constraints(goal):
                if item not in merged:
                    merged.append(item)
            session["constraints"] = merged

        # If the revision feedback signals a goal-level miss (not a minor tweak),
        # deep-reflect first so the regenerated plan avoids the dead-ends. The
        # packet is stored on the session and consumed by plan generation below.
        should_reflect_before_replan = (
            feedback
            and requested_operation != "replace"
            and str(session.get("status") or "") in ("failed", "review")
        )
        if should_reflect_before_replan and await _workbench_should_reflect(
            str(session.get("goal") or ""), session.get("acceptanceCriteria") or [], feedback
        ):
            packet = await _workbench_run_reflection(
                session_id, focus=feedback, goal_gap="用户对当前计划/结果不满意：" + feedback
            )
            if packet:
                _workbench_store_reflection(session, packet, trigger="feedback", project=project)
                await _workbench_dispatch_reflection_hints(project, session, packet)
        steps, acceptance, from_llm, operation = await _workbench_generate_plan_steps(
            session,
            project,
            feedback=feedback,
            auto_start=auto_start,
            requested_operation=requested_operation,
        )
        latest_payload = _read_workbench_store()
        latest_project, latest_session = _workbench_find_session(latest_payload, session_id)
        if not latest_session or not latest_project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if int(latest_session.get("planRevision") or 0) != base_plan_revision:
            return JSONResponse(
                {"error": "计划已在生成期间发生变化，请基于最新计划重试。", "code": "stale_plan_revision"},
                status_code=409,
            )
        if not (feedback and not from_llm):
            latest_session["plan"] = steps
            latest_session["planRevision"] = base_plan_revision + 1
            latest_session["planDefinitionRevision"] = int(
                latest_session.get("planDefinitionRevision") or 0
            ) + 1
            latest_session["approvedPlanDefinitionRevision"] = None
            latest_session["acceptanceCriteria"] = acceptance
        for field in ("goal", "title", "constraints", "reflection", "planningThread"):
            if field in session:
                latest_session[field] = session[field]
        # Merge hint mutations from original project sessions into the fresh
        # payload.  _workbench_dispatch_reflection_hints mutated sessions
        # in-place on the original ``project``; copy those pendingHints /
        # events to ``latest_project`` so the final write persists them.
        _workbench_merge_hint_mutations(project, latest_project)
        latest_session["status"] = "planning"
        if from_llm:
            latest_session["agentReply"] = (
                "我已生成一份全新的执行计划，原计划不再作为当前步骤。"
                if operation == "replace" else
                "我已结合你的要求修订执行计划，并保留了可对应步骤的执行状态。"
                if operation == "revise" else
                "我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。"
            )
        else:
            latest_session["agentReply"] = (
                "计划调整未能生成有效结果，当前计划保持不变。你可以稍后重试。"
                if feedback else
                "计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。"
            )
        now = _utc_now_iso()
        latest_session["events"] = list(latest_session.get("events") or []) + [{
            "id": _short_id("event"),
            "type": "PlanRevised" if feedback else "PlanGenerated",
            "createdAt": now,
            "body": (
                f"{'整体替换' if operation == 'replace' else '修订'}执行计划，共 {len(steps)} 步。"
                if feedback else f"生成执行计划，共 {len(steps)} 步。"
            ) + ("" if from_llm else "（生成失败，保留原计划）"),
        }]
        latest_session["updatedAt"] = now
        latest_project["updatedAt"] = now
        latest_payload["activeSessionId"] = session_id
        _write_workbench_store(latest_payload)
        return {
            "ok": True,
            "project": latest_project,
            "session": latest_session,
            "planOperation": operation,
            "planSource": "llm" if from_llm else "fallback",
            **latest_payload,
        }

    @router.post("/api/task-sessions/{session_id}/acceptance/generate")
    async def api_workbench_generate_acceptance(session_id: str):
        """Generate fresh acceptance criteria from the current task and plan."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        criteria, from_llm = await _workbench_generate_acceptance_criteria(session, project)
        session["acceptanceCriteria"] = criteria
        now = _utc_now_iso()
        session["events"] = list(session.get("events") or []) + [{
            "id": _short_id("event"),
            "type": "AcceptanceGenerated",
            "createdAt": now,
            "body": f"生成验收标准，共 {len(criteria)} 条。" + ("" if from_llm else "（兜底标准）"),
        }]
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {
            "ok": True,
            "project": project,
            "session": session,
            "acceptanceSource": "llm" if from_llm else "fallback",
            **payload,
        }

    @router.post("/api/task-sessions/{session_id}/reflect")
    async def api_workbench_reflect(
        session_id: str, body_model: api_models.ReflectionBody
    ):
        """Run deep reflection over this task's history and attach the packet."""
        body = api_models.body_dict(body_model)
        focus = str(body.get("focus") or "").strip()
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        packet = await _workbench_run_reflection(
            session_id, focus=focus, goal_gap=str(body.get("goalGap") or "").strip()
        )
        if not packet:
            return JSONResponse({"error": "no history to reflect on"}, status_code=400)
        _workbench_store_reflection(session, packet, trigger="manual", project=project)
        await _workbench_dispatch_reflection_hints(project, session, packet)
        now = _utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/verify")
    async def api_workbench_verify(
        session_id: str, _body: api_models.EmptyBody | None = None
    ):
        """Independent acceptance agent verifies the criteria against results."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        try:
            verdict = await _workbench_verify_acceptance(session, project)
        except Exception as exc:
            error = _workbench_generation_error(exc)
            logger.warning(
                "Workbench verification failed for session %s: %s",
                session_id,
                error.message,
            )
            return JSONResponse(
                {
                    "error": f"验收暂时不可用：{error.message}",
                    "code": "verification_unavailable",
                    "category": error.category,
                },
                status_code=503,
            )
        if not isinstance(verdict, dict):
            return JSONResponse(
                {
                    "error": "验收暂时不可用：模型没有返回有效结果。",
                    "code": "verification_unavailable",
                    "category": "response_format",
                },
                status_code=503,
            )
        results = verdict.get("results") if isinstance(verdict.get("results"), list) else []
        by_id = {str(r.get("id")): r for r in results if isinstance(r, dict)}
        criteria = [a for a in (session.get("acceptanceCriteria") or []) if isinstance(a, dict)]
        any_failed = False
        for a in criteria:
            r = by_id.get(str(a.get("id")))
            if not isinstance(r, dict):
                a["status"] = "failed"
                a["evidence"] = "验收器未返回这一项的结论。"
                any_failed = True
                continue
            passed = bool(r.get("passed"))
            a["status"] = "passed" if passed else "failed"
            a["evidence"] = str(r.get("evidence") or "")
            if not passed:
                any_failed = True
        session["acceptanceCriteria"] = criteria
        recommend = bool(verdict.get("recommend_reflection")) if any_failed else False
        now = _utc_now_iso()
        if any_failed:
            session["status"] = "failed"
            session["verifyReason"] = str(verdict.get("reason") or "")
            session["recommendReflection"] = recommend
            session["agentReply"] = "独立验收未通过：" + str(verdict.get("reason") or "部分验收标准未达成。")
            session["events"] = list(session.get("events") or []) + [{
                "id": _short_id("event"), "type": "VerificationFailed", "createdAt": now,
                "body": "独立验收未通过。" + str(verdict.get("reason") or ""),
            }]
        else:
            session["recommendReflection"] = False
            session["verifyReason"] = ""
            session["agentReply"] = "独立验收通过：所有验收标准均已达成。"
            session["events"] = list(session.get("events") or []) + [{
                "id": _short_id("event"), "type": "VerificationPassed", "createdAt": now,
                "body": "独立验收通过，所有标准达成。",
            }]
            _workbench_mark_completed_if_acceptance_passed(
                session,
                now=now,
                event_body="独立验收通过，所有验收标准均已通过，任务自动标记为已完成。",
            )
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "verdict": verdict, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/reflect-and-fork")
    async def api_workbench_reflect_and_fork(
        session_id: str, _body: api_models.EmptyBody | None = None
    ):
        """Reflect on a (failed) task, then create a fresh sibling session that
        inherits the goal/constraints and carries the reflection packet so its
        plan avoids the dead-ends. Returns the new session (made active)."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        packet = await _workbench_run_reflection(
            session_id, goal_gap="任务验收未通过，需在新任务中换思路重试。"
        )
        project_id = str(project.get("id") or "")
        new_title = (str(session.get("title") or "任务") + " · 反思重试")[:80]
        new_session = _workbench_new_session(project_id, new_title, str(session.get("goal") or "").strip())
        new_session["constraints"] = list(session.get("constraints") or [])
        new_session["parentSessionId"] = session_id
        if isinstance(packet, dict) and packet:
            _workbench_store_reflection(new_session, packet, trigger="forked", source_session_id=session_id, project=project)
            # Nudge sibling open sessions before the fork joins the list (so it
            # isn't a candidate for its own packet).
            await _workbench_dispatch_reflection_hints(project, session, packet)
        project.setdefault("sessions", []).insert(0, new_session)
        now = _utc_now_iso()
        project["updatedAt"] = now
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = new_session["id"]
        _write_workbench_store(payload)
        return {"ok": True, "session": new_session, "sourceSessionId": session_id, **payload}

    def _workbench_find_pending_hint(session: dict[str, Any], hint_id: str) -> dict[str, Any] | None:
        hints = session.get("pendingHints") if isinstance(session.get("pendingHints"), list) else []
        return next((h for h in hints if isinstance(h, dict) and str(h.get("id")) == hint_id), None)

    @router.post("/api/task-sessions/{session_id}/hints/{hint_id}/accept")
    async def api_workbench_accept_hint(session_id: str, hint_id: str):
        """Accept a sibling-reflection hint: merge its packet into THIS session's
        reflection (so its next plan/run benefits) and mark the hint accepted."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        hint = _workbench_find_pending_hint(session, hint_id)
        if not hint:
            return JSONResponse({"error": "hint not found"}, status_code=404)
        packet = hint.get("packet") if isinstance(hint.get("packet"), dict) else None
        if packet:
            _workbench_store_reflection(
                session, packet, trigger="hint",
                source_session_id=str(hint.get("fromSessionId") or ""), project=project,
            )
        hint["status"] = "accepted"
        now = _utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/hints/{hint_id}/dismiss")
    async def api_workbench_dismiss_hint(session_id: str, hint_id: str):
        """Dismiss a sibling-reflection hint (no change to this session)."""
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        hint = _workbench_find_pending_hint(session, hint_id)
        if not hint:
            return JSONResponse({"error": "hint not found"}, status_code=404)
        hint["status"] = "dismissed"
        now = _utc_now_iso()
        session["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/runs")
    async def api_workbench_create_run(
        session_id: str, body_model: api_models.AgentInputBody
    ):
        body = api_models.body_dict(body_model)
        user_input = str(body.get("input") or body.get("message") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        mode = str(body.get("mode") or "auto")
        command = str(body.get("command") or "")
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        if not user_input and not attachments:
            return JSONResponse({"error": "input is required"}, status_code=400)

        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        model_error = apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error

        # Snapshot task-meta before any mutation so we can later detect what the
        # agent changed mid-run via set_task_goal and avoid clobbering it.
        task_meta_before = _workbench_capture_task_meta(session)
        # A per-step run (from runStep) executes one already-planned step — it must
        # NOT rebuild the plan / acceptance / goal / status; the client drives those.
        step_id = str(body.get("stepId") or "").strip()
        action = str(body.get("action") or "").strip()
        run_meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        is_step_run = bool(step_id) or action == "spawn_subagent"
        if is_step_run:
            plan = session.get("plan") if isinstance(session.get("plan"), list) else []
            step = next(
                (
                    item for item in plan
                    if isinstance(item, dict) and str(item.get("id") or "") == step_id
                ),
                None,
            )
            if not step:
                return JSONResponse({"error": "步骤不存在。", "code": "step_not_found"}, status_code=404)
            try:
                requested_definition_revision = int(body.get("planDefinitionRevision"))
            except (TypeError, ValueError):
                return JSONResponse({"error": "invalid planDefinitionRevision"}, status_code=400)
            current_definition_revision = int(session.get("planDefinitionRevision") or 0)
            if requested_definition_revision != current_definition_revision:
                return JSONResponse(
                    {"error": "计划已发生变化，请重新确认后执行。", "code": "stale_plan_revision"},
                    status_code=409,
                )
            approved_definition_revision = session.get("approvedPlanDefinitionRevision")
            try:
                approved_definition_revision = (
                    int(approved_definition_revision)
                    if approved_definition_revision is not None
                    else -1
                )
            except (TypeError, ValueError):
                approved_definition_revision = -1
            if approved_definition_revision != current_definition_revision:
                return JSONResponse(
                    {"error": "当前计划尚未获得执行确认。", "code": "plan_not_approved"},
                    status_code=409,
                )
            dependencies_ready, unmet_dependency_ids = _workbench_step_dependencies_satisfied(plan, step_id)
            if not dependencies_ready:
                titles_by_id = {
                    str(item.get("id") or ""): str(item.get("title") or "")
                    for item in plan if isinstance(item, dict)
                }
                return JSONResponse(
                    {
                        "error": "前置步骤尚未完成：" + "、".join(
                            titles_by_id.get(dependency_id, dependency_id)
                            for dependency_id in unmet_dependency_ids
                        ),
                        "code": "unmet_dependencies",
                    },
                    status_code=409,
                )

        run_started_at = _utc_now_iso()
        if not is_step_run:
            constraints = await _workbench_extract_constraints(user_input)
            merged_constraints = list(session.get("constraints") or [])
            for item in constraints:
                if item not in merged_constraints:
                    merged_constraints.append(item)
            if not session.get("goal") or session.get("status") == "idle":
                session["goal"] = user_input
            session["constraints"] = merged_constraints
            session["plan"] = _workbench_plan_from_input(user_input, session)
            session["acceptanceCriteria"] = _workbench_acceptance_from_session(session)
        else:
            constraints = []
        run_start_ts = run_started_at
        workspace_root = _workbench_workspace_root(project)
        git_status_before = _workbench_git_status_snapshot(workspace_root)
        workspace_files_before = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_before = _workbench_workspace_text_snapshot(workspace_root)
        memory_pair = _workbench_compose_memory_ephemeral(project, session)
        ephemeral_system = _workbench_compose_ephemeral_system(
            project, session, step_id=step_id if is_step_run else "", workspace_root=workspace_root, memory_pair=memory_pair
        )
        volatile_ephemeral_system = _workbench_compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_error = None
        try:
            agent_reply = await _workbench_agent_reply(user_input, session, constraints, attachments=attachments, permission_mode=mode, command=command, project_workspace=str(project.get("workspacePath") or ""), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=_workbench_compose_static_system(project, session), conversation_source="" if ui_instance_id else "webui", ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except _WorkbenchAgentRunError as exc:
            agent_error = exc
            agent_reply = exc.message
        git_status_after = _workbench_git_status_snapshot(workspace_root)
        workspace_files_after = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_after = _workbench_workspace_text_snapshot(workspace_root)
        # A run that hit a permission / clarification boundary pauses awaiting the
        # user's answer — surface the question on the card instead of the sentinel.
        if agent_error is None:
            agent_reply, awaiting_user = _workbench_apply_pending(session, session_id, agent_reply)
        else:
            awaiting_user = False
        if is_step_run and awaiting_user:
            session["pendingPlanStep"] = {
                "stepId": step_id,
                "continueAll": bool(run_meta.get("continueAll")),
            }
        elif is_step_run:
            session.pop("pendingPlanStep", None)
        # Preserve any title/goal/summary the agent changed mid-run via set_task_goal.
        _workbench_sync_agent_task_meta(session, session_id, task_meta_before)
        session["agentReply"] = agent_reply
        # Generate step outcome for context cascade (best-effort, short timeout).
        if is_step_run and not awaiting_user and agent_error is None and step:
            try:
                await asyncio.wait_for(
                    _workbench_generate_step_outcome(step, agent_reply, user_input),
                    timeout=10,
                )
            except (asyncio.TimeoutError, Exception):
                pass
        # Sink durable memories from this exchange into the project's workspace store.
        if not command and not awaiting_user and agent_error is None:
            schedule_capture(_workbench_project_memory_key(project), user_input, agent_reply)
        if agent_error is not None and not is_step_run:
            session["status"] = "failed"
        elif not is_step_run and not awaiting_user:
            session["status"] = "planning" if session.get("status") in ("idle", "pending") else session.get("status", "planning")
        normalized_attachments = _workbench_normalize_attachments(attachments)
        public_attachments = [build_public_attachment_payload(item) for item in normalized_attachments]
        run_id = _short_id("run")
        activity_events = _collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [event for event in activity_events if event.get("type") == "ToolCallEvent"]
        file_changes = _workbench_collect_run_file_changes(
            tool_call_events,
            git_status_before,
            git_status_after,
            workspace_files_before,
            workspace_files_after,
            workspace_root,
            f"{user_input}\n{agent_reply}",
            workspace_text_before=workspace_text_before,
            workspace_text_after=workspace_text_after,
        )
        finished_at = _utc_now_iso()
        if is_step_run and step_id:
            _workbench_apply_step_file_changes(session, step_id, file_changes)
        if is_step_run and agent_error is not None and step:
            step["status"] = "failed"
            step["updatedAt"] = finished_at
            step["currentAction"] = agent_error.message
            session["planRevision"] = int(session.get("planRevision") or 0) + 1
            session["status"] = "failed"
        elif is_step_run and not awaiting_user and step:
            step["status"] = "completed"
            step["completedAt"] = finished_at
            step["updatedAt"] = finished_at
            step["currentAction"] = (
                f"已完成，本步调用工具 {len(tool_call_events)} 次。"
                if tool_call_events else "已完成该步骤。"
            )
            step["toolCalls"] = [
                {"tool": event["tool"], "argsPreview": event["argsPreview"]}
                for event in tool_call_events
            ]
            started_at = str(step.get("startedAt") or run_started_at)
            try:
                duration = round(
                    (
                        datetime.fromisoformat(finished_at)
                        - datetime.fromisoformat(started_at)
                    ).total_seconds()
                )
                if duration >= 1:
                    step["durationSec"] = duration
            except (TypeError, ValueError):
                pass
            session["planRevision"] = int(session.get("planRevision") or 0) + 1
            unresolved = [
                item
                for item in (session.get("plan") or [])
                if isinstance(item, dict)
                and str(item.get("status") or "pending")
                not in {"completed", "done", "skipped"}
            ]
            session["status"] = (
                "review"
                if not unresolved
                else "running"
                if bool(run_meta.get("continueAll"))
                else "paused"
            )
        events = [
            {"id": _short_id("event"), "type": "UserMessageEvent", "runId": run_id, "createdAt": run_started_at, "body": user_input or "[附件]", "attachments": public_attachments},
            *activity_events,
            {"id": _short_id("event"), "type": "AgentErrorEvent" if agent_error else "AgentResponseEvent", "runId": run_id, "createdAt": finished_at, "body": agent_reply},
            {"id": _short_id("event"), "type": "PlanUpdatedEvent", "runId": run_id, "createdAt": finished_at, "stepCount": len(session.get("plan") or [])},
        ]
        if is_step_run and not awaiting_user and step:
            events.append({
                "id": _short_id("event"),
                "type": "ExecutionFailed" if agent_error else "ExecutionFinished",
                "runId": run_id,
                "stepId": step_id,
                "createdAt": finished_at,
                "body": (
                    f"步骤「{step.get('title') or step_id}」执行失败：{agent_error.message}"
                    if agent_error
                    else f"步骤「{step.get('title') or step_id}」执行完成。"
                ),
            })
        run = {
            "id": run_id,
            "taskId": session_id,
            "userInput": user_input,
            "agentResponse": agent_reply,
            "status": "failed" if agent_error else "completed",
            "startedAt": run_started_at,
            "endedAt": finished_at,
            "contextPackId": _short_id("ctx"),
            "events": events,
            "fileChanges": file_changes,
            "toolCalls": [{"tool": e["tool"], "argsPreview": e["argsPreview"]} for e in tool_call_events],
            "artifacts": [],
            "attachments": public_attachments,
            "mode": mode,
            "error": agent_error.message if agent_error else None,
        }
        session.setdefault("runs", []).append(run)
        session.setdefault("events", []).extend(events)
        _workbench_promote_file_artifacts(session, file_changes, finished_at, workspace_root)
        if not awaiting_user and agent_error is None:
            await _workbench_archive_run_knowledge(
                project, session, run, workspace_root, finished_at,
            )
        session["updatedAt"] = finished_at
        project["updatedAt"] = finished_at
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        finalize_host_actions_after_reply(session_id, client_request_id)
        append_notification(
            title="任务执行失败" if agent_error else "任务回复完成",
            body=(
                f"Agent 执行任务「{session.get('title') or '未命名任务'}」失败：{agent_error.message}"
                if agent_error
                else f"Agent 已更新任务「{session.get('title') or '未命名任务'}」。"
            ),
            tab="comment",
            project_ref=project.get("id"),
            source="task_reply",
            source_label="任务",
            link_label=str(session.get("title") or ""),
            meta={"sessionId": session_id, "runId": run_id},
        )
        if agent_error is not None:
            return agent_run_error_response(agent_error)
        return {"ok": True, "project": project, "session": session, "run": run, **payload}

    @router.post("/api/task-sessions/{session_id}/chat")
    async def api_workbench_session_chat(
        session_id: str, body_model: api_models.AgentInputBody
    ):
        """Simple chat mode — returns agent reply without generating plans/steps."""
        body = api_models.body_dict(body_model)
        message = str(body.get("message") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        mode = str(body.get("mode") or "auto")
        command = str(body.get("command") or "")
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        if not message and not attachments:
            return JSONResponse({"error": "message is required"}, status_code=400)

        # ── Budget gate ──
        _bgt = await _check_budget_gate(session_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        model_error = apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        task_meta_before = _workbench_capture_task_meta(session)
        chat_run_start_ts = _utc_now_iso()
        workspace_root = _workbench_workspace_root(project)
        git_status_before = _workbench_git_status_snapshot(workspace_root)
        workspace_files_before = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_before = _workbench_workspace_text_snapshot(workspace_root)
        memory_pair = _workbench_compose_memory_ephemeral(project, session)
        ephemeral_system = _workbench_compose_ephemeral_system(
            project, session, workspace_root=workspace_root, memory_pair=memory_pair
        )
        ephemeral_system = (ephemeral_system + "\n\n" + _WORKBENCH_TASK_REPLY_DIRECTIVE).strip()
        volatile_ephemeral_system = _workbench_compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_command = command or "workbench-task-reply"
        try:
            agent_reply = await _workbench_agent_reply(message, session, [], attachments=attachments, permission_mode=mode, command=agent_command, project_workspace=str(project.get("workspacePath") or ""), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=_workbench_compose_static_system(project, session), conversation_source="" if ui_instance_id else "webui", ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except _WorkbenchAgentRunError as exc:
            return agent_run_error_response(exc)
        git_status_after = _workbench_git_status_snapshot(workspace_root)
        workspace_files_after = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_after = _workbench_workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = _workbench_apply_pending(session, session_id, agent_reply)
        # Preserve any title/goal/summary the agent changed mid-run via set_task_goal.
        _workbench_sync_agent_task_meta(session, session_id, task_meta_before)
        session["agentReply"] = agent_reply
        # Sink durable memories from this exchange into the project's workspace store.
        if not command and not awaiting_user:
            schedule_capture(_workbench_project_memory_key(project), message, agent_reply)
        session["status"] = "waiting_for_user" if awaiting_user else "completed"
        now = _utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        chat_run_id = _short_id("run")
        chat_tool_events = _collect_run_tool_events(session_id, chat_run_start_ts, chat_run_id, workspace_root)
        file_changes = _workbench_collect_run_file_changes(
            chat_tool_events,
            git_status_before,
            git_status_after,
            workspace_files_before,
            workspace_files_after,
            workspace_root,
            f"{message}\n{agent_reply}",
            workspace_text_before=workspace_text_before,
            workspace_text_after=workspace_text_after,
        )
        if chat_tool_events:
            session.setdefault("events", []).extend(chat_tool_events)
        _workbench_promote_file_artifacts(session, file_changes, now, workspace_root)
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        finalize_host_actions_after_reply(session_id, client_request_id)
        append_notification(
            title="Agent 回复完成",
            body=f"Agent 在「{session.get('title') or '对话'}」中回复了你。",
            tab="mention",
            project_ref=project.get("id"),
            source="chat_reply",
            source_label="对话",
            link_label=str(session.get("title") or ""),
            meta={"sessionId": session_id},
        )
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/dispatch")
    async def api_workbench_dispatch(
        session_id: str, body_model: api_models.AgentInputBody
    ):
        """Intent-aware entry for the task composer.

        Classifies the input and routes it: a question → a direct answer; a
        one-shot instruction → execute it and report what changed; a complex goal
        → generate a plan; a completion/handoff signal ("done", "可以验收了") →
        summarize the existing deliverables and move to review (no re-planning).
        Only the plan branch enters the planning/approval flow; answer/direct/
        finalize return an agent reply with no plan/steps. ``replyKind`` tells the
        client which card to render.
        """
        body = api_models.body_dict(body_model)
        user_input = str(body.get("input") or body.get("message") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        mode = str(body.get("mode") or "auto")
        command = str(body.get("command") or "")
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        client_request_id = str(body.get("clientRequestId") or "").strip()
        requested_base_revision = body.get("basePlanRevision")
        if not user_input and not attachments:
            return JSONResponse({"error": "input is required"}, status_code=400)
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        model_error = apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error

        # ── Budget gate at dispatch entry (before any LLM call) ──
        _bgt = await _check_budget_gate(session_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        # Snapshot task-meta before any mutation so we can later detect what the
        # agent changed mid-run via set_task_goal and avoid clobbering it.
        task_meta_before = _workbench_capture_task_meta(session)
        should_generate_title = bool(
            user_input
            and not session.get("titleLocked")
            and not session.get("titleNamingStatus")
            and _workbench_is_default_title(session.get("title"))
        )
        if should_generate_title:
            from cyrene.workbench.session_naming import generate_session_title
            from cyrene.model_runtime.client import resolve_session_model_candidate

            session["titleNamingStatus"] = "pending"
            session["titleNamingStartedAt"] = _utc_now_iso()
            naming_candidate = resolve_session_model_candidate(session_id)
            candidate_id = str((naming_candidate or {}).get("id") or "")
            candidate_model = str((naming_candidate or {}).get("model") or "")
            logger.info(
                "Workbench task session naming started "
                "[session=%s candidate=%s model=%s input_chars=%d]",
                session_id,
                candidate_id or "unresolved",
                candidate_model or "unresolved",
                len(user_input),
            )
            try:
                if naming_candidate is None:
                    raise RuntimeError("no configured model candidate for task session")
                generated_title = await generate_session_title(
                    user_input,
                    limit=80,
                    candidate=naming_candidate,
                )
            except Exception as exc:
                logger.exception(
                    "Workbench task session naming failed "
                    "[session=%s candidate=%s model=%s error_type=%s]",
                    session_id,
                    candidate_id or "unresolved",
                    candidate_model or "unresolved",
                    type(exc).__name__,
                )
                generated_title = ""
            if generated_title and not session.get("titleLocked"):
                session["title"] = generated_title
                session["titleNamingStatus"] = "generated"
                session["titleGeneratedAt"] = _utc_now_iso()
            else:
                session["titleNamingStatus"] = "failed"
            logger.info(
                "Workbench task session naming finished "
                "[session=%s candidate=%s model=%s status=%s output_chars=%d]",
                session_id,
                candidate_id or "unresolved",
                candidate_model or "unresolved",
                session["titleNamingStatus"],
                len(generated_title),
            )
        # A slash command or attachment-only message is already a concrete action —
        # skip classification and treat it as a direct instruction.
        if command or (not user_input and attachments):
            kind = "direct"
        else:
            kind = await _workbench_classify_intent(user_input, session)

        now = _utc_now_iso()
        # Seed goal/title from the first ACTIONABLE input so the task
        # gets a real identity — but not for a pure question (kind=="answer") or a
        # completion signal (kind=="finalize"): neither is a task goal. The agent
        # can still set/correct goal+title at any time via the set_task_goal tool.
        if kind not in ("answer", "finalize") and _workbench_is_blank_goal(session.get("goal")) and user_input:
            session["goal"] = user_input
            if _workbench_is_default_title(session.get("title")):
                session["title"] = _workbench_derive_title(user_input)
        # Constraints are semantic requirements and may be added in later turns,
        # so analyze every actionable task/direct input instead of only the first
        # goal or matching a few trigger words.
        if kind in ("plan", "direct") and user_input:
            merged = list(session.get("constraints") or [])
            for item in await _workbench_extract_constraints(user_input):
                if item not in merged:
                    merged.append(item)
            session["constraints"] = merged

        if kind == "plan":
            # A plan already exists → treat this as a REVISION (pass the input as
            # feedback) so _workbench_generate_plan_steps reconciles + preserves
            # completed/in-progress steps, rather than wiping progress with a
            # fresh plan. No plan yet → first-time generation.
            existing_plan = session.get("plan") if isinstance(session.get("plan"), list) else []
            revising = bool(existing_plan)
            base_plan_revision = int(session.get("planRevision") or 0)
            if requested_base_revision is not None:
                try:
                    requested_base_revision = int(requested_base_revision)
                except (TypeError, ValueError):
                    return JSONResponse({"error": "invalid basePlanRevision"}, status_code=400)
                if requested_base_revision != base_plan_revision:
                    return JSONResponse(
                        {"error": "计划已发生变化，请基于最新计划重试。", "code": "stale_plan_revision"},
                        status_code=409,
                    )
            steps, acceptance, from_llm, operation = await _workbench_generate_plan_steps(
                session, project, feedback=(user_input if revising else "")
            )
            latest_payload = _read_workbench_store()
            latest_project, latest_session = _workbench_find_session(latest_payload, session_id)
            if not latest_session or not latest_project:
                return JSONResponse({"error": "session not found"}, status_code=404)
            if int(latest_session.get("planRevision") or 0) != base_plan_revision:
                return JSONResponse(
                    {"error": "计划已在生成期间发生变化，请基于最新计划重试。", "code": "stale_plan_revision"},
                    status_code=409,
                )
            if not (revising and not from_llm):
                latest_session["plan"] = steps
                latest_session["planRevision"] = base_plan_revision + 1
                latest_session["planDefinitionRevision"] = int(
                    latest_session.get("planDefinitionRevision") or 0
                ) + 1
                latest_session["approvedPlanDefinitionRevision"] = None
                latest_session["acceptanceCriteria"] = acceptance
            for field in (
                "goal",
                "title",
                "constraints",
                "reflection",
                "modelSelectionId",
                "model",
                "reasoningEffort",
                "titleNamingStatus",
                "titleNamingStartedAt",
                "titleGeneratedAt",
            ):
                if field in session:
                    latest_session[field] = session[field]
            latest_session["status"] = "planning"
            if revising:
                latest_session["agentReply"] = (
                    "我判断这次要求需要整体替换计划，已生成全新步骤。"
                    if from_llm and operation == "replace" else
                    "我已按你的说明修订执行计划，并保留了可对应步骤的执行状态。"
                    if from_llm else
                    "计划调整服务暂时不可用，已保留原计划。你可以稍后再让我调整。"
                )
            else:
                latest_session["agentReply"] = (
                    "我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。"
                    if from_llm else
                    "计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。"
                )
            latest_session["events"] = list(latest_session.get("events") or []) + [{
                "id": _short_id("event"),
                "type": "PlanRevised" if revising else "PlanGenerated",
                "createdAt": now,
                "body": (
                    f"{'整体替换' if operation == 'replace' else '修订'}执行计划，共 {len(steps)} 步。"
                    if revising else f"生成执行计划，共 {len(steps)} 步。"
                ) + (
                    "" if from_llm else
                    "（生成失败，保留原计划）" if revising else
                    "（兜底计划）"
                ),
            }]
            latest_session["updatedAt"] = now
            latest_project["updatedAt"] = now
            latest_payload["activeSessionId"] = session_id
            _write_workbench_store(latest_payload)
            return {
                "ok": True,
                "replyKind": "plan",
                "planOperation": operation,
                "planSource": "llm" if from_llm else "fallback",
                "project": latest_project,
                "session": latest_session,
                **latest_payload,
            }

        # answer / direct / finalize — run a real agent reply (no plan generated),
        # then collect any tool activity + file changes the run produced so the card
        # can report what actually happened. finalize additionally instructs the
        # agent to summarize+hand off the existing deliverables and lands in review.
        finalizing = kind == "finalize"
        repairing_acceptance = command == "workbench-task-repair"
        run_start_ts = _utc_now_iso()
        workspace_root = _workbench_workspace_root(project)
        git_status_before = _workbench_git_status_snapshot(workspace_root)
        workspace_files_before = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_before = _workbench_workspace_text_snapshot(workspace_root)
        memory_pair = _workbench_compose_memory_ephemeral(project, session)
        ephemeral_system = _workbench_compose_ephemeral_system(
            project, session, workspace_root=workspace_root, memory_pair=memory_pair
        )
        if finalizing:
            ephemeral_system = (ephemeral_system + "\n\n" + _workbench_finalize_directive(session)).strip()
        elif repairing_acceptance:
            ephemeral_system = (ephemeral_system + "\n\n" + _workbench_acceptance_repair_directive(session)).strip()
        elif kind == "answer":
            ephemeral_system = (ephemeral_system + "\n\n" + _WORKBENCH_TASK_REPLY_DIRECTIVE).strip()
        volatile_ephemeral_system = _workbench_compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_command = command or ("workbench-task-reply" if kind == "answer" else "")
        try:
            agent_reply = await _workbench_agent_reply(user_input, session, [], attachments=attachments, permission_mode=mode, command=agent_command, project_workspace=str(project.get("workspacePath") or ""), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=_workbench_compose_static_system(project, session), conversation_source="" if ui_instance_id else "webui", ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except _WorkbenchAgentRunError as exc:
            return agent_run_error_response(exc)
        git_status_after = _workbench_git_status_snapshot(workspace_root)
        workspace_files_after = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_after = _workbench_workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = _workbench_apply_pending(session, session_id, agent_reply)
        # Preserve any title/goal/summary the agent changed mid-run via set_task_goal.
        _workbench_sync_agent_task_meta(session, session_id, task_meta_before)
        session["agentReply"] = agent_reply
        if not command and not awaiting_user:
            schedule_capture(_workbench_project_memory_key(project), user_input, agent_reply)
        # On finalize: generate a task completion report for cross-session learning.
        if finalizing and not awaiting_user:
            _schedule_task_report(project, session)
        session["status"] = (
            "waiting_for_user" if awaiting_user
            else "review" if (finalizing or repairing_acceptance)
            else "acted" if kind == "direct"
            else "answered"
        )

        normalized_attachments = _workbench_normalize_attachments(attachments)
        public_attachments = [build_public_attachment_payload(item) for item in normalized_attachments]
        run_id = _short_id("run")
        activity_events = _collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [event for event in activity_events if event.get("type") == "ToolCallEvent"]
        file_changes = _workbench_collect_run_file_changes(
            tool_call_events,
            git_status_before,
            git_status_after,
            workspace_files_before,
            workspace_files_after,
            workspace_root,
            f"{user_input}\n{agent_reply}",
            workspace_text_before=workspace_text_before,
            workspace_text_after=workspace_text_after,
        )
        finished_at = _utc_now_iso()
        events = [
            {"id": _short_id("event"), "type": "UserMessageEvent", "runId": run_id, "createdAt": run_start_ts, "body": user_input or "[附件]", "attachments": public_attachments},
            *activity_events,
            {"id": _short_id("event"), "type": "AgentResponseEvent", "runId": run_id, "createdAt": finished_at, "body": agent_reply},
        ]
        run = {
            "id": run_id,
            "taskId": session_id,
            "userInput": user_input,
            "agentResponse": agent_reply,
            "status": "completed",
            "startedAt": run_start_ts,
            "endedAt": finished_at,
            "contextPackId": _short_id("ctx"),
            "events": events,
            "fileChanges": file_changes,
            "toolCalls": [{"tool": e["tool"], "argsPreview": e["argsPreview"]} for e in tool_call_events],
            "artifacts": [],
            "attachments": public_attachments,
            "mode": mode,
            "error": None,
        }
        session.setdefault("runs", []).append(run)
        session.setdefault("events", []).extend(events)
        _workbench_promote_file_artifacts(session, file_changes, finished_at, workspace_root)
        if not awaiting_user:
            await _workbench_archive_run_knowledge(
                project, session, run, workspace_root, finished_at,
            )
        session["updatedAt"] = finished_at
        project["updatedAt"] = finished_at
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        finalize_host_actions_after_reply(session_id, client_request_id)
        append_notification(
            title="Agent 回复完成",
            body=f"Agent 在「{session.get('title') or '任务'}」中" + (
                "整理并交付了任务成果，待你验收。" if finalizing
                else "参考验收结果继续修改了当前任务。" if repairing_acceptance
                else "执行了你的指令。" if kind == "direct"
                else "回复了你。"
            ),
            tab="comment",
            project_ref=project.get("id"),
            source="task_reply",
            source_label="任务",
            link_label=str(session.get("title") or ""),
            meta={"sessionId": session_id, "runId": run_id},
        )
        return {"ok": True, "replyKind": "repair" if repairing_acceptance else kind, "project": project, "session": session, "run": run, **payload}

    @router.post("/api/task-sessions/{session_id}/answer")
    async def api_workbench_answer(
        session_id: str, body_model: api_models.AnswerBody
    ):
        """Answer a paused run's permission / clarification question and resume
        the SAME round inside the project scope. The continued reply (or a follow-up
        question) replaces the question card. Mirrors the legacy chat answer flow,
        but session-scoped to this Workbench task."""
        body = api_models.body_dict(body_model)
        question_id = str(body.get("question_id") or "").strip()
        answer_text = str(body.get("answer") or body.get("selected_option") or "").strip()
        ui_instance_id = str(body.get("uiInstanceId") or "").strip()
        if not question_id or not answer_text:
            return JSONResponse({"error": "question_id and answer are required"}, status_code=400)
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        pending = session.get("pendingQuestion") if isinstance(session.get("pendingQuestion"), dict) else None
        if not pending or str(pending.get("id") or "") != question_id:
            return JSONResponse({"error": "no matching pending question"}, status_code=409)
        pending_plan_step = (
            dict(session.get("pendingPlanStep"))
            if isinstance(session.get("pendingPlanStep"), dict)
            else None
        )
        permission_kinds = {
            "scope_elevation",
            "write_permission_request",
            "read_elevation",
            "subshell_elevation",
            "delete_confirmation",
            "task_permission_request",
            "git_commit",
            "destructive_confirmation",
            "external_delivery_request",
            "external_upload_confirmation",
        }
        pending_options = pending.get("options") if isinstance(pending.get("options"), list) else []
        normalized_answer = answer_text.strip().casefold()
        explicit_denial = (
            normalized_answer == str(pending_options[-1]).strip().casefold()
            if pending_options
            else normalized_answer in {"拒绝", "不允许", "否", "reject", "deny", "no"}
        )
        permission_denied = (
            str(pending.get("kind") or "") in permission_kinds
            and explicit_denial
        )

        # Goal-loop clarifications (not permission denials) are resumed by the
        # background runner, so this request returns immediately instead of
        # blocking for as long as the agent keeps working. The agent-side pending
        # question stays in place; the runner clears it when it resumes. Denials
        # fall through to the synchronous path below (they only record the denial
        # and don't run the agent), as does any case the runner declines to own.
        if (
            pending_plan_step
            and bool(pending_plan_step.get("goalLoop"))
            and not permission_denied
        ):
            from cyrene.workbench.goal_loop import begin_async_answer
            if await begin_async_answer(_db_path, session_id, question_id, answer_text):
                payload = _read_workbench_store()
                project, session = _workbench_find_session(payload, session_id)
                return {
                    "ok": True,
                    "awaitingUser": False,
                    "continuePlanExecution": False,
                    "project": project,
                    "session": session,
                    "run": None,
                    **payload,
                }

        now = _utc_now_iso()
        run_start_ts = now
        workspace_root = _workbench_workspace_root(project)
        workspace_dir = _workbench_resolve_workspace_dir(project)
        git_status_before = _workbench_git_status_snapshot(workspace_root)
        workspace_files_before = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_before = _workbench_workspace_text_snapshot(workspace_root)
        from cyrene.runtime.host_bridge import resolve_conversation_source

        conversation_source = await resolve_conversation_source(ui_instance_id)
        try:
            agent_reply = await _workbench_answer_pending(
                session_id,
                question_id,
                answer_text,
                workspace_dir,
                ui_instance_id=ui_instance_id,
                conversation_source=conversation_source,
            )
        except asyncio.CancelledError:
            # The user's answer is consumed before the continuation starts.
            # Persist a genuine paused/cancelled terminal state rather than
            # restoring a question that no longer exists in agent state.
            cancelled_payload = _read_workbench_store()
            _cancelled_project, cancelled_session = _workbench_find_session(
                cancelled_payload, session_id
            )
            cancelled_run = None
            if cancelled_session:
                finished_at = _utc_now_iso()
                cancelled_session.pop("pendingQuestion", None)
                cancelled_session.pop("pendingPlanStep", None)
                cancelled_session["status"] = "paused"
                cancelled_session["agentReply"] = (
                    "回答已提交，但继续执行已被你中断。可稍后继续。"
                )
                for step in cancelled_session.get("plan") or []:
                    if not isinstance(step, dict) or step.get("status") != "running":
                        continue
                    step["status"] = "pending"
                    step["startedAt"] = None
                    step["currentAction"] = "已停止，可重新执行。"
                    step["updatedAt"] = finished_at
                run_id = _short_id("run")
                events = [
                    {
                        "id": _short_id("event"),
                        "type": "UserMessageEvent",
                        "runId": run_id,
                        "createdAt": run_start_ts,
                        "body": f"[确认] {answer_text}",
                    },
                    {
                        "id": _short_id("event"),
                        "type": "Paused",
                        "runId": run_id,
                        "createdAt": finished_at,
                        "body": "用户中断了回答后的继续执行。",
                    },
                ]
                cancelled_session.setdefault("events", []).extend(events)
                cancelled_run = {
                    "id": run_id,
                    "taskId": session_id,
                    "userInput": answer_text,
                    "agentResponse": "",
                    "status": "cancelled",
                    "terminationReason": "user_interrupted",
                    "startedAt": run_start_ts,
                    "endedAt": finished_at,
                    "contextPackId": _short_id("ctx"),
                    "events": events,
                    "toolCalls": [],
                    "fileChanges": [],
                    "artifacts": [],
                    "attachments": [],
                    "mode": "auto",
                    "error": None,
                }
                cancelled_session.setdefault("runs", []).append(cancelled_run)
                cancelled_session["updatedAt"] = finished_at
                _write_workbench_store(cancelled_payload)
                return {
                    "ok": True,
                    "interrupted": True,
                    "awaitingUser": False,
                    "continuePlanExecution": False,
                    "project": _cancelled_project,
                    "session": cancelled_session,
                    "run": cancelled_run,
                    **cancelled_payload,
                }
            raise
        except Exception:
            logger.exception("Workbench answer-resume failed for session %s", session_id)
            return JSONResponse({"error": "answer resume failed"}, status_code=502)
        git_status_after = _workbench_git_status_snapshot(workspace_root)
        workspace_files_after = _workbench_workspace_file_snapshot(workspace_root)
        workspace_text_after = _workbench_workspace_text_snapshot(workspace_root)

        # Another boundary may have been hit while resuming → re-surface the new
        # question; otherwise clear the card and settle on the continued reply.
        agent_reply, awaiting_user = _workbench_apply_pending(session, session_id, agent_reply)
        session["agentReply"] = agent_reply
        if not awaiting_user:
            session.pop("pendingQuestion", None)
            session["status"] = "acted"
            schedule_capture(_workbench_project_memory_key(project), answer_text, agent_reply)

        run_id = _short_id("run")
        activity_events = _collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [e for e in activity_events if e.get("type") == "ToolCallEvent"]
        file_changes = _workbench_collect_run_file_changes(
            tool_call_events,
            git_status_before,
            git_status_after,
            workspace_files_before,
            workspace_files_after,
            workspace_root,
            f"{answer_text}\n{agent_reply}",
            workspace_text_before=workspace_text_before,
            workspace_text_after=workspace_text_after,
        )
        finished_at = _utc_now_iso()
        events = [
            {"id": _short_id("event"), "type": "UserMessageEvent", "runId": run_id, "createdAt": now, "body": f"[确认] {answer_text}"},
            *activity_events,
            {"id": _short_id("event"), "type": "AgentResponseEvent", "runId": run_id, "createdAt": finished_at, "body": agent_reply},
        ]
        run = {
            "id": run_id,
            "taskId": session_id,
            "userInput": answer_text,
            "agentResponse": agent_reply,
            "status": "completed",
            "startedAt": run_start_ts,
            "endedAt": finished_at,
            "contextPackId": _short_id("ctx"),
            "events": events,
            "fileChanges": file_changes,
            "toolCalls": [{"tool": e["tool"], "argsPreview": e["argsPreview"]} for e in tool_call_events],
            "artifacts": [],
            "attachments": [],
            "mode": "auto",
            "error": None,
        }
        session.setdefault("runs", []).append(run)
        session.setdefault("events", []).extend(events)
        _workbench_promote_file_artifacts(session, file_changes, finished_at, workspace_root)
        continue_plan_execution = False
        if pending_plan_step and not awaiting_user:
            pending_step_id = str(pending_plan_step.get("stepId") or "").strip()
            is_goal_loop_step = bool(pending_plan_step.get("goalLoop"))
            target_step = next(
                (
                    step for step in (session.get("plan") or [])
                    if isinstance(step, dict) and str(step.get("id") or "") == pending_step_id
                ),
                None,
            )
            if target_step:
                session["planRevision"] = int(session.get("planRevision") or 0) + 1
                target_step["updatedAt"] = finished_at
                if permission_denied:
                    # The session pauses for user action, but the denied step
                    # remains pending so its command can be edited and retried.
                    target_step["status"] = "pending"
                    target_step["startedAt"] = None
                    target_step["currentAction"] = "权限请求被拒绝，可调整命令后重新执行。"
                    session["status"] = "paused"
                elif is_goal_loop_step:
                    # The answered slice already advanced this step: the agent
                    # resumed from its question and ran to the end of its turn,
                    # exactly like the normal answer branch below that marks the
                    # step complete. Marking it complete here — instead of
                    # resetting it to pending — stops the server-side runner from
                    # re-executing the SAME step and re-asking the SAME question.
                    # The independent FINAL acceptance stays the authoritative
                    # gate: if the step isn't really done it fails there and the
                    # runner generates repair steps.
                    target_step["status"] = "completed"
                    target_step["completedAt"] = finished_at
                    target_step["currentAction"] = (
                        f"用户已确认；本步完成，调用工具 {len(tool_call_events)} 次。"
                        if tool_call_events else "用户已确认，本步骤完成。"
                    )
                    target_step["toolCalls"] = [
                        {"tool": event["tool"], "argsPreview": event["argsPreview"]}
                        for event in tool_call_events
                    ]
                    _workbench_apply_step_file_changes(session, pending_step_id, file_changes)
                    # Hand progression back to the runner (next step / final
                    # acceptance); do not settle on review/paused here.
                    session["status"] = "running"
                else:
                    target_step["status"] = "completed"
                    target_step["completedAt"] = finished_at
                    target_step["currentAction"] = (
                        f"已完成，本步调用工具 {len(tool_call_events)} 次。"
                        if tool_call_events else "已完成该步骤。"
                    )
                    target_step["toolCalls"] = [
                        {"tool": event["tool"], "argsPreview": event["argsPreview"]}
                        for event in tool_call_events
                    ]
                    start_ms = target_step.get("startedAt")
                    if start_ms and target_step.get("durationSec") is None:
                        try:
                            seconds = round(
                                (
                                    datetime.fromisoformat(finished_at)
                                    - datetime.fromisoformat(str(start_ms))
                                ).total_seconds()
                            )
                            if seconds >= 1:
                                target_step["durationSec"] = seconds
                        except (TypeError, ValueError):
                            pass
                    _workbench_apply_step_file_changes(session, pending_step_id, file_changes)
                    remaining = [
                        step for step in (session.get("plan") or [])
                        if isinstance(step, dict)
                        and str(step.get("status") or "pending") not in ("completed", "done", "skipped")
                    ]
                    if not remaining:
                        session["status"] = "review"
                    elif bool(pending_plan_step.get("continueAll")):
                        session["status"] = "running"
                        continue_plan_execution = True
                    else:
                        session["status"] = "paused"
            session.pop("pendingPlanStep", None)
        if not awaiting_user:
            await _workbench_archive_run_knowledge(
                project, session, run, workspace_root, finished_at,
            )
        session["updatedAt"] = finished_at
        project["updatedAt"] = finished_at
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        finalize_host_actions_after_reply(session_id)
        if pending_plan_step and bool(pending_plan_step.get("goalLoop")) and not awaiting_user:
            from cyrene.workbench.goal_loop import resume_after_answer
            await resume_after_answer(
                _db_path,
                session_id,
                permission_denied=permission_denied,
            )
        return {
            "ok": True,
            "awaitingUser": awaiting_user,
            "continuePlanExecution": continue_plan_execution,
            "project": project,
            "session": session,
            "run": run,
            **payload,
        }

    @router.post("/api/task-sessions/{session_id}/init/submit")
    async def api_workbench_submit_init(
        session_id: str, body_model: api_models.InitSubmitBody
    ):
        """Finalize project initialization.

        Persists the onboarding answers, writes a project brief into the project
        context, and asks the initialization agent to draft the major task plan.
        Confirming that plan is a separate step that creates task sessions.
        """
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if str(session.get("kind") or "") != "init":
            return JSONResponse({"error": "not an init session"}, status_code=400)
        init_state = session.get("init") if isinstance(session.get("init"), dict) else {}
        if bool(init_state.get("completed")):
            return JSONResponse({"error": "init already completed"}, status_code=409)
        form = session.get("init") if isinstance(session.get("init"), dict) else _workbench_default_init_form(project)
        if isinstance(body.get("answers"), dict):
            merged = form.get("answers") if isinstance(form.get("answers"), dict) else {}
            merged.update(body["answers"])
            form["answers"] = merged

        brief = _workbench_init_brief(project, form)
        answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
        goal = str(answers.get("goal") or "").strip()
        now = _utc_now_iso()
        # Fold the onboarding into the project's durable context.
        context = project.get("context") if isinstance(project.get("context"), dict) else {}
        if brief:
            context["summary"] = brief
        project["context"] = context
        if not str(project.get("description") or "").strip() and goal:
            project["description"] = goal[:200]
        task_plan, plan_from_llm, plan_error = await _workbench_generate_init_task_plan(project, form)
        form["completed"] = False
        form.pop("planError", None)
        if plan_from_llm and task_plan:
            form["taskPlan"] = task_plan
            form["planReady"] = True
            form["planSource"] = "llm"
        else:
            form["taskPlan"] = []
            form["planReady"] = False
            form["planSource"] = "error"
            form["planError"] = {
                **(plan_error or {}),
                "occurredAt": now,
            }
        session["init"] = form
        session["status"] = "waiting_for_user"
        if plan_from_llm:
            session["agentReply"] = "我已根据你的初始化回答拆解出大任务计划。你可以直接编辑，或继续告诉我如何调整；确认后我会把每个大任务创建为独立 session。"
        else:
            summary = str((plan_error or {}).get("summary") or "未知错误")
            attempt_count = int((plan_error or {}).get("attemptCount") or 5)
            session["agentReply"] = f"计划生成连续重试 {attempt_count} 次后仍然失败：{summary}"
        session["summary"] = brief or session.get("summary")
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/init/plan")
    async def api_workbench_revise_init_plan(
        session_id: str, body_model: api_models.InitPlanBody
    ):
        """Revise the initialization task plan from user feedback."""
        body = api_models.body_dict(body_model)
        feedback = str(body.get("feedback") or body.get("message") or "").strip()
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if str(session.get("kind") or "") != "init":
            return JSONResponse({"error": "not an init session"}, status_code=400)
        form = session.get("init") if isinstance(session.get("init"), dict) else _workbench_default_init_form(project)
        if bool(form.get("completed")):
            return JSONResponse({"error": "init already completed"}, status_code=409)
        # The client sends the plan currently on screen (incl. manual edits) so
        # the agent adjusts THAT plan; fall back to the persisted one.
        incoming_plan = body.get("taskPlan") if isinstance(body.get("taskPlan"), list) else None
        current_plan = _workbench_coerce_init_task_plan(incoming_plan, []) if incoming_plan else None
        if not current_plan:
            existing = form.get("taskPlan")
            current_plan = existing if isinstance(existing, list) and existing else None
        task_plan, plan_from_llm, plan_error = await _workbench_generate_init_task_plan(
            project, form, feedback=feedback, current_plan=current_plan,
        )
        form.pop("planError", None)
        if plan_from_llm and task_plan:
            form["taskPlan"] = task_plan
            form["planSource"] = "llm"
            session["agentReply"] = "我已按你的反馈更新任务计划。你可以继续修改，或确认创建 sessions。"
        else:
            if current_plan:
                form["taskPlan"] = current_plan
            form["planSource"] = "error"
            form["planError"] = {
                **(plan_error or {}),
                "occurredAt": _utc_now_iso(),
            }
            summary = str((plan_error or {}).get("summary") or "未知错误")
            attempt_count = int((plan_error or {}).get("attemptCount") or 5)
            session["agentReply"] = f"计划调整连续重试 {attempt_count} 次后仍然失败，当前计划未改变：{summary}"
        form["planReady"] = bool(isinstance(form.get("taskPlan"), list) and form.get("taskPlan"))
        session["init"] = form
        session["status"] = "waiting_for_user"
        now = _utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/init/confirm")
    async def api_workbench_confirm_init_plan(
        session_id: str, body_model: api_models.InitConfirmBody
    ):
        """Create task sessions from the confirmed initialization plan."""
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if str(session.get("kind") or "") != "init":
            return JSONResponse({"error": "not an init session"}, status_code=400)
        form = session.get("init") if isinstance(session.get("init"), dict) else _workbench_default_init_form(project)
        if bool(form.get("completed")):
            existing_ids = form.get("createdSessionIds") if isinstance(form.get("createdSessionIds"), list) else []
            existing = [
                s for s in project.get("sessions", [])
                if str(s.get("id") or "") in {str(item) for item in existing_ids}
            ]
            return {"ok": True, "project": project, "session": existing[0] if existing else session, "initSession": session, "createdSessions": existing, **payload}
        incoming_plan = body.get("taskPlan") if isinstance(body.get("taskPlan"), list) else form.get("taskPlan")
        fallback = _workbench_fallback_init_task_plan(project, form)
        task_plan = _workbench_coerce_init_task_plan(incoming_plan, fallback)
        if not task_plan:
            return JSONResponse({"error": "task plan is empty"}, status_code=400)
        now = _utc_now_iso()
        created = _workbench_create_sessions_from_init_plan(project, task_plan, now)
        if not created:
            return JSONResponse({"error": "no sessions created"}, status_code=400)
        form["taskPlan"] = task_plan
        form["planReady"] = True
        form["completed"] = True
        form["createdSessionIds"] = [item["id"] for item in created]
        session["init"] = form
        session["status"] = "completed"
        session["agentReply"] = f"初始化已完成。我已根据确认后的计划创建 {len(created)} 个任务 session。"
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeProjectId"] = project.get("id")
        payload["activeSessionId"] = created[0]["id"]
        _write_workbench_store(payload)
        append_notification(
            title="初始化任务已生成",
            body=f"{project.get('name') or 'Workspace'} 已创建 {len(created)} 个任务 session。",
            tab="system",
            project_ref=project.get("id"),
            source="init_confirmed",
            source_label="系统",
            link_label=str(project.get("name") or ""),
            meta={"createdSessionIds": [item["id"] for item in created]},
        )
        return {"ok": True, "project": project, "session": created[0], "initSession": session, "createdSessions": created, **payload}

    @router.get("/api/task-sessions/{session_id}/events")
    async def api_workbench_session_events(session_id: str):
        payload = _read_workbench_store()
        _project, session = _workbench_find_session(payload, session_id)
        if not session:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return {"events": session.get("events", [])}

    @router.get("/api/task-sessions/{session_id}/artifacts")
    async def api_workbench_session_artifacts(session_id: str):
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if project:
            await migrate_legacy_artifacts_if_needed(payload, project, session)
        return {"artifacts": session.get("artifacts", [])}

    @router.get("/api/task-sessions/{session_id}/artifacts/{artifact_id}/download")
    async def api_workbench_download_artifact(session_id: str, artifact_id: str):
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, session_id)
        if not session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)
        try:
            artifact, target = _workbench_artifact_download_target(project, session, artifact_id)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        filename = Path(str(artifact.get("name") or target.name)).name or target.name
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(target, filename=filename, media_type=media_type)

    return {
        "get_task": api_workbench_get_session,
        "update_task": api_workbench_update_session,
        "dispatch_task": api_workbench_dispatch,
        "create_run": api_workbench_create_run,
        "answer_task": api_workbench_answer,
        "task_events": api_workbench_session_events,
        "task_artifacts": api_workbench_session_artifacts,
    }

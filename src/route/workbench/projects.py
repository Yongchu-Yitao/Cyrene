"""Workbench project routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *
from route import schemas as api_models
from route.errors import error_response
from route.workspace import WorkspacePathError, validate_workspace_path


def register_project_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Workbench projects / task sessions ----

    @router.get("/api/projects")
    async def api_workbench_projects(detail: str = "full"):
        if str(detail or "").strip().lower() in {"summary", "light", "list"}:
            payload = await asyncio.to_thread(_read_workbench_store_lightweight)
            return _workbench_lightweight_store(payload)
        payload = _read_workbench_store()
        return payload

    @router.get("/api/workbench/notifications")
    async def api_workbench_notifications(
        tab: str = "all",
        limit: int = 80,
        visible_chat_id: str = "",
        visible_session_id: str = "",
    ):
        return list_notifications(
            tab=tab,
            limit=limit,
            visible_chat_id=visible_chat_id,
            visible_session_id=visible_session_id,
        )

    @router.post("/api/workbench/notifications/read")
    async def api_workbench_notifications_read(body: api_models.NotificationsReadBody):
        ids = body.ids
        mark_all = body.markAll
        result = mark_notifications_read(ids, mark_all=mark_all)
        return {**result, **list_notifications(limit=80)}

    @router.patch("/api/workbench/activate")
    async def api_workbench_activate(body: api_models.WorkbenchActivateBody):
        selection = await asyncio.to_thread(
            _persist_workbench_selection,
            body.projectId,
            body.sessionId,
        )
        return {"ok": True, **selection}

    @router.post("/api/projects")
    async def api_workbench_create_project(body_model: api_models.ProjectCreateBody):
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        now = _utc_now_iso()
        project_id = _short_id("project")
        raw_workspace = str(body.get("workspacePath") or "").strip()
        workspace_path_source = "user" if raw_workspace else "generated"
        if not raw_workspace:
            # No explicit workspace folder picked — create a fresh per-project
            # subdirectory under the global WORKSPACE_DIR so the new project
            # starts empty instead of inheriting the (non-empty) default
            # workspace. This ensures _is_workspace_empty() returns True and
            # the init flow takes the "brand-new project" branch rather than
            # exploring the default workspace's contents.
            raw_workspace = str(Path(WORKSPACE_DIR) / "projects" / project_id)
        try:
            workspace_path = str(
                validate_workspace_path(
                    raw_workspace,
                    create=True,
                )
            )
        except WorkspacePathError as exc:
            return error_response(str(exc), 400, exc.code)
        name = str(body.get("name") or Path(workspace_path).name or "New Project").strip()
        description = str(body.get("description") or "").strip()
        project = {
            "id": project_id,
            "name": name,
            "dataKey": _safe_workbench_data_key(project_id),
            "description": description,
            "icon": str(body.get("icon") or "spark").strip() or "spark",
            "color": str(body.get("color") or "").strip(),
            "template": str(body.get("template") or "blank").strip() or "blank",
            "workspacePath": workspace_path,
            "workspacePathSource": workspace_path_source,
            "status": "active",
            "model": _get_model(),
            "accountTier": str(body.get("accountTier") or "Pro"),
            "context": {
                "summary": str(body.get("summary") or description or f"Workspace at {workspace_path}"),
                "stack": body.get("stack") if isinstance(body.get("stack"), list) else [],
                "decisions": [],
                "knowledgeDocumentIds": [],
            },
            "createdAt": now,
            "updatedAt": now,
            "sessions": [],
            "sharedArtifacts": [],
        }
        # New projects open onto an agent-led "初始化项目" onboarding session.
        initial_session = _workbench_new_init_session(project_id, project, now)
        project["sessions"] = [initial_session]
        payload.setdefault("projects", []).insert(0, project)
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = initial_session["id"]
        _write_workbench_store(payload)
        append_notification(
            title="项目创建完成",
            body=f"已创建 workspace「{name}」。",
            tab="system",
            project_ref=project_id,
            source="project_created",
            source_label="Workspace",
            link_label=name,
        )
        return {"ok": True, "project": project, "session": initial_session, **payload}

    @router.patch("/api/projects/{project_id}")
    async def api_workbench_update_project(
        project_id: str, body_model: api_models.ProjectUpdateBody
    ):
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project = _workbench_find_project(payload, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        if "workspacePath" in body:
            try:
                body["workspacePath"] = str(
                    validate_workspace_path(
                        str(body.get("workspacePath") or ""),
                        create=True,
                    )
                )
            except WorkspacePathError as exc:
                return error_response(str(exc), 400, exc.code)
            project["workspacePathSource"] = "user"
        for field in ("name", "description", "icon", "color", "template", "workspacePath", "status", "model", "accountTier"):
            if field in body:
                project[field] = body[field]
        if isinstance(body.get("context"), dict):
            project["context"] = {**(project.get("context") or {}), **body["context"]}
        project["updatedAt"] = _utc_now_iso()
        _write_workbench_store(payload)
        return {"ok": True, "project": project, **payload}

    @router.delete("/api/projects/{project_id}")
    async def api_workbench_delete_project(project_id: str):
        payload = _read_workbench_store()
        projects = payload.get("projects", [])
        # Collect session IDs before filtering so we can clean up agent state
        doomed_project = next((p for p in projects if str(p.get("id") or "") == project_id), None)
        if doomed_project and _workbench_project_data_key(doomed_project) == _WORKBENCH_LEGACY_DATA_KEY:
            return error_response(
                "The default project cannot be deleted",
                400,
                "default_project_protected",
            )
        if doomed_project:
            doomed_data_key = _workbench_project_data_key(doomed_project)
            doomed_memory_key = _workbench_project_memory_key(doomed_project)
            for s in (doomed_project.get("sessions") or []):
                sid = str(s.get("id") or "").strip()
                if sid:
                    try:
                        await clear_session_id(session_id=sid)
                    except Exception:
                        logger.exception("Failed to clear session state for %s", sid)
            # Also drop the project's workbench conversations (chat-kind sessions).
            try:
                from route.workbench.chat import remove_project_chats
                await remove_project_chats(project_id)
            except Exception:
                logger.exception("Failed to remove chats for project %s", project_id)
            if doomed_data_key != _WORKBENCH_LEGACY_DATA_KEY:
                try:
                    from cyrene.config import get_knowledge_db_path
                    from route.workbench.memory import delete_workspace_memory

                    # Knowledge db is keyed on the project id (memory key).
                    _remove_path(get_knowledge_db_path(doomed_memory_key))
                    delete_workspace_memory(doomed_memory_key)
                    import aiosqlite
                    async with aiosqlite.connect(_db_path) as db:
                        await db.execute(
                            "DELETE FROM scheduled_tasks WHERE COALESCE(project_id, 'default') = ?",
                            (doomed_data_key,),
                        )
                        await db.commit()
                except Exception:
                    logger.exception("Failed to remove project-scoped data for %s", project_id)
        base_payload = getattr(payload, "_workbench_base", None)
        next_projects = [project for project in projects if str(project.get("id") or "") != project_id]
        if len(next_projects) == len(projects):
            return JSONResponse({"error": "project not found"}, status_code=404)
        payload["projects"] = next_projects
        if not next_projects:
            payload = _workbench_default_project()
        else:
            payload["activeProjectId"] = next_projects[0].get("id")
            sessions = next_projects[0].get("sessions") or []
            payload["activeSessionId"] = sessions[0].get("id") if sessions else ""
        _write_workbench_store(payload, base_value=base_payload)
        return {"ok": True, **payload}

    @router.get("/api/projects/{project_id}/sessions")
    async def api_workbench_project_sessions(project_id: str):
        payload = _read_workbench_store()
        project = _workbench_find_project(payload, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        return {"sessions": project.get("sessions", [])}

    @router.post("/api/projects/{project_id}/sessions")
    async def api_workbench_create_session(
        project_id: str, body_model: api_models.SessionCreateBody
    ):
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project = _workbench_find_project(payload, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        title = str(body.get("title") or body.get("goal") or "新任务").strip() or "新任务"
        session = _workbench_new_session(project_id, title, str(body.get("goal") or "").strip())
        if str(body.get("priority") or "").strip() in ("high", "medium", "low"):
            session["priority"] = str(body.get("priority")).strip()
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = session["id"]
        _write_workbench_store(payload)
        append_notification(
            title="新任务已创建",
            body=f"任务「{title}」已加入 {project.get('name') or 'workspace'}。",
            tab="comment",
            project_ref=project_id,
            source="task_created",
            source_label="任务",
            link_label=title,
            meta={"sessionId": session["id"]},
        )
        return {"ok": True, "session": session, **payload}

    @router.post("/api/task-sessions/{session_id}/follow-up")
    async def api_workbench_create_follow_up(
        session_id: str, body_model: api_models.FollowUpBody
    ):
        body = api_models.body_dict(body_model)
        payload = _read_workbench_store()
        project, source_session = _workbench_find_session(payload, session_id)
        if not source_session or not project:
            return JSONResponse({"error": "session not found"}, status_code=404)

        seed = _workbench_follow_up_seed(
            source_session,
            requested_title=str(body.get("title") or "").strip(),
            requested_goal=str(body.get("goal") or "").strip(),
        )
        project_id = str(project.get("id") or "")
        session = _workbench_new_session(project_id, seed["title"], seed["goal"])
        session["parentSessionId"] = session_id
        session["priority"] = seed["priority"]
        session["constraints"] = seed["constraints"]
        session["followUpContext"] = seed["context"]
        session["agentReply"] = "已根据来源任务的当前进度创建后续任务。你可以直接交给 Agent，或继续补充要求。"
        session["events"] = [{
            "id": _short_id("event"),
            "type": "CreatedAsFollowUp",
            "createdAt": session["createdAt"],
            "body": f"基于任务「{source_session.get('title') or '任务'}」的当前情况创建。",
            "sourceSessionId": session_id,
        }]
        for text in seed["unresolvedAcceptance"]:
            session["acceptanceCriteria"].append({
                "id": _short_id("accept"),
                "text": text,
                "status": "pending",
            })

        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = session["id"]
        _write_workbench_store(payload)
        append_notification(
            title="后续任务已创建",
            body=f"已根据「{source_session.get('title') or '任务'}」的当前情况创建「{session['title']}」。",
            tab="comment",
            project_ref=project_id,
            source="follow_up_created",
            source_label="任务",
            link_label=session["title"],
            meta={"sessionId": session["id"], "sourceSessionId": session_id},
        )
        return {
            "ok": True,
            "session": session,
            "sourceSessionId": session_id,
            **payload,
        }

    @router.post("/api/projects/{project_id}/init/generate")
    async def api_workbench_generate_init(
        project_id: str, body_model: api_models.InitGenerateBody
    ):
        """(Re)generate the onboarding questions for a project's init session.

        Runs the agent against the project's metadata and workspace files; on
        any failure it keeps the deterministic fallback form. Either way the
        form is marked as ``generated`` so the client only requests this once.
        """
        lang = str(body_model.lang or "").strip()
        payload = _read_workbench_store()
        project = _workbench_find_project(payload, project_id)
        if not project:
            return JSONResponse({"error": "project not found"}, status_code=404)
        session = next(
            (s for s in project.get("sessions", []) if str(s.get("kind") or "") == "init"),
            None,
        )
        if not session:
            return JSONResponse({"error": "init session not found"}, status_code=404)
        current = session.get("init") if isinstance(session.get("init"), dict) else _workbench_default_init_form(project)
        generated = await _workbench_generate_init_form(project, lang=lang)
        if generated:
            # Preserve any answers the user already entered.
            generated["answers"] = current.get("answers") if isinstance(current.get("answers"), dict) else {}
            generated["completed"] = bool(current.get("completed"))
            session["init"] = generated
            session["agentReply"] = generated.get("greeting") or session.get("agentReply") or ""
        else:
            # Generation failed (LLM error / unparseable output). Keep the
            # deterministic fallback but DON'T mark it generated — the client
            # guards re-entry per mount (genRef), so leaving generated=False lets
            # it self-heal on the next open instead of permanently sticking the
            # generic form. The user can also press 重新生成问题 to retry now.
            current["generated"] = False
            session["init"] = current
        now = _utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session.get("id")
        _write_workbench_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

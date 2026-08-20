"""Workbench project routes."""

# ruff: noqa: F403,F405

import hashlib
import os
import tempfile

from cyrene.workbench.runtime import *
from route import schemas as api_models
from route.errors import error_response
from route.workspace import WorkspacePathError, validate_workspace_path


def register_project_routes(
    router: APIRouter,
    bot: Any,
    db_path: str,
) -> dict[str, Any]:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    editable_text_extensions = {
        ".bash", ".c", ".cc", ".cfg", ".conf", ".cpp", ".css", ".csv",
        ".env", ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java",
        ".js", ".json", ".jsx", ".kt", ".log", ".md", ".mdx", ".php",
        ".properties", ".py", ".rb", ".rs", ".rst", ".scss", ".sh",
        ".sql", ".svelte", ".swift", ".toml", ".ts", ".tsx", ".txt",
        ".vue", ".xml", ".yaml", ".yml",
    }
    editable_text_names = {
        ".editorconfig", ".env", ".gitattributes", ".gitignore", ".npmrc",
        "dockerfile", "license", "makefile", "readme",
    }
    max_editable_text_bytes = 4 * 1024 * 1024

    def resolve_project_file(project_id: str, file_path: str):
        project = _workbench_find_project_lightweight(project_id)
        if project is None:
            return None, error_response("Project not found", 404, "project_not_found")
        raw_root = _workbench_resolve_workspace_dir(project)
        if not raw_root:
            return None, error_response("Project has no workspace", 404, "workspace_unavailable")
        root = Path(raw_root).expanduser().resolve()
        requested = str(file_path or "").replace("\\", "/").strip()
        if not requested:
            return None, error_response("File path is required", 400, "invalid_workspace_path")

        cursor = root
        for part in Path(requested).parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                return None, error_response(
                    "Symbolic links cannot be accessed", 403, "symlink_not_allowed"
                )
        try:
            target = (root / requested).resolve(strict=False)
        except (OSError, RuntimeError):
            return None, error_response("Invalid project file path", 400, "invalid_workspace_path")
        if target != root and root not in target.parents:
            return None, error_response(
                "Path escapes the project workspace", 400, "invalid_workspace_path"
            )
        if not target.is_file():
            return None, error_response("File not found", 404, "file_not_found")
        return target, None

    def editable_text_payload(target: Path):
        stat = target.stat()
        if stat.st_size > max_editable_text_bytes:
            return None, error_response(
                "Text file is too large to edit", 413, "text_file_too_large",
                maxBytes=max_editable_text_bytes,
            )
        media_type = mimetypes.guess_type(target.name)[0] or ""
        extension = target.suffix.lower()
        normalized_name = target.name.lower()
        if not (
            media_type.startswith("text/")
            or extension in editable_text_extensions
            or normalized_name in editable_text_names
        ):
            return None, error_response(
                "This file type is not editable as text", 415, "text_file_type_unsupported"
            )
        try:
            raw = target.read_bytes()
            has_bom = raw.startswith(b"\xef\xbb\xbf")
            content = raw.decode("utf-8-sig" if has_bom else "utf-8")
        except UnicodeDecodeError:
            return None, error_response(
                "Only UTF-8 text files can be edited", 415, "text_file_encoding_unsupported"
            )
        return {
            "content": content,
            "version": hashlib.sha256(raw).hexdigest(),
            "modifiedNs": int(stat.st_mtime_ns),
            "size": len(raw),
            "bom": has_bom,
            "contentType": media_type or "text/plain",
        }, None

    # ---- Workbench projects / task sessions ----

    @router.get("/api/projects")
    async def api_workbench_projects(detail: str = "full"):
        if str(detail or "").strip().lower() in {"summary", "light", "list"}:
            payload = await asyncio.to_thread(_read_workbench_store_lightweight)
            return _workbench_lightweight_store(payload)
        payload = _read_workbench_store()
        return payload

    @router.get("/api/projects/{project_id}/files")
    async def api_workbench_project_files(
        project_id: str,
        path: str = ".",
        query: str = "",
    ):
        project = _workbench_find_project_lightweight(project_id)
        if project is None:
            return error_response("Project not found", 404, "project_not_found")
        raw_root = await _workbench_resolve_workspace_dir_async(project)
        if not raw_root:
            return error_response("Project has no workspace", 404, "workspace_unavailable")
        root = Path(raw_root).expanduser().resolve()
        requested = str(path or ".").replace("\\", "/").strip() or "."
        candidate = (root / requested).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            return error_response("Path escapes the project workspace", 400, "invalid_workspace_path")
        if not candidate.is_dir():
            return error_response("Directory not found", 404, "directory_not_found")

        normalized_query = str(query or "").strip().casefold()

        def entry_payload(item: Path) -> dict[str, Any]:
            info = item.stat()
            return {
                "name": item.name,
                "path": item.relative_to(root).as_posix(),
                "kind": "directory" if item.is_dir() else "file",
                "size": int(info.st_size) if item.is_file() else 0,
                "modifiedNs": int(info.st_mtime_ns),
            }

        def list_entries() -> list[dict[str, Any]]:
            entries: list[dict[str, Any]] = []
            for item in sorted(candidate.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
                if item.is_symlink() or item.name in {".git", "node_modules", "__pycache__"}:
                    continue
                entries.append(entry_payload(item))
                if len(entries) >= 500:
                    break
            return entries

        def search_entries() -> list[dict[str, Any]]:
            entries: list[dict[str, Any]] = []
            ignored = {".git", "node_modules", "__pycache__"}
            for directory, names, filenames in os.walk(root, followlinks=False):
                names[:] = sorted(
                    name for name in names
                    if name not in ignored and not (Path(directory) / name).is_symlink()
                )
                items = [Path(directory) / name for name in names]
                items.extend(Path(directory) / name for name in sorted(filenames))
                for item in items:
                    if item.is_symlink():
                        continue
                    relative = item.relative_to(root).as_posix()
                    if normalized_query not in item.name.casefold() and normalized_query not in relative.casefold():
                        continue
                    entries.append(entry_payload(item))
                    if len(entries) >= 500:
                        return entries
            return entries

        return {
            "ok": True,
            "path": "." if normalized_query or candidate == root else candidate.relative_to(root).as_posix(),
            "query": str(query or "").strip(),
            "entries": await asyncio.to_thread(search_entries if normalized_query else list_entries),
        }

    @router.get("/api/projects/{project_id}/files/content/{file_path:path}")
    async def api_workbench_project_file_content(project_id: str, file_path: str):
        """Stream a regular project file for the Workbench split viewer."""
        project = _workbench_find_project_lightweight(project_id)
        if project is None:
            return error_response("Project not found", 404, "project_not_found")
        raw_root = await _workbench_resolve_workspace_dir_async(project)
        if not raw_root:
            return error_response("Project has no workspace", 404, "workspace_unavailable")
        root = Path(raw_root).expanduser().resolve()
        requested = str(file_path or "").replace("\\", "/").strip()
        if not requested:
            return error_response("File path is required", 400, "invalid_workspace_path")

        unresolved = root / requested
        cursor = root
        for part in Path(requested).parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                return error_response("Symbolic links cannot be previewed", 403, "symlink_not_allowed")
        try:
            target = unresolved.resolve(strict=False)
        except (OSError, RuntimeError):
            return error_response("Invalid project file path", 400, "invalid_workspace_path")
        if target != root and root not in target.parents:
            return error_response("Path escapes the project workspace", 400, "invalid_workspace_path")
        if not target.is_file():
            return error_response("File not found", 404, "file_not_found")

        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            target,
            filename=target.name,
            media_type=media_type,
            content_disposition_type="inline",
        )

    @router.get("/api/projects/{project_id}/files/edit/{file_path:path}")
    async def api_workbench_project_text_file(project_id: str, file_path: str):
        """Read an editable UTF-8 project file with an optimistic-lock version."""
        target, failure = resolve_project_file(project_id, file_path)
        if failure is not None:
            return failure
        payload, failure = await asyncio.to_thread(editable_text_payload, target)
        if failure is not None:
            return failure
        return {"ok": True, "path": str(file_path).replace("\\", "/"), **payload}

    @router.put("/api/projects/{project_id}/files/edit/{file_path:path}")
    async def api_workbench_update_project_text_file(
        project_id: str,
        file_path: str,
        body: api_models.ProjectTextFileUpdateBody,
    ):
        """Atomically save an existing UTF-8 project file with conflict detection."""
        target, failure = resolve_project_file(project_id, file_path)
        if failure is not None:
            return failure
        current, failure = await asyncio.to_thread(editable_text_payload, target)
        if failure is not None:
            return failure
        expected_version = str(body.expectedVersion or "").strip()
        if expected_version and expected_version != current["version"] and not body.force:
            return error_response(
                "The file changed after it was opened", 409, "text_file_conflict",
                version=current["version"], modifiedNs=current["modifiedNs"],
            )

        encoded = body.content.encode("utf-8")
        if current["bom"]:
            encoded = b"\xef\xbb\xbf" + encoded
        if len(encoded) > max_editable_text_bytes:
            return error_response(
                "Text file is too large to save", 413, "text_file_too_large",
                maxBytes=max_editable_text_bytes,
            )

        def atomic_write() -> dict[str, Any]:
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=".cyrene-edit-", dir=target.parent, delete=False
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected_version and not body.force:
                    latest = target.read_bytes()
                    latest_version = hashlib.sha256(latest).hexdigest()
                    if latest_version != expected_version:
                        latest_stat = target.stat()
                        return {
                            "conflict": True,
                            "version": latest_version,
                            "modifiedNs": int(latest_stat.st_mtime_ns),
                        }
                os.chmod(temp_path, target.stat().st_mode)
                os.replace(temp_path, target)
                temp_path = None
                stat = target.stat()
                return {
                    "ok": True,
                    "path": str(file_path).replace("\\", "/"),
                    "version": hashlib.sha256(encoded).hexdigest(),
                    "modifiedNs": int(stat.st_mtime_ns),
                    "size": len(encoded),
                }
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

        try:
            result = await asyncio.to_thread(atomic_write)
            if result.get("conflict"):
                return error_response(
                    "The file changed while it was being saved", 409, "text_file_conflict",
                    version=result["version"], modifiedNs=result["modifiedNs"],
                )
            return result
        except OSError:
            return error_response("The project file could not be saved", 403, "text_file_not_writable")

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
            # subdirectory under the global WORKSPACE_DIR/.cyrene so the new
            # project starts empty instead of inheriting the (non-empty)
            # default workspace. This ensures _is_workspace_empty() returns
            # True and the init flow takes the "brand-new project" branch
            # rather than exploring the default workspace's contents.
            raw_workspace = str(cyrene_dir(WORKSPACE_DIR) / "projects" / project_id)
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
            from cyrene.workbench.chat import _read_chats_store
            doomed_chat_ids = [
                str(item.get("id") or "")
                for item in (_read_chats_store().get("chats") or [])
                if isinstance(item, dict)
                and str(item.get("projectId") or "") == project_id
                and str(item.get("id") or "")
            ]
            for s in (doomed_project.get("sessions") or []):
                sid = str(s.get("id") or "").strip()
                if sid:
                    try:
                        interrupt_active_run(session_id=sid)
                        await clear_session_id(session_id=sid, deleting=True)
                    except Exception:
                        logger.exception("Failed to clear session state for %s", sid)
                        return error_response(
                            "Project agents could not be terminated",
                            503,
                            "project_agents_not_terminated",
                        )
            # Also drop the project's workbench conversations (chat-kind sessions).
            try:
                from route.workbench.chat import remove_project_chats
                await remove_project_chats(project_id)
            except Exception:
                logger.exception("Failed to remove chats for project %s", project_id)
                return error_response(
                    "Project chat agents could not be terminated",
                    503,
                    "project_chat_agents_not_terminated",
                )
            if doomed_data_key != _WORKBENCH_LEGACY_DATA_KEY:
                try:
                    from cyrene.config import get_knowledge_db_path
                    from route.workbench.memory import delete_workspace_memory
                    from cyrene.workbench.project_memory_prompt import (
                        cancel_project_jobs,
                        delete_project_memory,
                    )

                    # Knowledge db is keyed on the project id (memory key).
                    _remove_path(get_knowledge_db_path(doomed_memory_key))
                    delete_workspace_memory(doomed_memory_key)
                    await cancel_project_jobs(project_id)
                    delete_project_memory(project_id, doomed_chat_ids)
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

    return {
        "list_tasks": api_workbench_project_sessions,
        "create_task": api_workbench_create_session,
    }

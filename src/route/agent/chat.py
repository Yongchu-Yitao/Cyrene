"""Legacy agent chat routes."""

# ruff: noqa: F403,F405

from cyrene.workbench.runtime import *


def register_chat_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Chat API ----

    @router.post("/api/chat/upload")
    async def api_chat_upload(background_tasks: BackgroundTasks, files: list[UploadFile]):
        if not files:
            return JSONResponse({"error": "no files uploaded"}, status_code=400)

        _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        uploaded: list[dict[str, Any]] = []

        for file in files:
            safe_name = _safe_upload_name(file.filename or "")
            target = _UPLOADS_DIR / f"{uuid.uuid4().hex}_{safe_name}"
            file_size = 0
            try:
                with target.open("wb") as f:
                    while chunk := await file.read(65536):
                        f.write(chunk)
                        file_size += len(chunk)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            content_type = str(file.content_type or mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            kind = attachment_kind_from_meta(content_type, target.name)
            width, height = _image_dimensions(target) if kind == "image" else (None, None)
            uploaded.append({
                "id": target.name,
                "name": file.filename or safe_name,
                "path": str(target.resolve()),
                "content_type": content_type,
                "size": file_size,
                "kind": kind,
                "url": f"/api/chat/upload/{target.name}",
                **({"width": width} if isinstance(width, int) else {}),
                **({"height": height} if isinstance(height, int) else {}),
            })

            # The response now owns this exact path. Run KB registration only
            # after the upload response is ready, and never let content-hash
            # deduplication delete a file referenced by the current session.
            background_tasks.add_task(
                _deduplicate_chat_upload_after_response,
                target,
                display_name=file.filename or safe_name,
                content_type=content_type,
                kind=kind,
                size=file_size,
            )

        return {"files": uploaded}

    @router.get("/api/chat/upload/{upload_id}")
    async def api_chat_upload_file(upload_id: str):
        safe_upload_id = _safe_upload_name(upload_id)
        target = (_UPLOADS_DIR / safe_upload_id).resolve()
        uploads_root = _UPLOADS_DIR.resolve()
        if target != uploads_root and uploads_root not in target.parents:
            return JSONResponse({"error": "invalid upload path"}, status_code=400)
        if not target.exists() or not target.is_file():
            return JSONResponse({"error": "upload not found"}, status_code=404)
        return FileResponse(target)

    @router.get("/api/chat/export/{export_id}")
    async def api_chat_export_file(export_id: str):
        safe_export_id = _safe_upload_name(export_id)
        target = (_EXPORTS_DIR / safe_export_id).resolve()
        exports_root = _EXPORTS_DIR.resolve()
        if target != exports_root and exports_root not in target.parents:
            return JSONResponse({"error": "invalid export path"}, status_code=400)
        if not target.exists() or not target.is_file():
            return JSONResponse({"error": "export not found"}, status_code=404)
        return FileResponse(target)

    @router.post("/api/chat")
    async def api_chat(request: Request):
        _conversation_source.set("webui")
        body = await request.json()
        message = (body.get("message") or "").strip()
        attachments = body.get("attachments") if isinstance(body.get("attachments"), list) else []
        guide_round_id = str(body.get("guide_round_id") or "").strip()
        client_request_id = str(body.get("client_request_id") or "").strip()
        wants_stream = bool(body.get("stream"))
        lang = str(body.get("lang") or "").strip()
        # Persist the user's UI language so server-side flows with no HTTP request
        # (notably the proactive scheduler) can reply in the same language.
        if lang in {"en", "zh"}:
            try:
                from cyrene.runtime.settings_store import get as _get_setting, set_ as _set_setting
                if str(_get_setting("app_language", "") or "") != lang:
                    _set_setting("app_language", lang)
            except Exception:
                pass
        command = str(body.get("command") or "").strip()
        from cyrene.agent.state import PERMISSION_MODES
        permission_mode = str(body.get("mode") or "default").strip().lower()
        if permission_mode not in PERMISSION_MODES:
            permission_mode = "default"
        from cyrene.agent.commands import DEEP_REFLECT_COMMAND_ID, parse_deep_reflect_command
        deep_reflect_parse = parse_deep_reflect_command(message)
        if deep_reflect_parse.get("matched"):
            command = DEEP_REFLECT_COMMAND_ID
        if command == DEEP_REFLECT_COMMAND_ID and not message:
            message = "/deep-reflect"
        mentions = body.get("mentions") if isinstance(body.get("mentions"), list) else []
        retry = bool(body.get("retry"))
        retry_request_id = str(body.get("retry_request_id") or "").strip()
        if retry and retry_request_id:
            await _remove_messages_by_request_id(retry_request_id)
        guide_round_id = _retry_safe_guide_round_id(guide_round_id, retry)
        normalized_attachments = [
            {
                "id": str(item.get("id") or "").strip(),
                "name": str(item.get("name") or "file"),
                "path": str(item.get("path") or ""),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "size": int(item.get("size") or 0),
                "kind": str(item.get("kind") or "file"),
                **({"width": int(item.get("width"))} if str(item.get("width", "")).strip().isdigit() else {}),
                **({"height": int(item.get("height"))} if str(item.get("height", "")).strip().isdigit() else {}),
            }
            for item in attachments
            if str(item.get("path") or "").strip()
        ]
        public_attachments = [build_public_attachment_payload(item) for item in normalized_attachments]
        if not message and not normalized_attachments and command != DEEP_REFLECT_COMMAND_ID:
            return JSONResponse({"error": "empty message"}, status_code=400)
        all_images = bool(normalized_attachments) and all(str(item.get("kind") or "") == "image" for item in normalized_attachments)
        message_with_attachments = (message or "[Attachment upload]") + _attachment_prompt_block(normalized_attachments)

        # Populate attachment path map so tool read guards auto-allow uploaded files
        # without requiring a permission prompt, even when the agent derives a wrong
        # path (e.g. /tmp/filename instead of the webui_uploads path).
        if normalized_attachments:
            att_map: dict[str, str] = {}
            for item in normalized_attachments:
                full_path = str(item.get("path") or "").strip()
                if not full_path:
                    continue
                from pathlib import Path as _Path
                uuid_name = _Path(full_path).name
                att_map[uuid_name] = full_path
                # Strip uuid prefix (format: "<hex>_<original>") to also match by original name
                parts = uuid_name.split("_", 1)
                if len(parts) == 2:
                    att_map[parts[1]] = full_path
            _attachment_paths_by_name.set(att_map)

        reset_lottery()
        if mentions and message:
            from cyrene.runtime.inbox import send_message
            from cyrene.subagent import _registry, reactivate, get_raw_messages, _spawn_subagent_task, _run_subagent

            valid_mentions = []
            for agent_id in mentions:
                agent_id = str(agent_id).strip()
                if not agent_id:
                    continue
                info = _registry.get(agent_id)
                if info is None:
                    continue
                valid_mentions.append(agent_id)
                status = str(info.get("status", "")).strip()
                if status in ("done", "timeout", "incomplete"):
                    mention_text = f"User sent you a new task. This is a round — complete it and report your result via quit.\n\n{message}"
                    await send_message("user", agent_id, "guidance", mention_text)
                    reactivated = await reactivate(agent_id)
                    if reactivated:
                        raw_msgs = await get_raw_messages(agent_id)
                        _spawn_subagent_task(
                            _run_subagent(agent_id, str(info.get("task") or ""), _bot, _CHAT_ID, _db_path, resume_messages=raw_msgs),
                            agent_id,
                        )
                else:
                    mention_text = (
                        f"[DIRECT_MESSAGE]\n"
                        f"The user has sent you guidance. This takes priority over your current approach — "
                        f"adjust your work accordingly. Use send_message_to_user ONCE to acknowledge and "
                        f"briefly say what you will change. Then continue working with the adjusted approach.\n\n"
                        f"User guidance:\n{message}"
                    )
                    await send_message("user", agent_id, "guidance", mention_text)

            if not valid_mentions:
                return JSONResponse({"error": "none of the mentioned agents exist"}, status_code=400)

            names = ", ".join(["@" + aid for aid in valid_mentions])
            response_text = f"Message sent to {names}."
            mention_prefix = " ".join(["@" + aid for aid in valid_mentions]) + " "

            user_entry = {
                "role": "user",
                "content": mention_prefix + message,
                "mentions": valid_mentions,
            }
            if normalized_attachments:
                user_entry["attachments"] = public_attachments
            if client_request_id:
                user_entry["client_request_id"] = client_request_id
            await _append_session_message(user_entry)

            if wants_stream:
                return StreamingResponse(
                    iter([_ndjson_line({"type": "reply_done", "response": response_text})]),
                    media_type="application/x-ndjson",
                    headers={"Cache-Control": "no-cache"},
                )
            return {"response": response_text}
        if guide_round_id:
            try:
                item = await queue_round_guidance(
                    guide_round_id,
                    message_with_attachments,
                    _bot,
                    _CHAT_ID,
                    _db_path,
                    client_request_id=client_request_id,
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            payload = {
                "response": f"Sent to the main-agent inbox for {guide_round_id}. It will run after the current main-agent output finishes.",
                "queued": True,
                "guide_round_id": guide_round_id,
                "guide_request_id": item.get("id", ""),
            }
            if wants_stream:
                return StreamingResponse(
                    iter([_ndjson_line({"type": "queued", **payload})]),
                    media_type="application/x-ndjson",
                    headers={"Cache-Control": "no-cache"},
                )
            return payload

        try:
            if all_images and command != DEEP_REFLECT_COMMAND_ID:
                async def _run_direct_image_chat() -> str:
                    response_text = await _chat_with_uploaded_images(message, normalized_attachments)
                    await _persist_direct_image_chat(message, response_text, public_attachments, client_request_id)
                    labels = get_session_labels()
                    await archive_exchange(
                        message,
                        response_text,
                        _CHAT_ID,
                        session_title=labels.get("session_title", ""),
                        round_title=labels.get("round_title", ""),
                        round_id=labels.get("round_id", ""),
                        archive_session_id=labels.get("archive_session_id", ""),
                    )
                    return response_text

                if wants_stream:
                    return _stream_agent_reply(_run_direct_image_chat, message or "")
                return {"response": await _run_direct_image_chat()}
            if wants_stream:
                return _stream_agent_reply(
                    lambda: run_agent(
                        message_with_attachments,
                        _bot,
                        _CHAT_ID,
                        _db_path,
                        client_request_id=client_request_id,
                        lang=lang,
                        command=command,
                        public_user_message=message,
                        public_attachments=public_attachments,
                        permission_mode=permission_mode,
                    ),
                    message or "",
                )
            response = await run_agent(
                message_with_attachments,
                _bot,
                _CHAT_ID,
                _db_path,
                client_request_id=client_request_id,
                lang=lang,
                command=command,
                public_user_message=message,
                public_attachments=public_attachments,
                permission_mode=permission_mode,
            )
            if response == _AWAITING_USER_SENTINEL:
                return {"awaiting_user": True, "pending_question": get_pending_question()}
            labels = get_session_labels()
            await archive_exchange(
                message,
                response,
                _CHAT_ID,
                session_title=labels.get("session_title", ""),
                round_title=labels.get("round_title", ""),
                round_id=labels.get("round_id", ""),
                archive_session_id=labels.get("archive_session_id", ""),
            )
            return {"response": response}
        except httpx.TimeoutException as exc:
            logger.exception(
                "Chat request timed out while calling upstream model: %s",
                format_httpx_error(exc),
            )
            return JSONResponse(
                {"error": "upstream model timed out", "detail": str(exc)},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            logger.exception(
                "Chat request failed while calling upstream model: %s",
                format_httpx_error(exc),
            )
            return JSONResponse(
                {"error": "upstream model request failed", "detail": str(exc)},
                status_code=502,
            )
        except Exception as exc:
            logger.exception("Chat request crashed")
            return JSONResponse(
                {"error": "internal server error", "detail": str(exc)},
                status_code=500,
            )

    @router.post("/api/chat/answer-question")
    async def api_answer_question(request: Request):
        _conversation_source.set("webui")
        body = await request.json()
        question_id = str(body.get("question_id") or "").strip()
        selected_option = str(body.get("selected_option") or "").strip()
        answer_text = str(body.get("answer") or "").strip() or selected_option
        client_request_id = str(body.get("client_request_id") or "").strip()
        wants_stream = bool(body.get("stream"))
        if not question_id:
            return JSONResponse({"error": "missing question_id"}, status_code=400)
        if not answer_text:
            return JSONResponse({"error": "empty answer"}, status_code=400)

        # ── Budget gate ──
        _bgt = await _check_budget_gate(question_id)
        if _bgt:
            return JSONResponse(_bgt, status_code=403)

        try:
            if wants_stream:
                return _stream_agent_reply(
                    lambda: answer_pending_question(
                        question_id,
                        answer_text,
                        _bot,
                        _CHAT_ID,
                        _db_path,
                        client_request_id=client_request_id,
                    ),
                    answer_text,
                )
            response = await answer_pending_question(
                question_id,
                answer_text,
                _bot,
                _CHAT_ID,
                _db_path,
                client_request_id=client_request_id,
            )
            if response == _AWAITING_USER_SENTINEL:
                return {"awaiting_user": True, "pending_question": get_pending_question()}
            labels = get_session_labels()
            await archive_exchange(
                answer_text,
                response,
                _CHAT_ID,
                session_title=labels.get("session_title", ""),
                round_title=labels.get("round_title", ""),
                round_id=labels.get("round_id", ""),
                archive_session_id=labels.get("archive_session_id", ""),
            )
            return {"response": response}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.TimeoutException as exc:
            logger.exception(
                "Question-answer request timed out while calling upstream model: %s",
                format_httpx_error(exc),
            )
            return JSONResponse(
                {"error": "upstream model timed out", "detail": str(exc)},
                status_code=504,
            )
        except httpx.HTTPError as exc:
            logger.exception(
                "Question-answer request failed while calling upstream model: %s",
                format_httpx_error(exc),
            )
            return JSONResponse(
                {"error": "upstream model request failed", "detail": str(exc)},
                status_code=502,
            )
        except Exception as exc:
            logger.exception("Question-answer request crashed")
            return JSONResponse(
                {"error": "internal server error", "detail": str(exc)},
                status_code=500,
            )

    @router.get("/api/chat/history")
    async def api_chat_history():
        return {"messages": _load_messages()}

    @router.get("/api/chat/state")
    async def api_chat_state():
        """Return raw session state (with round_id, tool_calls, etc.)."""
        from cyrene.config import STATE_FILE as _STATE_FILE
        if _STATE_FILE.exists():
            import json as _json
            try:
                data = _json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                msgs = data.get("messages", [])
                return {"messages": msgs if isinstance(msgs, list) else []}
            except Exception:
                pass
        return {"messages": []}

    @router.post("/api/chat/interrupt")
    async def api_interrupt_chat(session_id: str = ""):
        interrupted = interrupt_active_run(session_id=session_id)
        if session_id:
            try:
                from route.workbench.chat import (
                    _CHAT_RUN_MANAGER,
                    _settle_chat_running_status,
                )

                interrupted = _CHAT_RUN_MANAGER.interrupt(session_id) or interrupted
                # Do not acknowledge a Workbench interruption while its durable
                # chat record can still say "running". The frontend waits for
                # this response before detaching its stream, so the subsequent
                # re-sync cannot race an unfinished running -> idle write.
                await asyncio.to_thread(_settle_chat_running_status, session_id)
            except Exception:
                logger.exception("Failed to interrupt workbench chat run for %s", session_id)
        return {"ok": True, "interrupted": interrupted}

    @router.post("/api/chat/clear")
    async def api_clear_session():
        await clear_session_id()
        return {"ok": True}

    @router.get("/api/subagents")
    async def api_subagents(session_id: str = ""):
        from cyrene.subagent import _registry  # noqa: WPS437
        items = []
        for agent_id, info in _registry.items():
            if session_id and str(info.get("session_id", "")) != session_id:
                continue
            items.append({
                "id": agent_id,
                "name": agent_id,
                "task": info.get("task", ""),
                "status": info.get("status", "running"),
                "result": info.get("result", ""),
            })
        return {"subagents": items}

    @router.get("/api/rounds/live")
    async def api_live_rounds():
        return {"rounds": get_live_rounds()}

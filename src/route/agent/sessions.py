"""Legacy conversation session routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *


def register_session_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Sessions API ----

    @router.get("/api/sessions")
    async def api_sessions():
        from cyrene import db as cy_db
        try:
            now_local = datetime.now(timezone.utc).astimezone()
            day_from = (now_local - timedelta(days=27)).strftime("%Y-%m-%d")
            day_to = now_local.strftime("%Y-%m-%d")
            model_stats = await cy_db.get_model_stats_range(_db_path, day_from, day_to)
        except Exception:
            model_stats = []
        return {"sessions": _build_sessions(), "model_stats": model_stats}

    @router.post("/api/sessions")
    async def api_create_session():
        """Start a new session by clearing current state.

        Compresses the existing conversation into short-term memory first
        (handled inside clear_session_id), then wipes state.json so the
        next message starts a fresh context window.
        """
        await clear_session_id()
        return {"ok": True, "sessions": _build_sessions()}

    @router.get("/api/sessions/archive-context")
    async def api_archive_context(cursor: str = ""):
        """Return the next archive session after *cursor*.

        Cursor is a full archive session id (``archive_YYYY-MM-DD_<id>``).
        When empty, returns the most recent archive session.
        Each message has ``isArchivedContext: true`` so the frontend can
        style it as read‑only historical context.

        Skips the current live session's own archive to avoid showing
        the same messages that are already in the live view.
        """
        # Skip the archive that belongs to the current live session
        current_skip_ids: set[str] = set()
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                caid = str(state.get("archive_session_id", "")).strip()
                cad = datetime.now().astimezone().strftime("%Y-%m-%d")
                if caid:
                    current_skip_ids.add(f"{cad}:{caid}")
            except Exception:
                pass

        archives = _build_archive_sessions(skip_archive_ids=current_skip_ids)
        if not archives:
            return {"messages": [], "hasMore": False}

        start = 0
        if cursor.strip():
            for idx, a in enumerate(archives):
                if a.get("id") == cursor.strip():
                    start = idx + 1
                    break
            else:
                return {"messages": [], "hasMore": False}

        if start >= len(archives):
            return {"messages": [], "hasMore": False}

        target = archives[start]
        raw_messages = target.get("chat", {}).get("messages", [])
        for msg in raw_messages:
            msg["isArchivedContext"] = True

        return {
            "messages": raw_messages,
            "id": target["id"],
            "archiveSessionId": target.get("archiveSessionId", ""),
            "archiveDate": target.get("archiveDate", ""),
            "title": target.get("title", ""),
            "hasMore": (start + 1) < len(archives),
        }

    @router.delete("/api/sessions/{session_id}")
    async def api_delete_session(session_id: str):
        """Delete a session.

        - run_live: same as create (clear current state).
        - archive_YYYY-MM-DD_<session_id>: deletes one archived session from that day.
        """
        payload, status_code = await _delete_chat_session(session_id)
        if status_code != 200:
            return JSONResponse(payload, status_code=status_code)
        return payload

    @router.get("/api/sessions/{session_id}/export")
    async def api_export_session(session_id: str, format: str = "markdown"):
        """Export a session as Markdown or JSON.

        session_id: 'run_live' or 'archive_YYYY-MM-DD_<archive_session_id>'
        format: 'markdown' (default) or 'json'
        """
        fmt = format.strip().lower()
        if fmt not in ("markdown", "json"):
            return JSONResponse({"error": "format must be 'markdown' or 'json'"}, status_code=400)

        if session_id == "run_live":
            # Read current session from state.json
            raw_msgs: list[dict] = []
            session_title = "current session"
            created_at = ""
            if STATE_FILE.exists():
                try:
                    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    raw_msgs = state.get("messages", []) or []
                    session_title = str(state.get("session_title", "")).strip() or "current session"
                    created_at = datetime.now().astimezone().strftime("%Y-%m-%d")
                except Exception:
                    pass
            messages = []
            for m in raw_msgs:
                role = str(m.get("role", "")).strip()
                if role not in ("user", "assistant"):
                    continue
                content = str(m.get("content") or "").strip()
                if not content:
                    continue
                messages.append({
                    "role": role,
                    "content": content,
                    "time": str(m.get("created_at", "") or "").strip(),
                })
            updated_at = created_at

        elif session_id.startswith("archive_"):
            suffix = session_id[len("archive_"):]
            date_str, _, archive_session_id = suffix.partition("_")
            filepath = CONVERSATIONS_DIR / f"{date_str}.md"
            if not filepath.exists():
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                content = filepath.read_text(encoding="utf-8")
                sections = _parse_archive_sections(content)
                matching = [
                    s for s in sections
                    if str(s.get("archive_session_id", "")).strip() == archive_session_id
                ]
                if not matching and archive_session_id.startswith("legacy_"):
                    matching = [
                        s for s in sections
                        if not str(s.get("archive_session_id", "")).strip()
                    ]
                if not matching:
                    return JSONResponse({"error": "session not found"}, status_code=404)
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

            session_title = next(
                (str(s.get("session_title", "")).strip() for s in matching if s.get("session_title")),
                "",
            ) or date_str
            created_at = date_str
            timestamps = [str(s.get("timestamp", "")).strip() for s in matching if s.get("timestamp")]
            updated_at = timestamps[-1] if timestamps else date_str
            messages = []
            for s in matching:
                ts = str(s.get("timestamp", "")).strip()
                user_body = str(s.get("user_body", "")).strip()
                assistant_body = str(s.get("assistant_body", "")).strip()
                if user_body:
                    messages.append({"role": "user", "content": user_body, "time": ts})
                if assistant_body:
                    messages.append({"role": "assistant", "content": assistant_body, "time": ts})
        else:
            return JSONResponse({"error": "unknown session id"}, status_code=400)

        safe_title = re.sub(r"[^\w\-. ]+", "_", session_title or session_id, flags=re.ASCII)[:60].strip("_. ") or "session"

        if fmt == "json":
            import json as _json
            payload = {
                "id": session_id,
                "title": session_title,
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": len(messages),
                "messages": messages,
            }
            content_bytes = _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"{safe_title}.json"
            return StreamingResponse(
                iter([content_bytes]),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # Markdown format
        lines: list[str] = [f"# {session_title}", ""]
        lines.append(f"**Session ID**: `{session_id}`")
        lines.append(f"**Date**: {created_at}")
        lines.append(f"**Messages**: {len(messages)}")
        lines.append("")
        lines.append("---")
        lines.append("")

        i = 0
        while i < len(messages):
            msg = messages[i]
            ts = msg.get("time", "")
            role = msg.get("role", "user")
            content_text = msg.get("content", "")

            if role == "user":
                if ts:
                    lines.append(f"## {ts}")
                    lines.append("")
                lines.append(f"**User**: {content_text}")
                lines.append("")
                # Look for the following assistant message at the same timestamp
                if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant":
                    assistant_content = messages[i + 1].get("content", "")
                    lines.append(f"**Cyrene**: {assistant_content}")
                    lines.append("")
                    lines.append("---")
                    lines.append("")
                    i += 2
                    continue
            else:
                lines.append(f"**Cyrene**: {content_text}")
                lines.append("")
                lines.append("---")
                lines.append("")
            i += 1

        md_text = "\n".join(lines)
        content_bytes = md_text.encode("utf-8")
        filename = f"{safe_title}.md"
        return StreamingResponse(
            iter([content_bytes]),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

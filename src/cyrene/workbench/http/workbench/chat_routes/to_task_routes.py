from __future__ import annotations

import asyncio
import copy
from typing import Any

from fastapi import APIRouter

from cyrene.localization import app_language, localized
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.application.notifications import append_notification
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext


def register_to_task_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _routes = context.runtime
    _coerce_brief_acceptance = service.coerce_brief_acceptance
    _coerce_brief_constraints = service.coerce_brief_constraints
    _get_workbench_chat = service.repository.get
    _short_id = service.short_id
    _summarize_chat_to_brief = service.summarize_chat_to_brief
    _utc_now_iso = service.utc_now_iso
    _write_chat_store = service.repository.write_one

    @router.post("/api/workbench/chats/{chat_id}/to-task")
    async def api_workbench_chat_to_task(chat_id: str, body_model: api_models.ChatToTaskBody):
        """Promote a conversation into a task session of its project (开始执行)."""
        body = api_models.body_dict(body_model)
        chat = await asyncio.to_thread(_get_workbench_chat, chat_id)
        language = app_language()
        if not chat:
            return localized_error_response(
                "Chat not found.",
                "未找到对话。",
                404,
                "chat_not_found",
                language=language,
            )
        base_chat = copy.deepcopy(chat)
        R = _routes()
        store = await asyncio.to_thread(R.read_store)
        project = R.find_project(store, str(chat.get("projectId") or ""))
        if not project:
            return localized_error_response(
                "Project not found.",
                "未找到项目。",
                404,
                "project_not_found",
                language=language,
            )
        # Fallback signal when synthesis is unavailable: the last user message.
        last_user = ""
        for message in reversed(chat.get("messages") or []):
            if message.get("role") == "user" and str(message.get("content") or "").strip():
                last_user = str(message["content"]).strip()
                break

        # Synthesize a task brief from the WHOLE conversation unless the caller
        # passed explicit overrides for both title and goal.
        override_title = str(body.get("title") or "").strip()
        override_goal = str(body.get("goal") or "").strip()
        brief: dict[str, Any] = {}
        if not (override_title and override_goal):
            synthesized = await _summarize_chat_to_brief(chat, project)
            if isinstance(synthesized, dict):
                brief = synthesized

        from_synthesis = bool(brief)
        default_task_title = localized(
            "New task", "新任务", language=language
        )
        title = (override_title or str(brief.get("title") or "").strip() or str(chat.get("title") or "").strip() or default_task_title)[:80] or default_task_title
        goal = (override_goal or str(brief.get("goal") or "").strip() or last_user or title).strip()
        constraints = _coerce_brief_constraints(brief.get("constraints"))
        acceptance = _coerce_brief_acceptance(brief.get("acceptanceCriteria"))

        session = R.new_session(project.get("id"), title, goal)
        if constraints:
            session["constraints"] = constraints
        if acceptance:
            session["acceptanceCriteria"] = acceptance
        session["sourceChatId"] = chat_id
        session["events"] = [
            {
                "id": _short_id("event"),
                "type": "CreatedFromChat",
                "createdAt": _utc_now_iso(),
                "body": (
                    localized(
                        'Synthesized from the full conversation "{chat}".',
                        '由对话「{chat}」综合整理而来（已通读完整对话）。',
                        language=language,
                        chat=chat.get('title') or localized(
                            "New chat", "新对话", language=language
                        ),
                    )
                    if from_synthesis
                    else localized(
                        'Created from conversation "{chat}".',
                        '由对话「{chat}」创建。',
                        language=language,
                        chat=chat.get('title') or localized(
                            "New chat", "新对话", language=language
                        ),
                    )
                ),
                "chatId": chat_id,
            }
        ]
        project.setdefault("sessions", []).insert(0, session)
        project["updatedAt"] = session["createdAt"]
        store["activeProjectId"] = project.get("id")
        store["activeSessionId"] = session["id"]
        await asyncio.to_thread(R.write_store, store)

        # Keep the original conversation and link it to the task, so it's clearly
        # preserved (never consumed) and reachable from both sides.
        chat["convertedSessionId"] = session["id"]
        chat["convertedTaskTitle"] = title
        chat["convertedAt"] = session["createdAt"]
        await asyncio.to_thread(_write_chat_store, chat, base_chat=base_chat)
        await publish_chat_changed(
            chat_id,
            str(project.get("id") or ""),
            "converted_to_task",
            task_session_id=str(session.get("id") or ""),
        )
        await asyncio.to_thread(
            append_notification,
            title=localized(
                "Conversation converted to task",
                "对话已转为任务",
                language=language,
            ),
            body=localized(
                'Conversation "{chat}" created task "{task}".',
                '对话「{chat}」已创建任务「{task}」。',
                language=language,
                chat=chat.get('title') or localized(
                    "New chat", "新对话", language=language
                ),
                task=title,
            ),
            tab="comment",
            project_ref=project.get("id"),
            source="chat_to_task",
            source_label=localized("Task", "任务", language=language),
            link_label=title,
            meta={"chatId": chat_id, "sessionId": session["id"]},
            language=language,
        )
        return {"ok": True, "session": session, **store}

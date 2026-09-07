"""Persist a completed chat reply and its Agent-runtime projection."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
import time
from dataclasses import dataclass
from typing import Any, Callable

from cyrene.localization import app_language, localized
from cyrene.workbench.chat.chat_external_turn_service import ExternalTurnProjection
from cyrene.workbench.chat.chat_application import (
    deduplicate_projected_messages, completed_turn_count, pending_question_message,
    merge_chat_messages_chronologically, public_message, utc_now_iso,
)
from cyrene.workbench.chat.chat_usage import runtime_model_message_fields, generation_message_fields
from cyrene.workbench.application.notifications import append_notification


@dataclass(slots=True)
class ChatReplyFinalizationDependencies:
    lock: Any
    get_chat: Callable[[str], dict[str, Any] | None]
    write_chat: Callable[..., Any]
    state_messages: Callable[[str], list[dict[str, Any]]]
    extract_timeline: Callable[..., tuple[list[dict[str, Any]], dict[str, Any], list[Any]]]
    last_model: Callable[..., str]
    short_id: Callable[[str], str]
    utc_now_iso: Callable[[], str]
    merge_messages: Callable[[dict[str, Any], list[dict[str, Any]]], Any]
    next_turn_count: Callable[..., int]
    public_chat_light: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ChatReplyFinalizationRequest:
    chat_id: str
    project_id: str
    workspace_dir: str
    message: str
    command: str
    retry: bool
    is_side_agent: bool
    is_external_agent: bool
    completed_turn_count_before: int
    processing_started_at: float
    state_ids_before: set[str]
    projection: ExternalTurnProjection
    commit_retry_cut: Callable[[dict[str, Any]], None]
    timeline: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class BackgroundReplyFinalizationDependencies:
    get_chat: Callable[[str], dict[str, Any] | None]
    write_chat: Callable[..., Any]
    assistant_message: Callable[..., dict[str, Any]]


class ChatReplyFinalizationApplicationService:
    def __init__(self, dependencies: ChatReplyFinalizationDependencies) -> None:
        self.dependencies = dependencies

    def finalize(
        self,
        request: ChatReplyFinalizationRequest,
        reply_text: str,
    ) -> dict[str, Any]:
        state_messages = self.dependencies.state_messages(request.chat_id)
        timeline, usage, files = self.dependencies.extract_timeline(
            state_messages,
            request.state_ids_before,
        )
        with self.dependencies.lock:
            chat = self.dependencies.get_chat(request.chat_id)
            if not chat:
                return {}
            base_chat = copy.deepcopy(chat)
            request.commit_retry_cut(chat)
            model = (
                request.projection.model
                or self.dependencies.last_model(state_messages, request.state_ids_before)
                or str(chat.get("model") or "")
            )
            for entry in timeline:
                entry.setdefault("model", model)
            assistant = self._assistant_message(request, reply_text, model, usage, files)
            if request.timeline is not None:
                timeline = copy.deepcopy(request.timeline)
                final = next((item for item in reversed(timeline)
                              if item.get("role") == "assistant" and not item.get("activityCard") and not item.get("intermediate")
                              and not item.get("notificationCard")), None)
                if final is not None:
                    assistant = {**assistant, **final, "status": "completed"}
                    timeline = [item for item in timeline if item["id"] != final["id"]]
            else:
                self._prepend_external_projection(request.projection, timeline, assistant, model)
            saved_messages = [*timeline, assistant]
            turn_count = self._update_chat(request, chat, saved_messages, assistant, model)
            self.dependencies.write_chat(chat, base_chat=base_chat)
        self._notify(request, chat)
        summary = self.dependencies.public_chat_light(chat)
        summary["status"] = "idle"
        summary["runStatus"] = "completed"
        return {
            "assistantMessage": assistant,
            "assistantMessages": saved_messages,
            "completedTurnCount": turn_count,
            # The durable write above is authoritative.  Return the existing
            # lightweight projection with the terminal stream event so clients
            # can update rails/caches without immediately reading the chat back.
            "chatSummary": summary,
        }

    @staticmethod
    async def finalize_background(
        dependencies: BackgroundReplyFinalizationDependencies, *, chat_id: str,
        run: Any, result: Any, started_at: float, agent_originated: bool,
        media_wake: bool, wake_id: str, run_language: str,
        user_entry: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Persist and publish wake replies with their existing source policy.

        Pending questions, turn counts and notification policy intentionally
        differ from an HTTP reply; both enter this finalization boundary.
        """
        fresh = await asyncio.to_thread(dependencies.get_chat, chat_id)
        if not fresh:
            raise RuntimeError(
                localized(
                    "The chat disappeared during background continuation.",
                    "后台接续期间对话已不存在。",
                    language=run_language,
                )
            )
        fresh_base = copy.deepcopy(fresh)
        additions = ChatReplyFinalizationApplicationService._project_background_reply(
            fresh, run, result, started_at, agent_originated, media_wake, wake_id, dependencies.assistant_message,
        )
        await asyncio.to_thread(
            dependencies.write_chat,
            fresh,
            base_chat=fresh_base,
        )

        if result.status == "awaiting_user":
            event: dict[str, Any] = {
                "type": "awaiting_user",
                "pendingQuestion": (run.outcome or {}).get("pending"),
                "assistantMessages": [
                    public_message(item) for item in additions
                ],
            }
        else:
            event = {
                "type": "saved",
                "assistantMessage": public_message(additions[-1]),
                "assistantMessages": [
                    public_message(item) for item in additions
                ],
            }
        if user_entry is not None:
            event["userMessage"] = public_message(user_entry)
        await run.publish(event)
        return fresh

    @staticmethod
    def _project_background_reply(fresh, run, result, started_at,
                                  agent_originated, media_wake, wake_id, assistant_message):
        model = str(result.model or fresh.get("model") or "")
        additions = [
            {
                **copy.deepcopy(dict(item)),
                "model": str(item.get("model") or model),
            }
            for item in result.activity_messages
            if isinstance(item, Mapping)
        ]
        if (
            result.status == "awaiting_user"
            and result.pending_question is not None
        ):
            pending = result.pending_question.as_dict()
            additions.append(
                pending_question_message(
                    pending,
                    usage=result.usage,
                    latest_request_usage=result.latest_request_usage,
                    model=model,
                )
            )
            fresh["pendingQuestion"] = pending
            fresh["status"] = "idle"
            run.outcome = {"kind": "awaiting", "pending": pending}
        else:
            assistant = assistant_message(
                result=result,
                model=model,
                started_at=started_at,
                agent_originated=agent_originated,
                media_wake=media_wake,
                wake_id=wake_id,
            )
            additions.append(assistant)
            fresh.pop("pendingQuestion", None)
            fresh["status"] = "idle"
            if agent_originated:
                fresh["completedTurnCount"] = (
                    completed_turn_count(fresh) + 1
                )
            run.outcome = {
                "kind": "reply",
                "payload": {
                    "assistantMessage": assistant,
                    "assistantMessages": additions,
                },
            }
        merge_chat_messages_chronologically(fresh, additions)
        if isinstance(result.active_plan, Mapping):
            fresh["activePlan"] = copy.deepcopy(dict(result.active_plan))
        fresh["lastModel"] = model
        fresh["updatedAt"] = utc_now_iso()
        return additions

    def _assistant_message(
        self,
        request: ChatReplyFinalizationRequest,
        reply_text: str,
        model: str,
        usage: dict[str, Any],
        files: list[Any],
    ) -> dict[str, Any]:
        assistant: dict[str, Any] = {
            "id": self.dependencies.short_id("msg"),
            "role": "assistant",
            "content": str(reply_text or ""),
            "createdAt": self.dependencies.utc_now_iso(),
            "model": model,
            "processingDurationMs": max(
                0,
                int(round((time.monotonic() - request.processing_started_at) * 1000)),
            ),
        }
        effective_usage = dict(usage)
        if any(request.projection.usage.values()):
            effective_usage.update(request.projection.usage)
        assistant.update(runtime_model_message_fields(
            effective_usage, request.projection.latest_request_usage, request.projection.model_identity,
        ))
        duration = request.projection.generation_duration_ms
        rate = request.projection.output_tokens_per_second
        assistant.update(generation_message_fields(
            duration if duration is not None and duration > 0 else None,
            rate if rate is not None and rate > 0 else None,
        ))
        attachments = self._deduplicate_files([*files, *request.projection.artifacts])
        if attachments:
            assistant["attachments"] = attachments
        return assistant

    @staticmethod
    def _deduplicate_files(files: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        known: set[str] = set()
        for file in files:
            if not isinstance(file, dict):
                continue
            key = str(file.get("id") or file.get("url") or file.get("path") or "")
            if not key or key in known:
                continue
            known.add(key)
            result.append(file)
        return result

    def _prepend_external_projection(
        self,
        projection: ExternalTurnProjection,
        timeline: list[dict[str, Any]],
        assistant: dict[str, Any],
        model: str,
    ) -> None:
        known_ids = {
            str(entry.get("id") or "")
            for entry in timeline
            if isinstance(entry, dict) and str(entry.get("id") or "")
        }
        durable_activities: list[dict[str, Any]] = []
        for raw_activity in projection.activity_messages:
            if not isinstance(raw_activity, dict):
                continue
            activity = copy.deepcopy(raw_activity)
            activity_id = str(activity.get("id") or "")
            if activity_id and activity_id in known_ids:
                continue
            activity.setdefault("id", self.dependencies.short_id("activity"))
            activity.setdefault("role", "assistant")
            activity.setdefault("content", "")
            activity.setdefault("createdAt", assistant["createdAt"])
            activity.setdefault("activityCard", True)
            activity.setdefault("intermediate", True)
            activity.setdefault("model", model)
            trace = activity.get("trace")
            activity["trace"] = list(trace[-40:]) if isinstance(trace, list) else []
            durable_activities.append(activity)
            known_ids.add(str(activity.get("id") or ""))
        if durable_activities:
            timeline[0:0] = durable_activities
        if projection.trace or projection.reasoning_parts:
            timeline.insert(
                0,
                {
                    "id": self.dependencies.short_id("activity"),
                    "role": "assistant",
                    "content": "",
                    "createdAt": assistant["createdAt"],
                    "activityCard": True,
                    "reasoning": "".join(projection.reasoning_parts),
                    "trace": projection.trace[-40:],
                    "intermediate": True,
                    "model": model,
                },
            )
        if projection.notifications:
            timeline[0:0] = [
                {
                    "id": str(notice.get("eventId") or self.dependencies.short_id("notice")),
                    "role": "assistant",
                    "content": "",
                    "createdAt": str(notice.get("createdAt") or assistant["createdAt"]),
                    "notificationCard": True,
                    "notification": {
                        key: notice[key]
                        for key in ("severity", "category", "message", "source", "terminal")
                        if key in notice
                    },
                    "intermediate": True,
                    "model": model,
                }
                for notice in projection.notifications
            ]
        timeline[:] = deduplicate_projected_messages(timeline)

    def _update_chat(
        self,
        request: ChatReplyFinalizationRequest,
        chat: dict[str, Any],
        saved_messages: list[dict[str, Any]],
        assistant: dict[str, Any],
        model: str,
    ) -> int:
        projection = request.projection
        if projection.commands is not None:
            chat["agentCommands"] = projection.commands
        if isinstance(projection.plan, dict):
            chat["activePlan"] = projection.plan
        if projection.agent_mode is not None:
            chat["agentMode"] = projection.agent_mode
        if projection.config_options:
            options = [
                item
                for item in (chat.get("agentConfigOptions") or [])
                if isinstance(item, dict)
                and str(item.get("id") or "") not in projection.config_options
            ]
            options.extend(projection.config_options.values())
            chat["agentConfigOptions"] = options[:100]
        chat["lastModel"] = model
        self.dependencies.merge_messages(chat, saved_messages)
        turn_count = self.dependencies.next_turn_count(
            {"completedTurnCount": request.completed_turn_count_before},
            retry=request.retry,
            command=request.command,
            is_side_agent=request.is_side_agent,
        )
        chat["completedTurnCount"] = turn_count
        chat["status"] = "idle"
        chat.pop("pendingQuestion", None)
        chat["updatedAt"] = assistant["createdAt"]
        return turn_count

    @staticmethod
    def _notify(
        request: ChatReplyFinalizationRequest,
        chat: dict[str, Any],
    ) -> None:
        if request.command or request.retry or request.is_side_agent:
            return
        language = app_language()
        chat_title = chat.get('title') or localized(
            "New chat", "新对话", language=language
        )
        append_notification(
            title=localized(
                "Agent reply completed", "Agent 回复完成", language=language
            ),
            body=localized(
                'The Agent replied to you in "{title}".',
                'Agent 在「{title}」中回复了你。',
                language=language,
                title=chat_title,
            ),
            tab="mention",
            project_ref=request.project_id,
            source="workbench_chat_reply",
            source_label=localized(
                "Conversation", "对话", language=language
            ),
            link_label=str(chat.get("title") or ""),
            meta={"chatId": request.chat_id},
            language=language,
        )


__all__ = [
    "ChatReplyFinalizationApplicationService",
    "ChatReplyFinalizationDependencies",
    "ChatReplyFinalizationRequest",
]

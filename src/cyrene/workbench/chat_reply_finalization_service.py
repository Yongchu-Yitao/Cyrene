"""Persist a completed chat reply and its Agent-runtime projection."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable

from cyrene.workbench.chat_external_turn_service import ExternalTurnProjection
from cyrene.workbench.notifications import append_notification


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
        if any(effective_usage.values()):
            assistant["usage"] = effective_usage
        if request.projection.model_identity:
            assistant["modelIdentity"] = dict(request.projection.model_identity)
        generation_duration_ms = request.projection.generation_duration_ms
        if generation_duration_ms is not None and generation_duration_ms > 0:
            assistant["modelGenerationDurationMs"] = round(
                generation_duration_ms,
                3,
            )
        output_rate = request.projection.output_tokens_per_second
        if output_rate is not None and output_rate > 0:
            assistant["outputTokensPerSecond"] = round(output_rate, 3)
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
        append_notification(
            title="Agent 回复完成",
            body=f"Agent 在「{chat.get('title') or '新对话'}」中回复了你。",
            tab="mention",
            project_ref=request.project_id,
            source="workbench_chat_reply",
            source_label="对话",
            link_label=str(chat.get("title") or ""),
            meta={"chatId": request.chat_id},
        )


__all__ = [
    "ChatReplyFinalizationApplicationService",
    "ChatReplyFinalizationDependencies",
    "ChatReplyFinalizationRequest",
]

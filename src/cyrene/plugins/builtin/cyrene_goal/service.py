"""Conversation-native durable Goal controller."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cyrene.core.plugin import PluginContext, application_plugin_service
from cyrene.localization import localized
from cyrene.plugins.native_runtime import run_context_value
from cyrene.workbench.application.notifications import append_notification
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_runs import ChatRun
from cyrene.workbench.chat.chat_service import ChatService, get_chat_run_manager
from cyrene.workbench.core_adapter.bridge import AgentSessionRunError
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig
from cyrene.workbench.projects import project_repository

from .repository import ConversationGoalRepository, utc_iso
from .state import persist_goal, persist_goal_by_id, public_goal

logger = logging.getLogger(__name__)

RUNNABLE = frozenset({"active", "reviewing", "reflecting"})
TERMINAL = frozenset({"completed", "aborted"})
DEFAULT_DURATION_SECONDS = 2 * 60 * 60
REVIEW_RETRY_DELAYS = (1.0, 3.0)


def _strings(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item or "").strip()[:2000]
        for item in value[:limit]
        if str(item or "").strip()
    ]


def _duration(value: Any) -> int:
    try:
        return max(300, min(int(value or DEFAULT_DURATION_SECONDS), 7 * 24 * 3600))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_DURATION_SECONDS


def _json_object(text: str) -> dict[str, Any] | None:
    source = str(text or "").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*", "", source, flags=re.I)
        source = re.sub(r"\s*```$", "", source)
    try:
        value = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"\{[\s\S]*\}", source)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return dict(value) if isinstance(value, Mapping) else None


def _reflection_committed(result: Any) -> bool:
    """Return whether this turn durably rewrote the conversation context."""

    snapshot = getattr(result, "snapshot", None)
    nodes = snapshot.get("nodes") if isinstance(snapshot, Mapping) else None
    run_id = str(getattr(result, "run_id", "") or "")
    if not isinstance(nodes, list) or not run_id:
        return False
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("value"), Mapping)
        and item["value"].get("role") == "context_reflection"
        and str(item["value"].get("run_id") or "") == run_id
        for item in nodes
    )


class ConversationGoalService:
    """Own Goal state and drive bounded Conversation turns until review passes."""

    def __init__(self, *, db_path: str, bot: Any) -> None:
        self.db_path = str(db_path)
        self.bot = bot
        self.repository = ConversationGoalRepository(self.db_path)
        self.chat = ChatService(self.db_path)
        self.run_manager = get_chat_run_manager()
        self._closed = False
        self._wake_tasks: set[asyncio.Task[Any]] = set()
        self._stopping_chat_ids: set[str] = set()

    @staticmethod
    def public(goal: Mapping[str, Any]) -> dict[str, Any] | None:
        return public_goal(goal)

    async def startup(self) -> None:
        self._closed = False
        await self.repository.ensure_schema()
        for goal in await self.repository.active():
            status = str(goal.get("status") or "")
            if status == "paused" and str(goal.get("stopReason") or "") == "goal_plugin_shutdown":
                restored = str(goal.get("pausedFromStatus") or "active")
                status = restored if restored in RUNNABLE else "active"
                goal.update({
                    "status": status,
                    "phase": "executing" if status == "active" else status,
                    "activeStartedAt": utc_iso(),
                    "stopReason": "",
                })
                goal = await self.repository.save(goal)
                await self._project(goal)
            if status in RUNNABLE:
                self.wake(str(goal.get("chatId") or ""))

    async def shutdown(self) -> None:
        self._closed = True
        for task in tuple(self._wake_tasks):
            task.cancel()
        if self._wake_tasks:
            await asyncio.gather(*tuple(self._wake_tasks), return_exceptions=True)
        self._wake_tasks.clear()
        for goal in await self.repository.active():
            if str(goal.get("status") or "") in RUNNABLE:
                await self._stop_live_run(
                    str(goal.get("chatId") or ""),
                    "goal_plugin_shutdown",
                )

    async def begin_negotiation(
        self,
        chat_id: str,
        *,
        initial_request: str = "",
        project_id: str = "",
    ) -> dict[str, Any]:
        current = await self.repository.get(chat_id)
        if current and str(current.get("status") or "") not in TERMINAL:
            await self._project(current)
            return current
        now = utc_iso()
        goal = {
            "id": "goal_" + uuid4().hex[:12],
            "chatId": str(chat_id),
            "projectId": str(project_id or ""),
            "revision": 1,
            "status": "negotiating",
            "phase": "negotiating",
            "objective": str(initial_request or "").strip(),
            "acceptanceCriteria": [],
            "constraints": [],
            "outOfScope": [],
            "durationSeconds": DEFAULT_DURATION_SECONDS,
            "activeSeconds": 0.0,
            "activeStartedAt": "",
            "attempt": 0,
            "candidate": None,
            "review": None,
            "childContextIds": [],
            "completionMode": "",
            "stopReason": "",
            "createdAt": now,
            "updatedAt": now,
        }
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "negotiation_started", {
            "initialRequest": str(initial_request or "").strip(),
        })
        await self._project(goal)
        await self._milestone(goal, "negotiation_started", localized(
            "Goal discussion started. I will research the request and confirm a measurable target with you.",
            "目标协商已开始。我会先研究任务，并与你确认一个可衡量的目标。",
        ))
        return goal

    async def propose_from_context(
        self,
        context: PluginContext,
        args: Mapping[str, Any],
    ) -> dict[str, Any]:
        chat_id = str(run_context_value(context, "session_id") or context.tree_id or "").strip()
        if not chat_id:
            raise RuntimeError("Goal proposal requires a conversation")
        goal = await self.repository.get(chat_id)
        if goal is None or str(goal.get("status") or "") in TERMINAL:
            raise RuntimeError("Start a Goal discussion with /goal before proposing it")
        if str(goal.get("status") or "") not in {"negotiating", "proposed"}:
            raise RuntimeError("Edit a running Goal from the Goal tab")
        self._ensure_not_stopping(chat_id)
        goal.update({
            "status": "proposed",
            "phase": "awaiting_confirmation",
            "objective": str(args.get("objective") or "").strip(),
            "acceptanceCriteria": _strings(args.get("acceptanceCriteria")),
            "constraints": _strings(args.get("constraints")),
            "outOfScope": _strings(args.get("outOfScope")),
            "durationSeconds": _duration(args.get("suggestedDurationSeconds")),
            "candidate": None,
            "review": None,
            "stopReason": "",
        })
        goal = await self.repository.save(goal)
        persist_goal(context, goal)
        await self.repository.event(goal, "goal_proposed", self.public(goal) or {})
        await self._project(goal)
        await self._milestone(goal, "goal_proposed", localized(
            "The Goal proposal is ready. Review and confirm it in the dialog before continuous execution starts.",
            "目标提案已准备好。请在弹窗中检查并确认，确认后才会开始持续执行。",
        ))
        return goal

    async def submit_candidate_from_context(
        self,
        context: PluginContext,
        args: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        chat_id = str(run_context_value(context, "session_id") or context.tree_id or "").strip()
        goal = await self.repository.get(chat_id)
        if goal is None or str(goal.get("status") or "") != "active":
            return None
        self._ensure_not_stopping(chat_id)
        goal["candidate"] = {
            "summary": str(args.get("summary") or "").strip(),
            "evidence": _strings(args.get("evidence"), limit=50),
            "deliverables": _strings(args.get("deliverables"), limit=50),
            "submittedAt": utc_iso(),
            "goalRevision": int(goal.get("revision") or 1),
        }
        goal["status"] = "reviewing"
        goal["phase"] = "reviewing"
        goal = await self.repository.save(goal)
        persist_goal(context, goal)
        await self.repository.event(goal, "candidate_submitted", dict(goal["candidate"]))
        await self._project(goal)
        return goal

    async def get(self, chat_id: str) -> dict[str, Any] | None:
        goal = await self.repository.get(chat_id)
        if goal is not None:
            goal = {**goal, "events": await self.repository.events(chat_id)}
        return goal

    async def confirm(self, chat_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_not_stopping(chat_id)
        goal = await self._required(chat_id)
        if str(goal.get("status") or "") not in {"proposed", "paused", "blocked"}:
            raise ValueError("Goal is not awaiting confirmation")
        self._apply_definition(goal, body)
        goal.update({
            "status": "active",
            "phase": "executing",
            "activeStartedAt": utc_iso(),
            "pausedFromStatus": "",
            "waitingFromStatus": "",
            "candidate": None,
            "review": None,
            "stopReason": "",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "goal_confirmed", self.public(goal) or {})
        await self._project(goal)
        await self._milestone(goal, "goal_confirmed", localized(
            "Goal confirmed. Continuous execution has started.",
            "目标已确认，持续执行已经开始。",
        ))
        self.wake(chat_id)
        return goal

    async def update(self, chat_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_not_stopping(chat_id)
        goal = await self._required(chat_id)
        if str(goal.get("status") or "") in TERMINAL:
            raise ValueError("Goal is already finished")
        definition_changed = any(
            key in body for key in ("objective", "acceptanceCriteria", "constraints", "outOfScope")
        )
        if definition_changed:
            await self._stop_live_run(chat_id, "goal_revision_changed")
            goal = await self._required(chat_id)
            goal["revision"] = int(goal.get("revision") or 1) + 1
            goal["status"] = "proposed"
            goal["phase"] = "awaiting_confirmation"
            goal["candidate"] = None
            goal["review"] = None
            goal["stopReason"] = "goal_revision_changed"
            goal["pausedFromStatus"] = ""
            goal["waitingFromStatus"] = ""
        self._apply_definition(goal, body)
        self._ensure_not_stopping(chat_id)
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "goal_updated", {
            "definitionChanged": definition_changed,
            "revision": goal.get("revision"),
        })
        await self._project(goal)
        if definition_changed:
            await self._milestone(goal, "goal_revised", localized(
                "The Goal was revised and paused for confirmation.",
                "目标已修改，并暂停等待重新确认。",
            ))
        return goal

    async def pause(self, chat_id: str) -> dict[str, Any]:
        self._ensure_not_stopping(chat_id)
        goal = await self._required(chat_id)
        previous_status = str(goal.get("status") or "")
        if previous_status in TERMINAL | {"paused", "proposed", "negotiating"}:
            raise ValueError("Goal cannot be paused in its current state")
        await self._stop_live_run(chat_id, "goal_paused")
        self._settle_active_time(goal)
        resume_status = previous_status
        waiting_from = str(goal.get("waitingFromStatus") or "")
        if previous_status == "waiting_user" and waiting_from in RUNNABLE:
            resume_status = waiting_from
        self._ensure_not_stopping(chat_id)
        goal.update({
            "status": "paused",
            "phase": "paused",
            "pausedFromStatus": resume_status,
            "stopReason": "user_paused",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "paused")
        await self._project(goal)
        await self._milestone(goal, "paused", localized("Goal execution paused.", "目标执行已暂停。"))
        return goal

    async def resume(self, chat_id: str) -> dict[str, Any]:
        self._ensure_not_stopping(chat_id)
        goal = await self._required(chat_id)
        if str(goal.get("status") or "") not in {"paused", "blocked"}:
            raise ValueError("Goal is not paused")
        resume_status = str(goal.get("pausedFromStatus") or "")
        if goal.get("candidate") and not goal.get("review"):
            # Older controller failures did not persist pausedFromStatus when
            # the reviewer crashed. A submitted, unreviewed candidate is an
            # unambiguous review checkpoint and must not rerun execution.
            resume_status = "reviewing"
        elif resume_status not in RUNNABLE:
            resume_status = "active"
        chat = self.chat.repository.get(chat_id)
        if chat and chat.get("pendingQuestion"):
            goal.update({
                "status": "waiting_user",
                "phase": "waiting_user",
                "waitingFromStatus": resume_status,
                "stopReason": "waiting_user",
            })
            goal = await self.repository.save(goal)
            await self._project(goal)
            return goal
        goal.update({
            "status": resume_status,
            "phase": "executing" if resume_status == "active" else resume_status,
            "activeStartedAt": utc_iso(),
            "pausedFromStatus": "",
            "waitingFromStatus": "",
            "stopReason": "",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "resumed")
        await self._project(goal)
        self.wake(chat_id)
        return goal

    async def abort(self, chat_id: str) -> dict[str, Any]:
        chat_id = str(chat_id)
        if chat_id in self._stopping_chat_ids:
            return await self._required(chat_id)
        self._stopping_chat_ids.add(chat_id)
        try:
            goal = await self._required(chat_id)
            if str(goal.get("status") or "") in TERMINAL:
                raise ValueError("Goal is already finished")
            # Persist the terminal state before waiting for the live Agent run.
            # This makes Stop authoritative even when another tab action or
            # controller turn is currently settling.
            self._settle_active_time(goal)
            goal.update({
                "status": "aborted",
                "phase": "aborted",
                "stopReason": "user_aborted",
            })
            goal = await self.repository.save(goal)
            await self.repository.event(goal, "aborted")
            await self._project(goal)
            await self._stop_live_run(chat_id, "goal_aborted")
            execution = application_plugin_service("workspace_execution")
            if execution is not None:
                await execution.stop_goal(str(goal.get("id") or ""))
            await self._milestone(goal, "aborted", localized(
                "Goal execution was stopped.",
                "目标执行已终止。",
            ))
            return goal
        finally:
            self._stopping_chat_ids.discard(chat_id)

    async def accept(self, chat_id: str) -> dict[str, Any]:
        self._ensure_not_stopping(chat_id)
        goal = await self._required(chat_id)
        if (
            str(goal.get("status") or "") in TERMINAL
            or int(goal.get("attempt") or 0) < 1
            or not (goal.get("candidate") or goal.get("review"))
        ):
            raise ValueError("There is no current Goal result to accept")
        await self._stop_live_run(chat_id, "accepted_by_user")
        execution = application_plugin_service("workspace_execution")
        if execution is not None:
            await execution.stop_goal(str(goal.get("id") or ""))
        self._ensure_not_stopping(chat_id)
        self._settle_active_time(goal)
        goal.update({
            "status": "completed",
            "phase": "completed",
            "completionMode": "accepted_by_user",
            "completedAt": utc_iso(),
            "stopReason": "",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "accepted_by_user", {
            "review": goal.get("review"),
        })
        await self._project(goal)
        await self._milestone(goal, "accepted_by_user", localized(
            "You accepted the current result. The Goal loop has ended.",
            "你已手动接受当前结果，目标循环已经结束。",
        ))
        await self._notify_goal_state(goal, "completed")
        return goal

    async def delete_chat(self, chat_id: str) -> None:
        await self._stop_live_run(chat_id, "chat_deleted")
        await self.repository.delete(chat_id)

    async def on_conversation_deleted(self, chat_id: str) -> None:
        await self.delete_chat(chat_id)

    async def on_conversation_settled(self, chat_id: str) -> None:
        goal = await self.repository.get(chat_id)
        if goal is None:
            return
        status = str(goal.get("status") or "")
        if status == "waiting_user":
            chat = self.chat.repository.get(chat_id)
            if chat and chat.get("pendingQuestion"):
                return
            restored = str(goal.get("waitingFromStatus") or "active")
            if restored not in RUNNABLE:
                restored = "active"
            goal.update({
                "status": restored,
                "phase": "executing" if restored == "active" else restored,
                "waitingFromStatus": "",
                "stopReason": "",
            })
            goal = await self.repository.save(goal)
            await self._project(goal)
            status = restored
        if status in RUNNABLE:
            self.wake(chat_id)

    def wake(self, chat_id: str) -> None:
        if self._closed or not str(chat_id or "").strip():
            return
        task = asyncio.create_task(self._wake(str(chat_id)))
        self._wake_tasks.add(task)
        task.add_done_callback(self._wake_tasks.discard)

    async def _wake(self, chat_id: str) -> None:
        goal = await self.repository.get(chat_id)
        if goal is None or str(goal.get("status") or "") not in RUNNABLE:
            return

        async def runner(run: ChatRun) -> None:
            await self._drive(run)

        run, is_new = self.run_manager.start_or_get(
            chat_id,
            {"type": "ack", "chatId": chat_id, "goal": True},
            runner,
            stream=True,
            settler=self._settle_run,
        )
        if is_new:
            chat = self.chat.repository.get(chat_id)
            if chat:
                await publish_chat_changed(
                    chat_id,
                    str(chat.get("projectId") or ""),
                    "goal_run_started",
                    run_id=run.run_id,
                    run_status="running",
                    chatSummary=self.chat.public_chat_light(chat),
                )
        if not is_new:
            # A normal conversation turn may still be settling. Retry exactly
            # once it releases the shared conversation lease.
            await run.done.wait()
            next_goal = await self.repository.get(chat_id)
            if next_goal and str(next_goal.get("status") or "") in RUNNABLE:
                self.wake(chat_id)

    async def _drive(self, run: ChatRun) -> None:
        chat_id = run.chat_id
        while not self._closed:
            goal = await self.repository.get(chat_id)
            if goal is None or str(goal.get("status") or "") not in RUNNABLE:
                return
            if self._duration_exhausted(goal):
                await self._pause_for_duration(goal)
                return
            if str(goal.get("status") or "") == "reflecting":
                await self._reflect(run, goal)
                continue
            if goal.get("candidate") and str(goal.get("status") or "") == "reviewing":
                await self._review(run, goal)
                continue
            goal["attempt"] = int(goal.get("attempt") or 0) + 1
            goal["status"] = "active"
            goal["phase"] = "executing"
            goal["activeStartedAt"] = goal.get("activeStartedAt") or utc_iso()
            goal = await self.repository.save(goal)
            await self.repository.event(goal, "attempt_started")
            await self._project(goal)
            await self._milestone(goal, "attempt_started", localized(
                "Goal attempt {attempt} started.",
                "目标第 {attempt} 次尝试开始。",
                attempt=int(goal.get("attempt") or 0),
            ))
            result = await self._conversation_turn(run, goal, reflection=False)
            await self._persist_runtime_result(goal, result)
            goal = await self.repository.get(chat_id) or goal
            if str(goal.get("status") or "") not in RUNNABLE:
                return
            if result.pending_question is not None:
                goal.update({
                    "status": "waiting_user",
                    "phase": "waiting_user",
                    "waitingFromStatus": "active",
                    "stopReason": "waiting_user",
                })
                goal = await self.repository.save(goal)
                await self._project(goal)
                return
            goal = await self.repository.get(chat_id) or goal
            if goal.get("candidate"):
                goal["status"] = "reviewing"
                goal["phase"] = "reviewing"
                goal = await self.repository.save(goal)
                await self._project(goal)
                continue
            # The Agent returned progress without submitting completion. Start
            # another bounded turn in the same durable Goal lifecycle.

    async def _review(self, run: ChatRun, goal: dict[str, Any]) -> None:
        await self._milestone(goal, "review_started", localized(
            "The candidate result was submitted for independent review.",
            "候选结果已提交独立审查。",
        ))
        review_id = f"{goal['chatId']}.goal.{goal['id']}.review.{goal.get('attempt', 0)}"
        children = list(goal.get("childContextIds") or [])
        if review_id not in children:
            children.append(review_id)
        goal["childContextIds"] = children
        goal = await self.repository.save(goal)
        await self._project(goal)
        config = await self._config(
            goal,
            run,
            session_id=review_id,
            permission_mode="plan",
            read_only=True,
        )
        prompt = self._review_prompt(goal)
        result = None
        last_error: AgentSessionRunError | None = None
        total_attempts = len(REVIEW_RETRY_DELAYS) + 1
        for review_attempt in range(1, total_attempts + 1):
            try:
                result = await self.run_manager.conversation_runtime.send(
                    config,
                    prompt,
                    run_id="goal_review_" + uuid4().hex,
                    metadata={
                        "goal_review": True,
                        "goal_id": goal.get("id"),
                        "goal_revision": goal.get("revision"),
                        "review_attempt": review_attempt,
                    },
                    publish=run.publish,
                )
                break
            except AgentSessionRunError as exc:
                last_error = exc
                latest = await self.repository.get(str(goal["chatId"]))
                if (
                    latest is None
                    or int(latest.get("revision") or 0) != int(goal.get("revision") or 0)
                    or str(latest.get("status") or "") != "reviewing"
                ):
                    return
                if review_attempt >= total_attempts:
                    await self._pause_unavailable_review(
                        latest,
                        attempts=review_attempt,
                        error=exc,
                    )
                    return
                await self.repository.event(latest, "review_retry_scheduled", {
                    "reviewAttempt": review_attempt + 1,
                    "maxAttempts": total_attempts,
                })
                await self._milestone(latest, "review_retry_scheduled", localized(
                    "The independent reviewer became temporarily unavailable. Retrying review ({attempt}/{maximum}).",
                    "独立审查暂时不可用，正在重试审查（{attempt}/{maximum}）。",
                    attempt=review_attempt + 1,
                    maximum=total_attempts,
                ))
                await asyncio.sleep(REVIEW_RETRY_DELAYS[review_attempt - 1])
        if result is None:
            # The exhausted path pauses above. This guard keeps type and state
            # handling explicit if the retry loop changes later.
            if last_error is not None:
                raise last_error
            return
        await self._apply_review(run, goal, result.text)

    async def _apply_review(
        self,
        run: ChatRun,
        goal: dict[str, Any],
        result_text: str,
    ) -> None:
        """Apply one reviewer verdict only to the revision it inspected."""

        review = self._normalize_review(_json_object(result_text), goal)
        latest = await self.repository.get(str(goal["chatId"])) or goal
        if (
            int(latest.get("revision") or 0) != int(goal.get("revision") or 0)
            or str(latest.get("status") or "") != "reviewing"
        ):
            return
        latest["review"] = review
        await self.repository.event(latest, "review_completed", review)
        if review["verdict"] == "pass":
            execution = application_plugin_service("workspace_execution")
            if execution is not None:
                await execution.stop_goal(str(latest.get("id") or ""))
            self._settle_active_time(latest)
            latest.update({
                "status": "completed",
                "phase": "completed",
                "completionMode": "review_passed",
                "completedAt": utc_iso(),
                "stopReason": "",
            })
            latest = await self.repository.save(latest)
            await self._project(latest)
            await self._milestone(latest, "review_passed", localized(
                "Independent review passed every required criterion. The Goal loop has ended.",
                "独立审查已通过全部必要验收标准，目标循环已经结束。",
            ))
            await self._notify_goal_state(latest, "completed")
            return
        latest.update({"status": "reflecting", "phase": "reflecting", "candidate": None})
        latest = await self.repository.save(latest)
        await self._project(latest)
        await self._milestone(latest, "review_failed", localized(
            "Independent review found gaps. Deep reflection will rebuild the working context before the next attempt.",
            "独立审查发现缺口，将先深度反思并重构工作上下文，再开始下一次尝试。",
        ))
        await self._reflect(run, latest)

    async def _pause_unavailable_review(
        self,
        goal: dict[str, Any],
        *,
        attempts: int,
        error: AgentSessionRunError,
    ) -> None:
        """Preserve a submitted candidate when reviewer infrastructure fails."""

        self._settle_active_time(goal)
        goal.update({
            "status": "paused",
            "phase": "paused",
            "pausedFromStatus": "reviewing",
            "stopReason": "review_provider_unavailable",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "review_unavailable", {
            "attempts": max(1, int(attempts)),
            "errorType": type(error).__name__,
        })
        await self._project(goal)
        await self._milestone(goal, "review_unavailable", localized(
            "Independent review is temporarily unavailable. The submitted result was preserved; continue the Goal to retry review.",
            "独立审查暂时不可用。候选结果已保留，继续目标即可重新审查。",
        ))
        await self._notify_goal_state(goal, "review_unavailable")

    async def _reflect(self, run: ChatRun, goal: dict[str, Any]) -> None:
        """Stay in reflection until DeepReflect commits or the Goal is interrupted."""

        chat_id = str(goal.get("chatId") or "")
        revision = int(goal.get("revision") or 0)
        while not self._closed:
            latest = await self.repository.get(chat_id)
            if (
                latest is None
                or int(latest.get("revision") or 0) != revision
                or str(latest.get("status") or "") != "reflecting"
            ):
                return
            if self._duration_exhausted(latest):
                await self._pause_for_duration(latest)
                return
            reflection_result = await self._conversation_turn(
                run,
                latest,
                reflection=True,
            )
            await self._persist_runtime_result(latest, reflection_result)
            latest = await self.repository.get(chat_id) or latest
            if (
                int(latest.get("revision") or 0) != revision
                or str(latest.get("status") or "") != "reflecting"
            ):
                return
            if reflection_result.pending_question is not None:
                latest.update({
                    "status": "waiting_user",
                    "phase": "waiting_user",
                    "waitingFromStatus": "reflecting",
                    "stopReason": "waiting_user",
                })
                latest = await self.repository.save(latest)
                await self._project(latest)
                return
            if not _reflection_committed(reflection_result):
                await self.repository.event(latest, "reflection_retry", {
                    "runId": str(getattr(reflection_result, "run_id", "") or ""),
                    "reason": "deep_reflect_not_committed",
                })
                continue
            latest.update({
                "status": "active",
                "phase": "executing",
                "waitingFromStatus": "",
                "stopReason": "",
            })
            latest = await self.repository.save(latest)
            await self.repository.event(latest, "reflection_completed")
            await self._project(latest)
            await self._milestone(latest, "reflection_completed", localized(
                "Deep reflection completed. Continuing with a rebuilt working context.",
                "深度反思已完成，将使用重构后的工作上下文继续。",
            ))
            return

    async def _conversation_turn(
        self,
        run: ChatRun,
        goal: dict[str, Any],
        *,
        reflection: bool,
    ):
        from cyrene.workbench.application.commands import command_system_prompt

        config = await self._config(goal, run)
        if reflection:
            config = replace(
                config,
                command="deep-reflect",
                system_extra=(
                    command_system_prompt("deep-reflect")
                    + "\n\n"
                    + self._reflection_prompt(goal)
                ),
            )
            prompt = self._reflection_prompt(goal)
        else:
            prompt = self._execution_prompt(goal)
        return await self.run_manager.conversation_runtime.send(
            config,
            prompt,
            run_id=("goal_reflect_" if reflection else "goal_attempt_") + uuid4().hex,
            metadata={
                "controller_owned": True,
                "public_visibility": "activity",
                "goal_id": goal.get("id"),
                "goal_revision": goal.get("revision"),
                "goal_attempt": goal.get("attempt"),
                "goal_reflection": reflection,
            },
            publish=run.publish,
        )

    async def _config(
        self,
        goal: Mapping[str, Any],
        run: ChatRun,
        *,
        session_id: str = "",
        permission_mode: str = "",
        read_only: bool = False,
    ) -> ConversationConfig:
        chat = self.chat.repository.get(str(goal.get("chatId") or ""))
        if not chat:
            raise RuntimeError("Goal conversation no longer exists")
        project = project_repository.find_workbench_project_lightweight(
            str(chat.get("projectId") or goal.get("projectId") or "")
        )
        if not project:
            raise RuntimeError("Goal project no longer exists")
        workspace = self.chat.resolve_chat_workspace_dir(
            chat,
            project,
            project_repository.resolve_project_workspace_dir,
        )
        input_context = self.chat.resolve_composer_input_context(chat, workspace, strict=True)
        memory = self.chat.ensure_chat_memory_snapshot(chat)
        return ConversationConfig(
            session_id=session_id or str(goal.get("chatId") or ""),
            workspace_dir=workspace,
            db_path=self.db_path,
            bot=self.bot,
            host_chat_id=str(goal.get("chatId") or ""),
            permission_mode=permission_mode or str(chat.get("permissionMode") or "auto"),
            public_user_message="",
            remote_device_ids=tuple(input_context["remoteDeviceIds"]),
            soul_enabled=bool(input_context["soulActive"]),
            workspace_enabled=bool(input_context["workspaceActive"]),
            context_activations=dict(input_context["contextActivations"]),
            resolved_context_activations=dict(input_context["resolvedContextActivations"]),
            project_id=str(project.get("id") or ""),
            project_memory_snapshot=memory,
            session_title=str(chat.get("title") or ""),
            memory_write_enabled=session_id == "",
            memory_trigger_enabled=session_id == "",
            memory_archive_enabled=True,
            completed_turn_count=int(chat.get("completedTurnCount") or 0) + 1,
            response_capabilities=("interactive_blocks",),
            conversation_source="goal_controller",
            guidance_channel=run.guidance_channel,
            read_only=read_only,
        )

    def _execution_prompt(self, goal: Mapping[str, Any]) -> str:
        return (
            "[Goal Controller]\n"
            "Continue working toward the confirmed Goal below. This is a bounded iteration inside a durable while loop. "
            "Research, edit, run, test, and use the normal unified permission system as needed. Keep the conversation natural. "
            "When and only when the result appears fully complete, call submit_goal_result with concrete evidence. "
            "Do not claim that the Goal is completed yourself; an independent reviewer decides.\n\n"
            + json.dumps({
                "goalId": goal.get("id"),
                "revision": goal.get("revision"),
                "objective": goal.get("objective"),
                "acceptanceCriteria": goal.get("acceptanceCriteria"),
                "constraints": goal.get("constraints"),
                "outOfScope": goal.get("outOfScope"),
                "attempt": goal.get("attempt"),
                "previousReview": goal.get("review"),
            }, ensure_ascii=False, default=str)
        )

    def _review_prompt(self, goal: Mapping[str, Any]) -> str:
        indexed_criteria = [
            {"criterionIndex": index, "criterion": criterion}
            for index, criterion in enumerate(
                _strings(goal.get("acceptanceCriteria")),
                start=1,
            )
        ]
        return (
            "You are an independent Goal reviewer in a fresh read-only context. Inspect workspace files and existing execution evidence. "
            "Use WorkspaceAction list/status when useful, but do not run commands, tests, builds, start processes, or edit files. "
            "Do not trust the executor's summary without evidence. Return only JSON with verdict ('pass' or 'fail'), criteria "
            "(array of {criterionIndex, criterion, passed, evidence, reason}), criticalGaps (array), and summary. Return exactly one "
            "criteria entry for every indexed acceptance criterion, preserving both its index and text. "
            "Pass only when every required criterion is fully satisfied and no critical gap remains.\n\n"
            + json.dumps({
                "goalId": goal.get("id"),
                "revision": goal.get("revision"),
                "objective": goal.get("objective"),
                "acceptanceCriteria": indexed_criteria,
                "constraints": goal.get("constraints"),
                "outOfScope": goal.get("outOfScope"),
                "candidate": goal.get("candidate"),
            }, ensure_ascii=False, default=str)
        )

    def _reflection_prompt(self, goal: Mapping[str, Any]) -> str:
        return (
            "The independent Goal review failed. Call DeepReflect immediately as the only tool in the first tool-call turn. "
            "Use the confirmed Goal and review gaps to discard wrong assumptions, rebuild the working context, and continue with the first useful repair action.\n\n"
            + json.dumps({
                "goal": goal.get("objective"),
                "acceptanceCriteria": goal.get("acceptanceCriteria"),
                "review": goal.get("review"),
                "attempt": goal.get("attempt"),
            }, ensure_ascii=False, default=str)
        )

    def _normalize_review(
        self,
        raw: dict[str, Any] | None,
        goal: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = raw or {}
        required = _strings(goal.get("acceptanceCriteria"))
        by_index: dict[int, dict[str, Any]] = {}
        invalid_mapping = False
        for raw_item in value.get("criteria", ()):
            if not isinstance(raw_item, Mapping):
                invalid_mapping = True
                continue
            item = dict(raw_item)
            index_value = item.get("criterionIndex")
            if isinstance(index_value, bool) or not isinstance(index_value, int):
                invalid_mapping = True
                continue
            if index_value < 1 or index_value > len(required) or index_value in by_index:
                invalid_mapping = True
                continue
            if str(item.get("criterion") or "").strip() != required[index_value - 1]:
                invalid_mapping = True
                continue
            by_index[index_value] = item
        criteria = [by_index[index] for index in range(1, len(required) + 1) if index in by_index]
        every_criterion_passed = all(
            item.get("passed") is True
            and bool(str(item.get("evidence") or "").strip())
            for item in criteria
        )
        passed = (
            str(value.get("verdict") or "").lower() == "pass"
            and not invalid_mapping
            and len(criteria) == len(required)
            and every_criterion_passed
            and not _strings(value.get("criticalGaps"), limit=50)
        )
        return {
            "verdict": "pass" if passed else "fail",
            "criteria": criteria,
            "criticalGaps": _strings(value.get("criticalGaps"), limit=50) or (
                [] if passed else ["The reviewer did not provide sufficient evidence for a full pass."]
            ),
            "summary": str(value.get("summary") or "").strip(),
            "reviewedAt": utc_iso(),
            "goalRevision": int(goal.get("revision") or 1),
            "attempt": int(goal.get("attempt") or 0),
        }

    async def _persist_runtime_result(self, goal: Mapping[str, Any], result: Any) -> None:
        chat_id = str(goal.get("chatId") or "")
        chat = self.chat.repository.get(chat_id)
        if not chat:
            return
        base = copy.deepcopy(chat)
        additions = [copy.deepcopy(dict(item)) for item in result.activity_messages if isinstance(item, Mapping)]
        now = utc_iso()
        if result.pending_question is not None:
            pending = result.pending_question.as_dict()
            chat["pendingQuestion"] = pending
            additions.append(self.chat.pending_question_message(
                pending,
                usage=dict(result.usage or {}),
                model=str(result.model or chat.get("model") or ""),
            ))
        elif str(result.text or "").strip():
            additions.append({
                "id": self.chat.short_id("msg"),
                "role": "assistant",
                "content": str(result.text or ""),
                "createdAt": now,
                "model": str(result.model or chat.get("model") or ""),
                "processingDurationMs": int(result.generation_duration_ms or 0),
                "usage": dict(result.usage or {}),
                "goalAttempt": int(goal.get("attempt") or 0),
            })
            chat.pop("pendingQuestion", None)
            chat["completedTurnCount"] = int(chat.get("completedTurnCount") or 0) + 1
        self.chat.merge_chat_messages_chronologically(chat, additions)
        chat["status"] = "idle"
        chat["updatedAt"] = now
        self.chat.repository.write_one(chat, base_chat=base)
        await publish_chat_changed(
            chat_id,
            str(chat.get("projectId") or ""),
            "goal_progress",
            run_status="running" if self.run_manager.get(chat_id) is not None else "",
            assistantMessages=[self.chat.public_message(item) for item in additions],
            chatSummary=self.chat.public_chat_light(chat),
        )

    async def _milestone(
        self,
        goal: Mapping[str, Any],
        event_type: str,
        content: str,
    ) -> None:
        chat_id = str(goal.get("chatId") or "")
        chat = self.chat.repository.get(chat_id)
        if not chat:
            return
        message = {
            "id": self.chat.short_id("msg"),
            "role": "assistant",
            "kind": "goal_milestone",
            "content": str(content),
            "createdAt": utc_iso(),
            "goalMilestone": {
                "type": str(event_type),
                "goalId": str(goal.get("id") or ""),
                "revision": int(goal.get("revision") or 1),
                "attempt": int(goal.get("attempt") or 0),
                "status": str(goal.get("status") or ""),
            },
        }
        base = copy.deepcopy(chat)
        self.chat.merge_chat_messages_chronologically(chat, [message])
        chat["updatedAt"] = message["createdAt"]
        self.chat.repository.write_one(chat, base_chat=base)
        await publish_chat_changed(
            chat_id,
            str(chat.get("projectId") or ""),
            "goal_milestone",
            run_status="running" if self.run_manager.get(chat_id) is not None else "",
            assistantMessages=[self.chat.public_message(message)],
            chatSummary=self.chat.public_chat_light(chat),
        )

    async def _project(self, goal: Mapping[str, Any]) -> None:
        value = public_goal(goal)
        chat_id = str(goal.get("chatId") or "")
        chat = self.chat.repository.get(chat_id)
        if chat:
            base = copy.deepcopy(chat)
            if value is None:
                chat.pop("activeGoal", None)
            else:
                chat["activeGoal"] = copy.deepcopy(value)
            chat["updatedAt"] = utc_iso()
            self.chat.repository.write_one(chat, base_chat=base)
        await asyncio.to_thread(persist_goal_by_id, self.db_path, chat_id, goal)
        if chat:
            await publish_chat_changed(
                chat_id,
                str(chat.get("projectId") or ""),
                "goal_changed",
                run_status="running" if self.run_manager.get(chat_id) is not None else "",
                activeGoal=value,
                chatSummary=self.chat.public_chat_light(chat),
            )

    async def _required(self, chat_id: str) -> dict[str, Any]:
        goal = await self.repository.get(chat_id)
        if goal is None:
            raise LookupError("Goal not found")
        return goal

    def _ensure_not_stopping(self, chat_id: str) -> None:
        if str(chat_id) in self._stopping_chat_ids:
            raise ValueError("Goal is stopping")

    def _apply_definition(self, goal: dict[str, Any], body: Mapping[str, Any]) -> None:
        if "objective" in body:
            objective = str(body.get("objective") or "").strip()
            if len(objective) < 3:
                raise ValueError("Goal objective is too short")
            goal["objective"] = objective
        if "acceptanceCriteria" in body:
            criteria = _strings(body.get("acceptanceCriteria"))
            if not criteria:
                raise ValueError("At least one acceptance criterion is required")
            goal["acceptanceCriteria"] = criteria
        if "constraints" in body:
            goal["constraints"] = _strings(body.get("constraints"))
        if "outOfScope" in body:
            goal["outOfScope"] = _strings(body.get("outOfScope"))
        if "durationSeconds" in body:
            goal["durationSeconds"] = _duration(body.get("durationSeconds"))

    @staticmethod
    def _settle_active_time(goal: dict[str, Any]) -> None:
        started = str(goal.get("activeStartedAt") or "").strip()
        if started:
            try:
                elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
            except ValueError:
                elapsed = 0.0
            goal["activeSeconds"] = float(goal.get("activeSeconds") or 0.0) + elapsed
        goal["activeStartedAt"] = ""

    def _duration_exhausted(self, goal: Mapping[str, Any]) -> bool:
        active = float(goal.get("activeSeconds") or 0.0)
        started = str(goal.get("activeStartedAt") or "").strip()
        if started:
            try:
                active += max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
            except ValueError:
                pass
        return active >= int(goal.get("durationSeconds") or DEFAULT_DURATION_SECONDS)

    async def _pause_for_duration(self, goal: dict[str, Any]) -> None:
        previous_status = str(goal.get("status") or "active")
        self._settle_active_time(goal)
        goal.update({
            "status": "paused",
            "phase": "paused",
            "pausedFromStatus": previous_status if previous_status in RUNNABLE else "active",
            "stopReason": "active_duration_exhausted",
        })
        goal = await self.repository.save(goal)
        await self.repository.event(goal, "duration_exhausted")
        await self._project(goal)
        await self._milestone(goal, "duration_exhausted", localized(
            "The configured active duration was reached. Extend it in the Goal tab to continue.",
            "已达到设定的活跃执行时间，请在目标 Tab 中延长后继续。",
        ))
        await self._notify_goal_state(goal, "duration_exhausted")

    async def _stop_live_run(self, chat_id: str, reason: str) -> None:
        run = self.run_manager.get(chat_id)
        if run is not None:
            await self.run_manager.terminate(chat_id, termination_reason=reason)

    async def _settle_run(self, run: ChatRun) -> None:
        goal = await self.repository.get(run.chat_id)
        if goal is None:
            return
        if run.status == "cancelled" and str(goal.get("status") or "") in RUNNABLE:
            # Explicit Goal controls update the state before cancellation. Only
            # unexpected interruption becomes a resumable pause.
            previous_status = str(goal.get("status") or "active")
            self._settle_active_time(goal)
            goal.update({
                "status": "paused",
                "phase": "paused",
                "pausedFromStatus": previous_status,
                "stopReason": run.termination_reason or "interrupted",
            })
            goal = await self.repository.save(goal)
            await self._project(goal)
        elif run.status == "error" and str(goal.get("status") or "") in RUNNABLE:
            self._settle_active_time(goal)
            goal.update({
                "status": "blocked",
                "phase": "blocked",
                "stopReason": run.termination_reason or "controller_error",
            })
            goal = await self.repository.save(goal)
            await self.repository.event(goal, "controller_blocked", {
                "reason": goal.get("stopReason"),
            })
            await self._project(goal)
            await self._milestone(goal, "controller_blocked", localized(
                "Goal execution stopped unexpectedly. Review the state in the Goal tab and resume when ready.",
                "目标执行意外停止。请在目标 Tab 检查状态，准备好后再继续。",
            ))
            await self._notify_goal_state(goal, "blocked")

    async def _notify_goal_state(
        self,
        goal: Mapping[str, Any],
        event_type: str,
    ) -> None:
        """Publish terminal or attention-worthy Goal state to the shared inbox."""

        chat_id = str(goal.get("chatId") or "")
        chat = self.chat.repository.get(chat_id) or {}
        chat_title = str(chat.get("title") or "").strip()
        objective = str(goal.get("objective") or "").strip()
        title_by_event = {
            "completed": localized("Goal completed", "目标已完成"),
            "blocked": localized("Goal needs attention", "目标需要处理"),
            "review_unavailable": localized(
                "Goal review paused",
                "目标审查已暂停",
            ),
            "duration_exhausted": localized(
                "Goal active duration reached",
                "目标已达到活跃时长",
            ),
        }
        body_by_event = {
            "completed": localized(
                'The Goal in conversation "{title}" has completed.',
                "对话「{title}」中的目标已经完成。",
                title=chat_title or objective,
            ),
            "blocked": localized(
                'The Goal in conversation "{title}" stopped unexpectedly and needs your attention.',
                "对话「{title}」中的目标意外停止，需要你处理。",
                title=chat_title or objective,
            ),
            "review_unavailable": localized(
                'The Goal result in conversation "{title}" was preserved because independent review is temporarily unavailable.',
                "对话「{title}」中的目标候选结果已保留；独立审查暂时不可用。",
                title=chat_title or objective,
            ),
            "duration_exhausted": localized(
                'The Goal in conversation "{title}" reached its configured active duration.',
                "对话「{title}」中的目标已达到设定的活跃执行时间。",
                title=chat_title or objective,
            ),
        }
        title = title_by_event.get(event_type)
        body = body_by_event.get(event_type)
        if not title or not body:
            return
        try:
            await asyncio.to_thread(
                append_notification,
                title=title,
                body=body,
                tab="system",
                project_ref=str(chat.get("projectId") or goal.get("projectId") or ""),
                source=f"conversation_goal_{event_type}",
                source_label=localized("Goal", "目标"),
                link_label=chat_title,
                meta={
                    "chatId": chat_id,
                    "goalId": str(goal.get("id") or ""),
                    "goalRevision": int(goal.get("revision") or 1),
                    "goalStatus": str(goal.get("status") or ""),
                    "completionMode": str(goal.get("completionMode") or ""),
                },
            )
        except Exception:
            logger.exception(
                "Failed to publish Goal notification [chat=%s event=%s]",
                chat_id,
                event_type,
            )


__all__ = ["ConversationGoalService", "DEFAULT_DURATION_SECONDS"]

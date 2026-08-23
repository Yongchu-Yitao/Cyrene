"""Application orchestration for Goal Loop HTTP and control adapters."""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from cyrene.workbench.goal_loop_repository import (
    GoalLoopRepositoryPort,
    GoalLoopTransactionPort,
    json_dumps,
    json_loads,
    utc_iso,
    utc_now,
)

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"review", "completed", "cancelled"}
RESUMABLE_STATUSES = {"paused", "blocked"}
REFLECTION_MODES = {"standard", "proactive", "frequent"}
PERMISSION_MODES = {"auto", "full_access"}


class GoalLoopApplicationError(Exception):
    def __init__(self, error: str, status_code: int, *, code: str = "") -> None:
        super().__init__(error)
        self.payload = {"error": error, **({"code": code} if code else {})}
        self.status_code = status_code


class GoalLoopSessionTransactionPort(Protocol):
    def read_session(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]: ...
    def write_session(self, session_id: str, mutator: Callable[..., None]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]: ...
    async def generate_plan(self, session: dict[str, Any], project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]: ...
    async def generate_acceptance(self, session: dict[str, Any], project: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]: ...
    def event_id(self) -> str: ...
    def serialize_run(self, run: dict[str, Any] | None) -> dict[str, Any] | None: ...
    async def publish(self, run: dict[str, Any]) -> None: ...
    async def sync_projection(self, run: dict[str, Any], *, message: str = "") -> None: ...
    def interrupt(self, session_id: str) -> None: ...


class GoalLoopExecutionPort(Protocol):
    def register_run(self, run_id: str, session_id: str) -> None: ...
    def wake(self, run_id: str) -> bool: ...
    def interrupt(self, session_id: str, *, reason: str) -> bool: ...


@dataclass(frozen=True)
class GoalLoopPreviewCommand:
    session_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class GoalLoopStartCommand:
    session_id: str
    draft_id: str


@dataclass(frozen=True)
class GoalLoopLimitsCommand:
    session_id: str
    payload: dict[str, Any]


class GoalLoopApplicationService:
    def __init__(
        self,
        repository: GoalLoopRepositoryPort,
        reservations: GoalLoopTransactionPort,
        sessions: GoalLoopSessionTransactionPort,
        manager: GoalLoopExecutionPort,
    ) -> None:
        self.repository = repository
        self.reservations = reservations
        self.sessions = sessions
        self.manager = manager
        self._start_lock = asyncio.Lock()

    async def preview(self, command: GoalLoopPreviewCommand) -> dict[str, Any]:
        limits = _validate_limits(command.payload)
        _payload, project, session = self._read_session(command.session_id)
        _require_planning_session(session)
        base_revision = _base_revision(command.payload, session)
        current = await self._prepare_preview_storage(command.session_id)
        if _is_active(current):
            raise GoalLoopApplicationError("该任务已有持续执行实例。", 409, code="goal_loop_exists")
        goal = str(limits["goal"])
        goal_changed = goal.strip() != str(session.get("goal") or "").strip()
        plan, acceptance, generated = await self._preview_plan(project, session, goal, goal_changed)
        draft = _draft_record(command.session_id, project, base_revision, goal, goal_changed, plan, acceptance, limits)
        try:
            await self.repository.save_draft(draft)
        except Exception as exc:
            if not self.repository.is_busy(exc):
                raise
            logger.warning("Goal-loop preview could not persist draft for session %s", command.session_id)
            raise _storage_busy_error() from exc
        return _draft_response(draft, plan, acceptance, limits, generated)

    async def start(self, command: GoalLoopStartCommand) -> dict[str, Any]:
        async with self._start_lock:
            return await self._start(command)

    async def _start(self, command: GoalLoopStartCommand) -> dict[str, Any]:
        draft = await self.repository.get_draft(command.draft_id, command.session_id)
        if not draft:
            await self._raise_missing_draft(command.session_id)
        assert draft is not None
        _require_valid_draft(draft)
        _payload, project, session = self._read_session(command.session_id)
        base_revision = int(draft.get("base_plan_revision") or 0)
        if base_revision != int(session.get("planDefinitionRevision") or 0):
            raise GoalLoopApplicationError("计划已发生变化，请重新生成目标配置。", 409, code="stale_plan_revision")
        existing = await self.repository.get_run_by_session(command.session_id)
        if _is_active(existing):
            raise GoalLoopApplicationError("该任务已有持续执行实例。", 409, code="goal_loop_exists")
        if existing:
            await self.repository.delete_run(str(existing["id"]))
        run_seed = _run_reservation(command.session_id, project, draft, base_revision)
        await self.reservations.reserve_run(run_seed)
        run = await self.repository.get_run_by_id(str(run_seed["id"]))
        try:
            response = self._project_started_run(command.session_id, draft, run)
        except Exception:
            try:
                await self.reservations.rollback_run(str(run_seed["id"]))
            except Exception:
                logger.exception("Failed to roll back unprojected goal-loop run %s", run_seed["id"])
            raise
        await self.repository.delete_draft(command.draft_id)
        await self.repository.add_event(str(run_seed["id"]), "started", payload={"limits": json_loads(draft.get("limits_json"), {})})
        if run:
            await self.sessions.publish(run)
        await self._wake_or_conflict(run, command.session_id, "持续执行未启动并已安全暂停。")
        return response

    async def get(self, session_id: str) -> dict[str, Any]:
        run = await self.repository.get_run_by_session(session_id)
        if not run:
            return {"ok": True, "goalLoop": None}
        events = await self.repository.list_events(str(run["id"]))
        return {"ok": True, "goalLoop": self.sessions.serialize_run(run), "events": [
            {"id": item["id"], "type": item["event_type"], "stepId": item.get("step_id") or "",
             "payload": json_loads(item.get("payload_json"), {}), "createdAt": item.get("created_at") or ""}
            for item in reversed(events)
        ]}

    async def pause(self, session_id: str) -> dict[str, Any]:
        run = await self.repository.get_run_by_session(session_id)
        if not run or str(run.get("status") or "") != "running":
            raise GoalLoopApplicationError("没有正在运行的持续任务。", 409)
        self.manager.interrupt(session_id, reason="user_paused")
        self.sessions.interrupt(session_id)
        paused = await self.repository.set_inactive(run, "paused", phase="paused", stop_reason="user_paused")
        if paused:
            await self.repository.add_event(str(run["id"]), "paused")
            await self.sessions.sync_projection(paused, message="持续执行已暂停，当前进度已保留。")
        return self._session_response(session_id, paused)

    async def resume(self, session_id: str) -> dict[str, Any]:
        run = await self.repository.get_run_by_session(session_id)
        if not run or str(run.get("status") or "") not in RESUMABLE_STATUSES:
            raise GoalLoopApplicationError("当前持续任务不能恢复。", 409)
        resumed = await self.repository.update_run(str(run["id"]), status="running", phase="executing",
            active_started_at=utc_iso(), stop_reason=None, last_error=None, lease_owner=None, lease_until=None)
        response = self._project_resumed_run(session_id, resumed)
        if resumed:
            await self.repository.add_event(str(run["id"]), "resumed")
            await self.sessions.publish(resumed)
            await self._wake_or_conflict(resumed, session_id, "持续执行未恢复并已安全暂停。")
        return response

    async def cancel(self, session_id: str) -> dict[str, Any]:
        run = await self.repository.get_run_by_session(session_id)
        if not run or str(run.get("status") or "") in TERMINAL_STATUSES | {"cancelled"}:
            raise GoalLoopApplicationError("没有可取消的持续任务。", 409)
        self.manager.interrupt(session_id, reason="user_cancelled")
        self.sessions.interrupt(session_id)
        cancelled = await self.repository.set_inactive(run, "cancelled", phase="cancelled", stop_reason="user_cancelled")
        if cancelled:
            await self.repository.add_event(str(run["id"]), "cancelled")
            await self.sessions.sync_projection(cancelled, message="持续执行已取消，当前进度和文件改动已保留。")
        return self._session_response(session_id, cancelled)

    async def update_limits(self, command: GoalLoopLimitsCommand) -> dict[str, Any]:
        run = await self.repository.get_run_by_session(command.session_id)
        if not run:
            raise GoalLoopApplicationError("持续任务不存在。", 404)
        max_hours, max_repairs, reflection_mode = _updated_limits(command.payload, run)
        updated = await self.repository.update_run(str(run["id"]), max_active_seconds=int(max_hours * 3600),
            max_repair_rounds=max_repairs, reflection_mode=reflection_mode)
        if updated:
            await self.sessions.sync_projection(updated, message="持续执行限制已更新。")
        return self._session_response(command.session_id, updated)

    async def _prepare_preview_storage(self, session_id: str) -> dict[str, Any] | None:
        try:
            current = await self.repository.get_run_by_session(session_id)
            await self.repository.delete_expired_drafts(utc_iso())
        except Exception as exc:
            if not self.repository.is_busy(exc):
                raise
            logger.warning("Goal-loop preview storage is busy for session %s", session_id)
            raise _storage_busy_error() from exc
        return current

    async def _preview_plan(self, project: dict[str, Any], session: dict[str, Any], goal: str, changed: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        draft_session = copy.deepcopy(session)
        draft_session["goal"] = goal
        if changed:
            draft_session["plan"] = []
            plan, acceptance, generated = await self.sessions.generate_plan(draft_session, project)
        else:
            plan = copy.deepcopy(session.get("plan") or [])
            draft_session["plan"] = plan
            acceptance = _existing_acceptance(session)
            if acceptance:
                generated = False
            else:
                acceptance, generated = await self.sessions.generate_acceptance(draft_session, project)
        if not plan:
            raise GoalLoopApplicationError("无法生成可执行计划。", 503)
        if not acceptance:
            raise GoalLoopApplicationError("无法生成验收条件。", 503)
        return plan, acceptance, generated

    async def _raise_missing_draft(self, session_id: str) -> None:
        if _is_active(await self.repository.get_run_by_session(session_id)):
            raise GoalLoopApplicationError("该任务已有持续执行实例。", 409, code="goal_loop_exists")
        raise GoalLoopApplicationError("目标配置草稿不存在或已过期。", 404, code="draft_not_found")

    def _read_session(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            return self.sessions.read_session(session_id)
        except KeyError as exc:
            raise GoalLoopApplicationError("session not found", 404) from exc

    def _project_started_run(self, session_id: str, draft: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
        plan = json_loads(draft.get("plan_json"), [])
        acceptance = json_loads(draft.get("acceptance_json"), [])
        revision = int(draft.get("base_plan_revision") or 0) + (1 if bool(draft.get("goal_changed")) else 0)
        now = str(run.get("created_at") if run else utc_iso())
        def apply(_payload: dict[str, Any], _project: dict[str, Any], session: dict[str, Any]) -> None:
            session.update(goal=str(draft.get("goal") or ""), plan=plan, acceptanceCriteria=acceptance,
                status="running", planRevision=int(session.get("planRevision") or 0) + 1,
                planDefinitionRevision=revision, approvedPlanDefinitionRevision=revision,
                goalLoop=self.sessions.serialize_run(run),
                agentReply="持续执行已启动，Agent 将执行计划并循环返工直到验收通过或达到退出条件。")
            session.setdefault("events", []).append({"id": self.sessions.event_id(), "type": "GoalLoopStarted",
                "createdAt": now, "body": "用户确认启动持续执行到验收通过。"})
        payload, project, session = self.sessions.write_session(session_id, apply)
        return {"ok": True, "project": project, "session": session, "goalLoop": self.sessions.serialize_run(run), **payload}

    def _project_resumed_run(self, session_id: str, run: dict[str, Any] | None) -> dict[str, Any]:
        def apply(_payload: dict[str, Any], _project: dict[str, Any], session: dict[str, Any]) -> None:
            for step in session.get("plan") or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("status") or "") == "running":
                    step["status"], step["startedAt"] = "pending", None
                if str(step.get("status") or "") not in {"completed", "done", "skipped"}:
                    step["goalLoopAttempts"] = 0
            session.update(status="running", goalLoop=self.sessions.serialize_run(run), agentReply="持续执行已恢复。")
        payload, project, session = self.sessions.write_session(session_id, apply)
        return {"ok": True, "project": project, "session": session, "goalLoop": self.sessions.serialize_run(run), **payload}

    async def _wake_or_conflict(self, run: dict[str, Any] | None, session_id: str, message: str) -> None:
        if not run:
            return
        run_id = str(run["id"])
        self.manager.register_run(run_id, session_id)
        if self.manager.wake(run_id) is not False:
            return
        paused = await self.repository.set_inactive(run, "paused", phase="paused", stop_reason="run_conflict")
        if paused:
            await self.sessions.sync_projection(paused, message=f"任务已有其他运行，{message}")
        self._read_session(session_id)
        raise GoalLoopApplicationError("该任务已有正在执行的请求，请等待完成或先停止它。", 409, code="task_run_in_progress")

    def _session_response(self, session_id: str, run: dict[str, Any] | None) -> dict[str, Any]:
        payload, project, session = self._read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": self.sessions.serialize_run(run), **payload}


def _validate_limits(payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal") or "").strip()
    if len(goal) < 3:
        raise GoalLoopApplicationError("目标至少需要 3 个字符。", 400)
    try:
        max_hours = float(payload.get("maxRuntimeHours"))
    except (TypeError, ValueError) as exc:
        raise GoalLoopApplicationError("最大运行时间必须是数字。", 400) from exc
    if max_hours < 0.5 or max_hours > 24:
        raise GoalLoopApplicationError("最大运行时间必须在 0.5 到 24 小时之间。", 400)
    try:
        max_repairs = int(payload.get("maxRepairRounds"))
    except (TypeError, ValueError) as exc:
        raise GoalLoopApplicationError("最大返工轮数必须是整数。", 400) from exc
    if max_repairs < 0 or max_repairs > 10:
        raise GoalLoopApplicationError("最大返工轮数必须在 0 到 10 之间。", 400)
    permission = str(payload.get("permissionMode") or "auto").strip()
    if permission not in PERMISSION_MODES:
        raise GoalLoopApplicationError("权限模式无效。", 400)
    if permission == "full_access" and not bool(payload.get("fullAccessConfirmed")):
        raise GoalLoopApplicationError("使用完全访问前必须确认风险。", 400)
    reflection = str(payload.get("reflectionMode") or "proactive").strip()
    if reflection not in REFLECTION_MODES:
        raise GoalLoopApplicationError("深度思考强度无效。", 400)
    return {"goal": goal, "maxRuntimeHours": max_hours, "maxActiveSeconds": int(max_hours * 3600),
        "maxRepairRounds": max_repairs, "permissionMode": permission, "reflectionMode": reflection}


def _base_revision(payload: dict[str, Any], session: dict[str, Any]) -> int:
    try:
        revision = int(payload.get("basePlanDefinitionRevision"))
    except (TypeError, ValueError) as exc:
        raise GoalLoopApplicationError("invalid basePlanDefinitionRevision", 400) from exc
    if revision != int(session.get("planDefinitionRevision") or 0):
        raise GoalLoopApplicationError("计划已发生变化，请重新打开配置。", 409, code="stale_plan_revision")
    return revision


def _require_planning_session(session: dict[str, Any]) -> None:
    if str(session.get("status") or "") != "planning":
        raise GoalLoopApplicationError("只有计划确认阶段可以启动持续执行。", 409, code="invalid_status")


def _require_valid_draft(draft: dict[str, Any]) -> None:
    try:
        if datetime.fromisoformat(str(draft["expires_at"])) <= utc_now():
            raise GoalLoopApplicationError("目标配置草稿已过期，请重新生成。", 409, code="draft_expired")
    except ValueError as exc:
        raise GoalLoopApplicationError("目标配置草稿无效。", 409) from exc


def _is_active(run: dict[str, Any] | None) -> bool:
    return bool(run and str(run.get("status") or "") not in TERMINAL_STATUSES | {"cancelled"})


def _existing_acceptance(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**copy.deepcopy(item), "status": "pending"} for item in (session.get("acceptanceCriteria") or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()]


def _draft_record(session_id: str, project: dict[str, Any], revision: int, goal: str, changed: bool,
    plan: list[dict[str, Any]], acceptance: list[dict[str, Any]], limits: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    expires = now + timedelta(minutes=30)
    return {"id": f"goal_draft_{uuid.uuid4().hex[:16]}", "session_id": session_id,
        "project_id": str(project.get("id") or ""), "base_plan_revision": revision, "goal": goal,
        "goal_changed": 1 if changed else 0, "plan_json": json_dumps(plan),
        "acceptance_json": json_dumps(acceptance), "limits_json": json_dumps(limits),
        "created_at": now.isoformat(), "expires_at": expires.isoformat()}


def _draft_response(draft: dict[str, Any], plan: list[dict[str, Any]], acceptance: list[dict[str, Any]],
    limits: dict[str, Any], generated: bool) -> dict[str, Any]:
    return {"ok": True, "draftId": draft["id"], "goalChanged": bool(draft["goal_changed"]),
        "goal": draft["goal"], "plan": plan, "acceptanceCriteria": acceptance, "limits": limits,
        "planSource": "llm" if generated else "fallback", "expiresAt": draft["expires_at"]}


def _run_reservation(session_id: str, project: dict[str, Any], draft: dict[str, Any], revision: int) -> dict[str, Any]:
    limits, now = json_loads(draft.get("limits_json"), {}), utc_iso()
    return {"id": f"goal_run_{uuid.uuid4().hex[:16]}", "session_id": session_id,
        "project_id": str(project.get("id") or ""), "objective": str(draft.get("goal") or ""),
        "plan_definition_revision": revision + (1 if bool(draft.get("goal_changed")) else 0),
        "permission_mode": str(limits.get("permissionMode") or "auto"),
        "reflection_mode": str(limits.get("reflectionMode") or "proactive"),
        "max_active_seconds": int(limits.get("maxActiveSeconds") or 7200),
        "max_repair_rounds": int(limits.get("maxRepairRounds") or 3),
        "active_started_at": now, "created_at": now, "updated_at": now}


def _updated_limits(payload: dict[str, Any], run: dict[str, Any]) -> tuple[float, int, str]:
    try:
        hours = float(payload.get("maxRuntimeHours", int(run["max_active_seconds"]) / 3600))
        repairs = int(payload.get("maxRepairRounds", run["max_repair_rounds"]))
    except (TypeError, ValueError) as exc:
        raise GoalLoopApplicationError("退出条件格式无效。", 400) from exc
    if hours < 0.5 or hours > 24 or repairs < 0 or repairs > 10:
        raise GoalLoopApplicationError("退出条件超出允许范围。", 400)
    reflection = str(payload.get("reflectionMode") or run.get("reflection_mode") or "proactive")
    if reflection not in REFLECTION_MODES:
        raise GoalLoopApplicationError("深度思考强度无效。", 400)
    return hours, repairs, reflection


def _storage_busy_error() -> GoalLoopApplicationError:
    return GoalLoopApplicationError("任务存储正被其他操作占用，请等待相关测试或任务结束后重试。", 503,
        code="goal_loop_storage_busy")


__all__ = ["GoalLoopApplicationError", "GoalLoopApplicationService", "GoalLoopLimitsCommand",
    "GoalLoopPreviewCommand", "GoalLoopStartCommand"]

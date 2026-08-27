"""Durable, server-side goal loop for Workbench task sessions.

The normal agent loop is intentionally bounded to one request.  This module
adds a harness-owned loop around those bounded slices:

    execute one plan step -> verify it -> continue -> verify the whole goal
    -> reflect/repair on failure -> repeat within user-configured limits.

SQLite is the source of truth for both loop execution and the Workbench UI
projection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from agent.plugin import active_plugin_service
from agent.workbench.task_runtime import (
    TaskAgentResult,
    TaskAgentRuntime,
    TaskAgentRuntimeError,
)
from cyrene.observability import debug
from cyrene.runtime.run_coordinator import RunLease, run_coordinator_for
from cyrene.workbench import (
    artifact_runtime,
    planning_runtime,
    project_repository,
    project_runtime,
)
from cyrene.workbench.goal_loop_repository import (
    ensure_schema,
    execute,
    fetch_all,
    fetch_one,
    json_dumps,
    json_loads,
    utc_iso,
    utc_now,
)
from cyrene.localization import app_language, localized
from cyrene.workbench.notifications import append_notification

logger = logging.getLogger(__name__)

# A single step that fails independent verification this many times in a row is
# treated as stuck: the loop reflects once, then blocks instead of retrying the
# same step until the runtime budget is burned.
_STEP_FAILURE_CAP = 3
_MANAGERS: dict[str, "GoalLoopManager"] = {}


def _utc_now() -> datetime:
    return utc_now()


def _utc_iso() -> str:
    return utc_iso()


def _json_dumps(value: Any) -> str:
    return json_dumps(value)


def _json_loads(value: Any, fallback: Any) -> Any:
    return json_loads(value, fallback)


def _l(en: str, zh: str, **values: Any) -> str:
    """Localize one goal-loop message using the effective app language."""

    return localized(en, zh, language=app_language(), **values)


async def _ensure_schema(db_path: str) -> None:
    project_repository._configure_workbench_store(str(db_path))
    await ensure_schema(db_path)


async def _fetch_one(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    return await fetch_one(db_path, sql, args)


async def _fetch_all(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return await fetch_all(db_path, sql, args)


async def _execute(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> int:
    return await execute(db_path, sql, args)


def _public_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    active_seconds = float(run.get("active_seconds") or 0)
    active_started_at = str(run.get("active_started_at") or "").strip()
    if str(run.get("status") or "") == "running" and active_started_at:
        try:
            active_seconds += max(0.0, (_utc_now() - datetime.fromisoformat(active_started_at)).total_seconds())
        except ValueError:
            pass
    return {
        "runId": str(run.get("id") or ""),
        "sessionId": str(run.get("session_id") or ""),
        "status": str(run.get("status") or ""),
        "phase": str(run.get("phase") or ""),
        "currentStepId": str(run.get("current_step_id") or ""),
        "permissionMode": str(run.get("permission_mode") or "auto"),
        "reflectionMode": str(run.get("reflection_mode") or "proactive"),
        "maxActiveSeconds": int(run.get("max_active_seconds") or 0),
        "activeSeconds": int(active_seconds),
        "repairRound": int(run.get("repair_round") or 0),
        "maxRepairRounds": int(run.get("max_repair_rounds") or 0),
        "stopReason": str(run.get("stop_reason") or ""),
        "lastError": str(run.get("last_error") or ""),
        "updatedAt": str(run.get("updated_at") or ""),
    }


async def _event(
    db_path: str,
    run_id: str,
    event_type: str,
    *,
    step_id: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    await _execute(
        db_path,
        "INSERT INTO goal_run_events (run_id, event_type, step_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, event_type, step_id or None, _json_dumps(payload or {}), _utc_iso()),
    )


def _read_session(session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(payload, session_id)
    if not project or not session:
        raise KeyError("session not found")
    return payload, project, session


def _write_session(
    session_id: str,
    mutator: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    with project_repository._WORKBENCH_STORE_LOCK:
        payload = project_repository._read_workbench_store()
        project, session = project_repository._workbench_find_session(payload, session_id)
        if not project or not session:
            raise KeyError("session not found")
        mutator(payload, project, session)
        now = project_runtime._utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        # Goal-loop runs in the background and must not steal the user's
        # persisted project selection. Only explicit UI activation is allowed
        # to change activeProjectId / activeSessionId.
        project_repository._write_workbench_store(payload)
        return payload, project, session


async def _publish(run: dict[str, Any], *, event_type: str = "goal_loop_update") -> None:
    public = _public_run(run) or {}
    await debug.publish_event(
        {
            "type": event_type,
            "session_id": str(run.get("session_id") or ""),
            "goal_loop": public,
        }
    )


async def sync_goal_loop_projection(run: dict[str, Any], *, message: str = "") -> None:
    public = _public_run(run) or {}

    def apply(_payload: dict[str, Any], _project: dict[str, Any], session: dict[str, Any]) -> None:
        session["goalLoop"] = public
        if message:
            session["agentReply"] = message
        status = str(run.get("status") or "")
        if status in {"completed", "cancelled"}:
            session.pop("pendingQuestion", None)
            session.pop("pendingPlanStep", None)
            for step in session.get("plan") or []:
                if not isinstance(step, dict):
                    continue
                step.pop("goalLoopResumeAnswer", None)
                if status == "cancelled" and str(step.get("status") or "") == "running":
                    step["status"] = "pending"
                    step["startedAt"] = None
                    step.pop("goalLoopAgentRunId", None)
        if status in {"running", "waiting_for_user", "paused", "blocked", "review", "completed", "cancelled"}:
            session["status"] = status

    try:
        _write_session(str(run["session_id"]), apply)
    except KeyError:
        return
    await _publish(run)


class WorkbenchGoalLoopTransaction:
    """Public Workbench document/planning port used by the application service."""

    def __init__(self, agent_runtime: TaskAgentRuntime) -> None:
        self.agent_runtime = agent_runtime

    def read_session(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return _read_session(session_id)

    def write_session(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return _write_session(session_id, mutator)

    async def generate_plan(
        self, session: dict[str, Any], project: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        plan, acceptance, generated, _operation = await self.agent_runtime.generate_plan(
            session,
            project,
            feedback=_l(
                "The user updated the goal in continuous-execution settings. Regenerate the complete plan for the new goal.",
                "目标已由用户在持续执行配置中更新，请基于新目标重新生成完整计划。",
            ),
            requested_operation="replace",
        )
        return plan, acceptance, generated

    async def generate_acceptance(
        self, session: dict[str, Any], project: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        return await self.agent_runtime.generate_acceptance_criteria(session, project)

    @staticmethod
    def event_id() -> str:
        return f"event_{uuid.uuid4().hex[:10]}"

    @staticmethod
    def serialize_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
        return _public_run(run)

    @staticmethod
    async def publish(run: dict[str, Any]) -> None:
        await _publish(run)

    @staticmethod
    async def sync_projection(run: dict[str, Any], *, message: str = "") -> None:
        await sync_goal_loop_projection(run, message=message)

def register_goal_loop_manager(db_path: str, manager: "GoalLoopManager") -> None:
    _MANAGERS[str(db_path)] = manager


async def _get_run_by_id(db_path: str, run_id: str) -> dict[str, Any] | None:
    return await _fetch_one(db_path, "SELECT * FROM goal_runs WHERE id = ?", (run_id,))


async def _get_run_by_session(db_path: str, session_id: str) -> dict[str, Any] | None:
    return await _fetch_one(db_path, "SELECT * FROM goal_runs WHERE session_id = ?", (session_id,))


async def _update_run(db_path: str, run_id: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return await _get_run_by_id(db_path, run_id)
    fields["updated_at"] = _utc_iso()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    await _execute(
        db_path,
        f"UPDATE goal_runs SET {assignments} WHERE id = ?",
        (*fields.values(), run_id),
    )
    return await _get_run_by_id(db_path, run_id)


async def _set_inactive_status(
    db_path: str,
    run: dict[str, Any],
    status: str,
    *,
    phase: str | None = None,
    stop_reason: str = "",
    last_error: str = "",
) -> dict[str, Any] | None:
    active_seconds = float(run.get("active_seconds") or 0)
    active_started_at = str(run.get("active_started_at") or "").strip()
    if active_started_at:
        try:
            active_seconds += max(0.0, (_utc_now() - datetime.fromisoformat(active_started_at)).total_seconds())
        except ValueError:
            pass
    fields: dict[str, Any] = {
        "status": status,
        "active_seconds": active_seconds,
        "active_started_at": None,
        "lease_owner": None,
        "lease_until": None,
        "stop_reason": stop_reason or None,
        "last_error": last_error or None,
    }
    if phase is not None:
        fields["phase"] = phase
    return await _update_run(db_path, str(run["id"]), **fields)


def _dependencies_satisfied(plan: list[dict[str, Any]], step: dict[str, Any]) -> bool:
    statuses = {
        str(item.get("id") or ""): str(item.get("status") or "pending")
        for item in plan
        if isinstance(item, dict)
    }
    # Skipping a prerequisite deliberately blocks its dependants in ordinary
    # execution. Goal-loop must preserve the same meaning instead of silently
    # treating a skipped prerequisite as successful work.
    return all(statuses.get(str(dep)) in {"completed", "done"} for dep in (step.get("dependsOn") or []))


def _next_step(plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    for step in plan:
        if not isinstance(step, dict):
            continue
        if str(step.get("status") or "pending") != "pending":
            continue
        if _dependencies_satisfied(plan, step):
            return step
    return None


def _recoverable_step(plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return an interrupted step whose durable Agent run can be rebound."""

    for step in plan:
        if (
            isinstance(step, dict)
            and str(step.get("status") or "") == "running"
            and str(step.get("goalLoopAgentRunId") or "").strip()
        ):
            return step
    # A narrowly-timed crash can persist ``running`` before the Agent id write.
    # It remains runnable, but necessarily starts one new durable Agent run
    # because no original identity exists to recover.
    for step in plan:
        if isinstance(step, dict) and str(step.get("status") or "") == "running":
            return step
    return None


def _step_prompt(session: dict[str, Any], step: dict[str, Any]) -> str:
    lines = [
        _l(
            "You are completing one bounded work slice in continuous-execution mode.",
            "你正在持续执行模式中完成一个有界工作片段。",
        ),
        _l(
            "Overall goal: {goal}",
            "总目标：{goal}",
            goal=str(session.get('goal') or session.get('title') or '').strip(),
        ),
        _l(
            "Current step: {step}",
            "当前步骤：{step}",
            step=str(step.get('title') or '').strip(),
        ),
    ]
    if step.get("description"):
        lines.append(_l(
            "Step description: {description}",
            "步骤说明：{description}",
            description=str(step.get("description") or "").strip(),
        ))
    if step.get("promptOverride"):
        lines.append(_l(
            "User-specified instruction for this step: {instruction}",
            "用户为本步骤指定的执行命令：{instruction}",
            instruction=str(step.get("promptOverride") or "").strip(),
        ))
    if step.get("currentAction"):
        lines.append(_l(
            "Previous result or verification feedback: {feedback}",
            "上一次结果或验证反馈：{feedback}",
            feedback=str(step.get("currentAction") or "").strip(),
        ))
    lines.extend(
        [
            _l(
                "Use the available tools to complete this step and verify the key result.",
                "请直接使用工具完成本步骤并验证关键结果。",
            ),
            _l(
                "Returning final text ends only this work slice; it does not mean the overall goal is complete.",
                "输出最终文本只会结束当前工作片段，不代表整个目标完成。",
            ),
            _l(
                "If user input or permission is required, use ask_user.",
                "如果必须获得用户输入或权限，请使用 ask_user。",
            ),
        ]
    )
    return "\n".join(lines)


def _project_tool_file_changes(
    events: list[dict[str, Any]],
    workspace_root: Any,
) -> list[dict[str, Any]]:
    """Attach artifact deltas to unwrapped ContextTree Plugin events."""

    projected: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        result = event.get("result")
        result_text = result if isinstance(result, str) else _json_dumps(result)
        event["fileChanges"] = (
            artifact_runtime._workbench_file_changes_from_tool_event(
                {
                    "tool": event.get("tool"),
                    "args": event.get("args") or event.get("arguments") or {},
                    "result": result_text,
                },
                workspace_root,
            )
        )
        projected.append(event)
    return projected


async def _verify_step(
    agent_runtime: TaskAgentRuntime,
    session: dict[str, Any],
    project: dict[str, Any],
    step: dict[str, Any],
    agent_reply: str,
) -> dict[str, Any]:
    prompt = _l(
        "You are an independent step-verification agent. Judge only from the step definition, real workspace artifacts, and any necessary read-only checks whether the step produced enough results to continue. Do not pass it merely because the execution agent claims completion.\n\n"
        "Overall goal: {goal}\nStep: {step}\nStep description: {description}\nExecution result summary: {reply}\n\n"
        'Return JSON only: {{"passed": true/false, "evidence": "brief basis", "retry_guidance": "how to fix the next attempt if it failed"}}.',
        "你是独立步骤验收 Agent。请只根据步骤定义、工作区真实产物和必要的只读检查，判断该步骤是否已经产生足够结果，可以进入下一步骤。不要因为执行 Agent 自称完成就通过。\n\n"
        "总目标：{goal}\n步骤：{step}\n步骤说明：{description}\n执行结果摘要：{reply}\n\n"
        '只返回 JSON：{{"passed": true/false, "evidence": "简短依据", "retry_guidance": "未通过时下一次应如何修复"}}。',
        goal=session.get('goal') or session.get('title') or '',
        step=step.get('title') or '',
        description=step.get('description') or '',
        reply=agent_reply[:2000],
    )
    try:
        parsed = await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            prompt=prompt,
            purpose="goal_loop_step_verification",
        )
    except Exception as exc:
        logger.warning("Goal-loop step verification unavailable", exc_info=True)
        raise RuntimeError(_l(
            "Independent step verification is temporarily unavailable.",
            "步骤独立验收暂时不可用。",
        )) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("passed"), bool):
        raise RuntimeError(_l(
            "Independent step verification returned no valid result.",
            "步骤独立验收没有返回有效结果。",
        ))
    return {
        "passed": bool(parsed["passed"]),
        "evidence": str(parsed.get("evidence") or "").strip(),
        "retry_guidance": str(parsed.get("retry_guidance") or "").strip(),
    }


async def _reflect(
    agent_runtime: TaskAgentRuntime,
    session_id: str,
    *,
    focus: str,
    goal_gap: str,
    trigger: str,
) -> dict[str, Any] | None:
    _payload, project, session = _read_session(session_id)
    packet = await agent_runtime.reflect_task(
        session,
        project,
        focus=focus,
        goal_gap=goal_gap,
    )
    if not packet:
        return None

    def apply(_payload: dict[str, Any], project: dict[str, Any], session: dict[str, Any]) -> None:
        planning_runtime._workbench_store_reflection(
            session,
            packet,
            trigger=trigger,
            project=project,
        )

    _write_session(session_id, apply)
    return packet


async def _generate_repair_steps(
    agent_runtime: TaskAgentRuntime,
    session: dict[str, Any],
    project: dict[str, Any],
    verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    failed = [
        item
        for item in (verdict.get("results") or [])
        if isinstance(item, dict) and not bool(item.get("passed"))
    ]
    reflection = session.get("reflection") if isinstance(session.get("reflection"), dict) else {}
    packet = reflection.get("packet") if isinstance(reflection.get("packet"), dict) else {}
    prompt = _l(
        "You are the repair-planning agent for a continuous task. The current plan finished, but independent acceptance failed. Inspect the workspace and generate only the new steps required to fix the failed criteria; do not repeat completed unrelated work.\n\n"
        "Goal: {goal}\nFailed acceptance criteria: {failed}\nDeep reflection: {reflection}\n\n"
        'Return JSON only: {{"steps":[{{"title":"repair step","description":"specific changes and verification"}}]}}. '
        "Generate 1-5 executable steps, each with a verification method.",
        "你是持续任务的返工规划 Agent。当前计划已经执行完，但独立验收未通过。请检查工作区，并只生成修复这些失败项所需的新增步骤，不要重复已经完成且无关的步骤。\n\n"
        "目标：{goal}\n失败验收项：{failed}\n深度反思：{reflection}\n\n"
        '只返回 JSON：{{"steps":[{{"title":"修复步骤","description":"具体修改和验证方式"}}]}}。'
        "生成 1-5 个步骤，每步必须可执行并包含验证方式。",
        goal=session.get('goal') or '',
        failed=_json_dumps(failed),
        reflection=_json_dumps(packet),
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed = await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            prompt=prompt,
            purpose="goal_loop_repair_planning",
        )
    except Exception:
        logger.warning("Goal-loop repair planning unavailable", exc_info=True)
    raw_steps = parsed.get("steps") if isinstance(parsed, dict) else None
    steps: list[dict[str, Any]] = []
    if isinstance(raw_steps, list):
        for raw in raw_steps[:5]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            steps.append(
                planning_runtime._workbench_new_plan_step(
                    title[:160],
                    str(raw.get("description") or "").strip()[:4000],
                    0,
                    str(session.get("id") or ""),
                )
            )
    if not steps:
        failed_text = (
            "；" if app_language() == "zh" else "; "
        ).join(str(item.get("evidence") or item.get("id") or "") for item in failed)
        steps = [
            planning_runtime._workbench_new_plan_step(
                _l(
                    "Fix failed acceptance criteria",
                    "修复未通过的验收项",
                ),
                _l(
                    "Fix the issues from independent acceptance evidence and verify again.{evidence}",
                    "根据独立验收证据修复问题并重新验证。{evidence}",
                    evidence=(
                        _l(" Evidence: {text}", " 证据：{text}", text=failed_text)
                        if failed_text else ""
                    ),
                )[:4000],
                0,
                str(session.get("id") or ""),
            )
        ]
    return steps


class GoalLoopManager:
    def __init__(self, db_path: str, agent_runtime: TaskAgentRuntime) -> None:
        self.db_path = str(db_path)
        self.agent_runtime = agent_runtime
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.coordinator = run_coordinator_for(self.db_path)
        self.run_leases: dict[str, RunLease] = {}
        self.run_sessions: dict[str, str] = {}
        self.closed = False

    async def startup(self) -> None:
        await _ensure_schema(self.db_path)
        rows = await _fetch_all(
            self.db_path,
            "SELECT * FROM goal_runs WHERE status = 'running' "
            "OR (status = 'paused' AND stop_reason = 'server_shutdown')",
        )
        for row in rows:
            recovered = await _update_run(
                self.db_path,
                str(row["id"]),
                status="running",
                phase="recovering",
                stop_reason=None,
                active_started_at=_utc_iso(),
                lease_owner=None,
                lease_until=None,
            )
            if recovered:
                def apply(
                    _payload: dict[str, Any],
                    _project: dict[str, Any],
                    session: dict[str, Any],
                ) -> None:
                    session["status"] = "running"
                    session["goalLoop"] = _public_run(recovered)
                    session["agentReply"] = _l(
                        "The previous execution was interrupted. Restoring it from ContextTree with the original Agent run ID.",
                        "检测到上次执行被中断，正在用原 Agent run ID 从 ContextTree 恢复。",
                    )

                try:
                    _write_session(str(row["session_id"]), apply)
                except KeyError:
                    logger.warning(
                        "Crash recovery: session projection not written (session=%s, run=%s)",
                        row["session_id"], row["id"], exc_info=True,
                    )
                await _publish(recovered)
            self.register_run(str(row["id"]), str(row["session_id"]))
            if self.wake(str(row["id"])) is False:
                paused = await _set_inactive_status(
                    self.db_path,
                    recovered or row,
                    "paused",
                    phase="paused",
                    stop_reason="run_conflict",
                )
                if paused:
                    await self.sync_projection(
                        paused,
                        message=_l(
                            "Another run was already active while restoring continuous execution, so this run was safely paused.",
                            "恢复持续执行时发现该任务已有其他运行，已安全暂停。",
                        ),
                    )

    async def shutdown(self) -> None:
        self.closed = True
        rows = await _fetch_all(self.db_path, "SELECT * FROM goal_runs WHERE status = 'running'")
        for row in rows:
            active_seconds = float(row.get("active_seconds") or 0)
            active_started_at = str(row.get("active_started_at") or "").strip()
            if active_started_at:
                try:
                    active_seconds += max(
                        0.0,
                        (_utc_now() - datetime.fromisoformat(active_started_at)).total_seconds(),
                    )
                except ValueError:
                    pass
            await _update_run(
                self.db_path,
                str(row["id"]),
                status="paused",
                phase="paused",
                active_seconds=active_seconds,
                active_started_at=None,
                stop_reason="server_shutdown",
                lease_owner=None,
                lease_until=None,
            )
        tasks = list(self.tasks.values())
        for lease in list(self.run_leases.values()):
            self.coordinator.interrupt(
                "task",
                lease.owner_id,
                reason="server_shutdown",
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def register_run(self, run_id: str, session_id: str) -> None:
        self.run_sessions[str(run_id or "")] = str(session_id or "")

    def wake(self, run_id: str) -> bool:
        if self.closed:
            return False
        current = self.tasks.get(run_id)
        if current is not None and not current.done():
            return True
        if current is not None:
            self.tasks.pop(run_id, None)
            previous_lease = self.run_leases.pop(str(run_id), None)
            if previous_lease is not None:
                self.coordinator.finish(
                    previous_lease,
                    status="cancelled" if current.cancelled() else "completed",
                    termination_reason=previous_lease.termination_reason,
                )
        target_session = self.run_sessions.get(str(run_id), "")
        if not target_session:
            return False
        lease = self.run_leases.get(str(run_id))
        if lease is None or lease.released:
            lease = self.coordinator.try_acquire(
                "task",
                target_session,
                str(run_id),
                run_type="goal_loop",
                bind_current_task=False,
                payload={"goalLoopRunId": str(run_id)},
            )
            if lease is None:
                return False
            self.run_leases[str(run_id)] = lease
        task = asyncio.create_task(self._run(run_id))
        if not self.coordinator.attach_task(lease, task):
            task.cancel()
            self.run_leases.pop(str(run_id), None)
            self.coordinator.finish(
                lease,
                status="cancelled",
                termination_reason="ownership_lost",
            )
            return False
        self.tasks[run_id] = task

        def done(completed: asyncio.Task[Any]) -> None:
            if self.tasks.get(run_id) is completed:
                self.tasks.pop(run_id, None)
            if self.run_leases.get(str(run_id)) is lease:
                self.run_leases.pop(str(run_id), None)
                self.coordinator.finish(
                    lease,
                    status="cancelled" if completed.cancelled() else "completed",
                    termination_reason=lease.termination_reason,
                )
            self.run_sessions.pop(str(run_id), None)
            try:
                completed.exception()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Goal-loop worker failed", exc_info=True)

        task.add_done_callback(done)
        return True

    def interrupt(self, session_id: str, *, reason: str) -> bool:
        return self.coordinator.interrupt(
            "task",
            str(session_id or ""),
            reason=reason,
        )

    async def cancel_agent_context(
        self,
        session_id: str,
        run_id: str,
        *,
        reason: str,
    ) -> bool:
        """Settle the worker, then durably cancel its exact Agent run."""

        worker = self.tasks.get(str(run_id or ""))
        if (
            worker is not None
            and worker is not asyncio.current_task()
            and not worker.done()
        ):
            await asyncio.gather(worker, return_exceptions=True)
        try:
            _payload, project, session = _read_session(session_id)
        except KeyError:
            return False
        agent_run_id = next(
            (
                str(step.get("goalLoopAgentRunId") or "").strip()
                for step in session.get("plan") or []
                if isinstance(step, dict)
                and str(step.get("status") or "") == "running"
                and str(step.get("goalLoopAgentRunId") or "").strip()
            ),
            "",
        )
        if not agent_run_id:
            return False
        try:
            return await self.agent_runtime.cancel_turn(
                project=project,
                session=session,
                run_id=agent_run_id,
                reason=reason,
            )
        except Exception:
            logger.warning(
                "Failed to persist Goal-loop Agent cancellation "
                "[session=%s run=%s agent_run=%s]",
                session_id,
                run_id,
                agent_run_id,
                exc_info=True,
            )
            return False

    async def _lease(self, run: dict[str, Any]) -> dict[str, Any] | None:
        now = _utc_now()
        lease_until = now + timedelta(minutes=10)
        changed = await _execute(
            self.db_path,
            """
            UPDATE goal_runs
            SET lease_owner = ?, lease_until = ?, updated_at = ?
            WHERE id = ? AND status = 'running'
              AND (lease_until IS NULL OR lease_until < ? OR lease_owner = ?)
            """,
            (
                self.owner,
                lease_until.isoformat(),
                now.isoformat(),
                str(run["id"]),
                now.isoformat(),
                self.owner,
            ),
        )
        return await _get_run_by_id(self.db_path, str(run["id"])) if changed else None

    async def sync_projection(self, run: dict[str, Any], *, message: str = "") -> None:
        await sync_goal_loop_projection(run, message=message)

    async def _pause_run(
        self,
        run: dict[str, Any],
        *,
        stop_reason: str,
        message: str,
        event_type: str,
        last_error: str = "",
        step_id: str = "",
    ) -> None:
        paused = await _set_inactive_status(
            self.db_path,
            run,
            "paused",
            phase="paused",
            stop_reason=stop_reason,
            last_error=last_error,
        )
        if paused:
            await _event(
                self.db_path,
                str(run["id"]),
                event_type,
                step_id=step_id,
                payload=({"error": last_error} if last_error else {}),
            )
            await self.sync_projection(paused, message=message)

    async def _with_lease_renewal(
        self,
        run_id: str,
        operation: Any,
    ) -> Any:
        """Keep the durable goal lease alive while one Agent turn is running."""

        task = asyncio.create_task(operation)
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=60)
                if task in done:
                    return await task
                current = await _get_run_by_id(self.db_path, run_id)
                if not current or str(current.get("status") or "") != "running":
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise asyncio.CancelledError
                if await self._lease(current) is None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise RuntimeError("Goal-loop lease ownership was lost")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _reflect_safely(
        self,
        session_id: str,
        *,
        focus: str,
        goal_gap: str,
        trigger: str,
    ) -> dict[str, Any] | None:
        try:
            return await _reflect(
                self.agent_runtime,
                session_id,
                focus=focus,
                goal_gap=goal_gap,
                trigger=trigger,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Goal-loop optional reflection failed [session=%s trigger=%s]",
                session_id,
                trigger,
                exc_info=True,
            )
            return None

    async def _execute_step(
        self,
        run: dict[str, Any],
        project: dict[str, Any],
        session: dict[str, Any],
        step: dict[str, Any],
    ) -> bool:
        """Execute and verify one step. Return whether the outer loop may continue."""

        run_id = str(run["id"])
        session_id = str(run["session_id"])
        step_id = str(step.get("id") or "")
        resume_answer = (
            dict(step["goalLoopResumeAnswer"])
            if isinstance(step.get("goalLoopResumeAnswer"), dict)
            else None
        )
        persisted_agent_run_id = str(step.get("goalLoopAgentRunId") or "").strip()
        agent_run_id = persisted_agent_run_id or project_runtime._short_id("goal_agent")
        first_start = not persisted_agent_run_id

        current_run = await _update_run(
            self.db_path,
            run_id,
            phase="executing",
            current_step_id=step_id,
            lease_until=(_utc_now() + timedelta(minutes=10)).isoformat(),
        )
        if not current_run:
            return False

        def start_step(
            _payload: dict[str, Any],
            _project: dict[str, Any],
            fresh: dict[str, Any],
        ) -> None:
            for candidate in fresh.get("plan") or []:
                if not isinstance(candidate, dict) or str(candidate.get("id") or "") != step_id:
                    continue
                candidate["status"] = "running"
                candidate.setdefault("startedAt", _utc_iso())
                candidate["goalLoopAgentRunId"] = agent_run_id
                candidate["currentAction"] = (
                    _l(
                        "Applying your reply to the original Agent run and continuing this step.",
                        "正在将你的回复写回原 Agent run 并继续此步骤。",
                    )
                    if resume_answer
                    else _l(
                        "Restoring this step from the original Agent run.",
                        "正在从原 Agent run 恢复此步骤。",
                    )
                    if persisted_agent_run_id
                    else _l(
                        "Continuous-execution mode is processing this step.",
                        "持续执行模式正在处理此步骤。",
                    )
                )
            fresh["goalLoop"] = _public_run(current_run)
            fresh["status"] = "running"

        _write_session(session_id, start_step)
        await _event(
            self.db_path,
            run_id,
            "step_started" if first_start else "step_resumed",
            step_id=step_id,
            payload={"agentRunId": agent_run_id},
        )
        await self.sync_projection(
            current_run,
            message=_l(
                "Continuous execution: {step}",
                "持续执行中：{step}",
                step=step.get('title') or _l("Current step", "当前步骤"),
            ),
        )

        _payload, current_project, current_session = _read_session(session_id)
        current_step = next(
            (
                candidate
                for candidate in current_session.get("plan") or []
                if isinstance(candidate, dict)
                and str(candidate.get("id") or "") == step_id
            ),
            step,
        )
        workspace_root = artifact_runtime._workbench_workspace_root(current_project)
        git_before = artifact_runtime._workbench_git_status_snapshot(workspace_root)
        workspace_files_before = artifact_runtime._workbench_workspace_file_snapshot(
            workspace_root
        )
        workspace_text_before = artifact_runtime._workbench_workspace_text_snapshot(
            workspace_root
        )
        started_at = str(current_step.get("startedAt") or _utc_iso())
        step_prompt = _step_prompt(current_session, current_step)
        execution_error = ""
        agent_result: TaskAgentResult | None = None
        try:
            if resume_answer:
                agent_result = await self._with_lease_renewal(
                    run_id,
                    self.agent_runtime.answer_turn(
                        project=current_project,
                        session=current_session,
                        question_id=str(resume_answer.get("questionId") or ""),
                        answer=str(resume_answer.get("answer") or ""),
                        run_id=agent_run_id,
                        permission_mode=str(run.get("permission_mode") or "auto"),
                        command="workbench-goal-loop-answer",
                        purpose="goal_loop_answer",
                        instruction=(
                            "Continue only the current bounded goal-loop step after "
                            "mounting this answer into the pending Plugin call."
                        ),
                        metadata={
                            "goal_loop_run_id": run_id,
                            "step_id": step_id,
                        },
                        cancel_on_caller_cancel=False,
                    ),
                )
            else:
                agent_result = await self._with_lease_renewal(
                    run_id,
                    self.agent_runtime.run_turn(
                        project=current_project,
                        session=current_session,
                        text=step_prompt,
                        run_id=agent_run_id,
                        permission_mode=str(run.get("permission_mode") or "auto"),
                        command="workbench-goal-loop-step",
                        purpose="goal_loop_step",
                        instruction=(
                            "Complete only the current bounded step. Use Plugin tools "
                            "for real work and verification. The outer Goal Loop owns "
                            "whole-goal completion; ask the user only when genuinely blocked."
                        ),
                        metadata={
                            "goal_loop_run_id": run_id,
                            "step_id": step_id,
                        },
                        cancel_on_caller_cancel=False,
                    ),
                )
        except asyncio.CancelledError:
            raise
        except TaskAgentRuntimeError as exc:
            if str(exc.code).startswith("budget_"):
                await self._pause_run(
                    await _get_run_by_id(self.db_path, run_id) or current_run,
                    stop_reason=str(exc.code),
                    message=_l(
                        "A budget limit prevented further execution, so the continuous task was paused: {message}",
                        "预算限制阻止了继续执行，持续任务已暂停：{message}",
                        message=exc.message,
                    ),
                    event_type="budget_blocked",
                    last_error=exc.message,
                    step_id=step_id,
                )
                return False
            execution_error = exc.message
        except Exception:
            logger.exception("Goal-loop Agent step failed")
            execution_error = _l(
                "Step execution failed.",
                "步骤执行失败。",
            )

        latest_run = await _get_run_by_id(self.db_path, run_id)
        if not latest_run or str(latest_run.get("status") or "") != "running":
            return False

        if agent_result is not None and agent_result.awaiting_user:
            pending_question = dict(agent_result.pending_question or {})
            pending_question["roundId"] = agent_run_id
            pending_question.setdefault("ownerLane", "execution")
            display_reply = (
                str(pending_question.get("text") or agent_result.text).strip()
                or _l(
                    "Your reply is required before execution can continue.",
                    "需要你的回复后才能继续。",
                )
            )

            def wait_for_user(
                _payload: dict[str, Any],
                _project: dict[str, Any],
                fresh: dict[str, Any],
            ) -> None:
                fresh["pendingQuestion"] = pending_question
                fresh["pendingPlanStep"] = {
                    "stepId": step_id,
                    "goalLoop": True,
                    "agentRunId": agent_run_id,
                }
                fresh["status"] = "waiting_for_user"
                fresh["agentReply"] = display_reply
                for candidate in fresh.get("plan") or []:
                    if (
                        isinstance(candidate, dict)
                        and str(candidate.get("id") or "") == step_id
                    ):
                        candidate["status"] = "running"
                        candidate["goalLoopAgentRunId"] = agent_run_id
                        candidate.pop("goalLoopResumeAnswer", None)
                        candidate["currentAction"] = _l(
                            "Waiting for user confirmation before continuing.",
                            "等待用户确认后继续。",
                        )

            _write_session(session_id, wait_for_user)
            waiting = await _set_inactive_status(
                self.db_path,
                latest_run,
                "waiting_for_user",
                phase="waiting_for_user",
                stop_reason="user_input",
            )
            if waiting:
                await _event(
                    self.db_path,
                    run_id,
                    "waiting_for_user",
                    step_id=step_id,
                    payload={
                        "agentRunId": agent_run_id,
                        "questionId": str(pending_question.get("id") or ""),
                    },
                )
                await self.sync_projection(waiting, message=display_reply)
            return False

        display_reply = (
            str(agent_result.text or "").strip()
            if agent_result is not None
            else execution_error
        )
        git_after = artifact_runtime._workbench_git_status_snapshot(workspace_root)
        workspace_files_after = artifact_runtime._workbench_workspace_file_snapshot(
            workspace_root
        )
        workspace_text_after = artifact_runtime._workbench_workspace_text_snapshot(
            workspace_root
        )
        _payload, latest_project, latest_session = _read_session(session_id)

        if execution_error:
            verification = {
                "passed": False,
                "evidence": execution_error,
                "retry_guidance": _l(
                    "Fix the Agent execution error, then run this step again.",
                    "修复 Agent 执行错误后重新运行本步骤。",
                ),
            }
        else:
            try:
                verification = await _verify_step(
                    self.agent_runtime,
                    latest_session,
                    latest_project,
                    current_step,
                    display_reply,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                safe_error = _l(
                    "Independent step verification is temporarily unavailable.",
                    "步骤独立验收暂时不可用。",
                )
                await self._pause_run(
                    latest_run,
                    stop_reason="step_verification_unavailable",
                    message=_l(
                        "Independent step verification is temporarily unavailable, so continuous execution was paused.",
                        "步骤独立验收暂时不可用，持续执行已暂停。",
                    ),
                    event_type="step_verification_unavailable",
                    last_error=safe_error,
                    step_id=step_id,
                )
                return False

        tool_events = _project_tool_file_changes(
            list(agent_result.tool_events) if agent_result is not None else [],
            workspace_root,
        )
        activity_events = [
            *tool_events,
            {
                "id": project_runtime._short_id("event"),
                "type": "AgentResponseEvent",
                "runId": agent_run_id,
                "createdAt": _utc_iso(),
                "body": display_reply,
            },
        ]
        file_changes = artifact_runtime._workbench_collect_run_file_changes(
            tool_events,
            git_before,
            git_after,
            workspace_files_before,
            workspace_files_after,
            workspace_root,
            f"{step_prompt}\n{display_reply}",
            workspace_text_before=workspace_text_before,
            workspace_text_after=workspace_text_after,
        )
        tool_calls = [
            {
                "tool": str(item.get("tool") or ""),
                "argsPreview": str(item.get("argsPreview") or ""),
            }
            for item in tool_events
        ]
        run_record = {
            "id": agent_run_id,
            "taskId": session_id,
            "runType": "goal_loop",
            "userInput": step_prompt,
            "agentResponse": display_reply,
            "status": "failed" if execution_error else "completed",
            "startedAt": started_at,
            "endedAt": _utc_iso(),
            "events": activity_events,
            "fileChanges": file_changes,
            "toolCalls": tool_calls,
            "artifacts": [],
            "attachments": [],
            "mode": str(run.get("permission_mode") or "auto"),
            "error": execution_error or None,
            "goalLoopRunId": run_id,
            "stepVerification": verification,
            "usage": (
                dict(agent_result.usage) if agent_result is not None else {}
            ),
            "model": str(agent_result.model if agent_result is not None else ""),
            "modelIdentity": (
                dict(agent_result.model_identity)
                if agent_result is not None
                else {}
            ),
            "generationDurationMs": (
                agent_result.generation_duration_ms
                if agent_result is not None
                else None
            ),
            "outputTokensPerSecond": (
                agent_result.output_tokens_per_second
                if agent_result is not None
                else None
            ),
        }
        passed = bool(verification.get("passed"))
        step_attempts = 0
        outcome = (
            {
                "summary": display_reply[:500],
                "filesChanged": [
                    str(change.get("path") or "")
                    for change in file_changes
                    if str(change.get("path") or "")
                ][:30],
                "issues": [],
            }
            if passed and display_reply
            else None
        )

        def finish_step(
            _payload: dict[str, Any],
            _project: dict[str, Any],
            fresh: dict[str, Any],
        ) -> None:
            nonlocal step_attempts
            runs = fresh.setdefault("runs", [])
            existing_index = next(
                (
                    index
                    for index, item in enumerate(runs)
                    if isinstance(item, dict)
                    and str(item.get("id") or "") == agent_run_id
                ),
                None,
            )
            if existing_index is None:
                runs.append(run_record)
            else:
                runs[existing_index] = run_record
            known_event_ids = {
                str(item.get("id") or "")
                for item in fresh.setdefault("events", [])
                if isinstance(item, dict)
            }
            fresh["events"].extend(
                item
                for item in activity_events
                if str(item.get("id") or "") not in known_event_ids
            )
            fresh["agentReply"] = display_reply
            for candidate in fresh.get("plan") or []:
                if (
                    not isinstance(candidate, dict)
                    or str(candidate.get("id") or "") != step_id
                ):
                    continue
                candidate["updatedAt"] = _utc_iso()
                candidate["toolCalls"] = tool_calls
                candidate["stepVerification"] = verification
                candidate.pop("goalLoopResumeAnswer", None)
                if passed:
                    candidate["status"] = "completed"
                    candidate["completedAt"] = _utc_iso()
                    candidate.pop("goalLoopAgentRunId", None)
                    candidate["currentAction"] = (
                        str(verification.get("evidence") or "").strip()
                        or _l(
                            "Step completed; the overall goal will be independently verified after all steps finish.",
                            "步骤执行完成；最终目标将在全部步骤后独立验收。",
                        )
                    )
                    if outcome:
                        candidate["outcome"] = outcome
                else:
                    candidate["status"] = "pending"
                    candidate["startedAt"] = None
                    candidate.pop("goalLoopAgentRunId", None)
                    step_attempts = int(candidate.get("goalLoopAttempts") or 0) + 1
                    candidate["goalLoopAttempts"] = step_attempts
                    candidate["currentAction"] = (
                        str(verification.get("retry_guidance") or "").strip()
                        or str(verification.get("evidence") or "").strip()
                        or _l(
                            "Step verification failed. Continue fixing the issues.",
                            "步骤验收未通过，请继续修复。",
                        )
                    )
            artifact_runtime._workbench_apply_step_file_changes(
                fresh, step_id, file_changes
            )
            artifact_runtime._workbench_promote_file_artifacts(
                fresh, file_changes, _utc_iso()
            )
            fresh["planRevision"] = int(fresh.get("planRevision") or 0) + 1
            fresh["status"] = "running"
            fresh["goalLoop"] = _public_run(latest_run)

        _write_session(session_id, finish_step)
        await _event(
            self.db_path,
            run_id,
            "step_verified" if passed else "step_verification_failed",
            step_id=step_id,
            payload={
                **verification,
                "agentRunId": agent_run_id,
            },
        )

        if not passed and step_attempts >= _STEP_FAILURE_CAP:
            reflecting = await _update_run(
                self.db_path, run_id, phase="reflecting", current_step_id=None
            )
            if reflecting:
                await self.sync_projection(
                    reflecting,
                    message=_l(
                        "The step repeatedly failed verification. Analyzing the root cause.",
                        "步骤反复未通过验收，正在深度思考失败根因。",
                    ),
                )
            await self._reflect_safely(
                session_id,
                focus=str(
                    verification.get("retry_guidance")
                    or _l(
                        'Step "{step}" repeatedly failed verification',
                        '步骤「{step}」反复未通过验收',
                        step=current_step.get('title') or '',
                    )
                ),
                goal_gap=_l(
                    "The same step failed independent verification several times in succession. Analyze the root cause and change the approach instead of retrying mechanically.",
                    "同一步骤连续多次独立验收未通过，需要分析根因并改变方案，而不是继续机械重试。",
                ),
                trigger="goal_loop_step_blocked",
            )
            blocked = await _set_inactive_status(
                self.db_path,
                await _get_run_by_id(self.db_path, run_id) or latest_run,
                "blocked",
                phase="blocked",
                stop_reason="step_stuck",
            )
            if blocked:
                language = app_language()
                await _event(
                    self.db_path,
                    run_id,
                    "step_blocked",
                    step_id=step_id,
                    payload={"attempts": step_attempts},
                )
                await self.sync_projection(
                    blocked,
                    message=localized(
                        'Step "{step}" failed independent verification {attempts} consecutive times, so continuous execution is blocked. Adjust the plan or goal before continuing.',
                        '步骤「{step}」连续 {attempts} 次未通过独立验收，持续执行已阻塞。请调整计划或目标后再继续。',
                        language=language,
                        step=current_step.get('title') or localized(
                            "Current step", "当前步骤", language=language
                        ),
                        attempts=step_attempts,
                    ),
                )
                append_notification(
                    title=localized(
                        "Continuous execution blocked",
                        "持续执行已阻塞",
                        language=language,
                    ),
                    body=localized(
                        'Step "{step}" repeatedly failed verification and needs your attention.',
                        '步骤「{step}」反复未通过验收，需要你介入。',
                        language=language,
                        step=current_step.get('title') or localized(
                            "Current step", "当前步骤", language=language
                        ),
                    ),
                    tab="system",
                    source="goal_loop_blocked",
                    source_label=localized(
                        "Continuous execution", "持续执行", language=language
                    ),
                    meta={"sessionId": session_id, "runId": run_id},
                    language=language,
                )
            return False

        reflection_mode = str(run.get("reflection_mode") or "")
        if passed and reflection_mode == "frequent":
            reflecting = await _update_run(self.db_path, run_id, phase="reflecting")
            if reflecting:
                await self.sync_projection(
                    reflecting,
                    message=_l(
                        "Step completed. Reflecting on the direction.",
                        "步骤完成，正在进行深度思考。",
                    ),
                )
            await self._reflect_safely(
                session_id,
                focus=_l(
                    'Direction check after completing step "{step}"',
                    '步骤「{step}」完成后的方向检查',
                    step=current_step.get('title') or '',
                ),
                goal_gap=_l(
                    "Check whether the current result genuinely narrows the goal gap and whether the remaining plan needs adjustment.",
                    "检查当前成果是否真正缩小了目标差距，以及后续计划是否需要调整。",
                ),
                trigger="goal_loop_step",
            )
        elif not passed and reflection_mode == "frequent":
            await self._reflect_safely(
                session_id,
                focus=str(verification.get("retry_guidance") or ""),
                goal_gap=_l(
                    "The current step failed independent verification. Analyze the root cause and change the execution approach.",
                    "当前步骤独立验收未通过，需要分析根因并改变执行方式。",
                ),
                trigger="goal_loop_step_failure",
            )
        await _update_run(
            self.db_path,
            run_id,
            phase="executing",
            current_step_id=None,
        )
        return True

    async def _archive_completed_goal(
        self,
        project: dict[str, Any],
        session: dict[str, Any],
        run_id: str,
    ) -> None:
        """Archive through the active knowledge Plugin application service."""

        service = active_plugin_service("knowledge")
        if service is None:
            return
        await service.archive_run(
            project,
            session,
            {
                "id": run_id,
                "userInput": str(session.get("goal") or ""),
                "agentResponse": str(session.get("agentReply") or ""),
            },
            artifact_runtime._workbench_workspace_root(project),
            _utc_iso(),
        )

    async def _run(self, run_id: str) -> None:
        try:
            while not self.closed:
                run = await _get_run_by_id(self.db_path, run_id)
                if not run or str(run.get("status") or "") != "running":
                    return
                run = await self._lease(run)
                if not run:
                    return

                public = _public_run(run) or {}
                if int(public.get("activeSeconds") or 0) >= int(
                    run.get("max_active_seconds") or 0
                ):
                    paused = await _set_inactive_status(
                        self.db_path,
                        run,
                        "paused",
                        phase="paused",
                        stop_reason="max_runtime",
                    )
                    if paused:
                        language = app_language()
                        await _event(
                            self.db_path, run_id, "runtime_limit_reached"
                        )
                        await self.sync_projection(
                            paused,
                            message=localized(
                                "Continuous execution paused after reaching the maximum runtime.",
                                "已达到最大运行时间，持续执行已暂停。",
                                language=language,
                            ),
                        )
                        append_notification(
                            title=localized(
                                "Continuous execution paused",
                                "持续执行已暂停",
                                language=language,
                            ),
                            body=localized(
                                "The task reached its maximum runtime. Adjust the limit before continuing.",
                                "任务达到最大运行时间，可调整限制后继续。",
                                language=language,
                            ),
                            tab="system",
                            source="goal_loop_paused",
                            source_label=localized(
                                "Continuous execution", "持续执行", language=language
                            ),
                            meta={
                                "sessionId": str(run["session_id"]),
                                "runId": run_id,
                            },
                            language=language,
                        )
                    return

                try:
                    _payload, project, session = _read_session(
                        str(run["session_id"])
                    )
                except KeyError:
                    cancelled = await _set_inactive_status(
                        self.db_path,
                        run,
                        "cancelled",
                        phase="cancelled",
                        stop_reason="session_missing",
                    )
                    if cancelled:
                        await _publish(cancelled)
                    return

                plan = (
                    session.get("plan")
                    if isinstance(session.get("plan"), list)
                    else []
                )
                resume_step = next(
                    (
                        item
                        for item in plan
                        if isinstance(item, dict)
                        and isinstance(item.get("goalLoopResumeAnswer"), dict)
                    ),
                    None,
                )
                step = resume_step or _recoverable_step(plan) or _next_step(plan)
                if step is not None:
                    if not await self._execute_step(run, project, session, step):
                        return
                    continue

                unresolved = [
                    item
                    for item in plan
                    if isinstance(item, dict)
                    and str(item.get("status") or "pending")
                    not in {"completed", "done", "skipped"}
                ]
                if unresolved:
                    blocked = await _set_inactive_status(
                        self.db_path,
                        run,
                        "blocked",
                        phase="blocked",
                        stop_reason="dependency_blocked",
                    )
                    if blocked:
                        await _event(self.db_path, run_id, "dependency_blocked")
                        await self.sync_projection(
                            blocked,
                            message=_l(
                                "No step is currently executable; plan dependencies are blocking the task.",
                                "没有可执行步骤，任务被计划依赖阻塞。",
                            ),
                        )
                    return

                reflection_mode = str(
                    run.get("reflection_mode") or "proactive"
                )
                if reflection_mode in {"proactive", "frequent"}:
                    reflecting = await _update_run(
                        self.db_path,
                        run_id,
                        phase="reflecting",
                        current_step_id=None,
                    )
                    if reflecting:
                        await self.sync_projection(
                            reflecting,
                            message=_l(
                                "All steps have been processed. Reflecting before final acceptance.",
                                "全部步骤已处理，正在最终验收前深度思考。",
                            ),
                        )
                    await self._reflect_safely(
                        str(run["session_id"]),
                        focus=_l(
                            "Check for omissions, false completion, and superficial compliance before final acceptance",
                            "最终验收前检查遗漏、假完成和表面满足",
                        ),
                        goal_gap=_l(
                            "All planned steps have run. Confirm whether any remaining goal gap could still prevent acceptance.",
                            "全部计划步骤已执行，需要确认是否仍存在影响验收的目标差距。",
                        ),
                        trigger="goal_loop_pre_verification",
                    )

                verifying = await _update_run(
                    self.db_path,
                    run_id,
                    phase="verifying",
                    current_step_id=None,
                )
                if not verifying:
                    return
                await self.sync_projection(
                    verifying,
                    message=_l(
                        "Independently verifying the goal.",
                        "正在独立验收目标。",
                    ),
                )
                _payload, project, session = _read_session(
                    str(run["session_id"])
                )
                try:
                    verdict = await self.agent_runtime.verify_acceptance(
                        session, project
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    safe_error = _l(
                        "Independent acceptance is temporarily unavailable.",
                        "独立验收暂时不可用。",
                    )
                    await self._pause_run(
                        verifying,
                        stop_reason="verification_unavailable",
                        message=_l(
                            "Independent acceptance is temporarily unavailable, so continuous execution was paused.",
                            "独立验收暂时不可用，持续执行已暂停。",
                        ),
                        event_type="verification_unavailable",
                        last_error=safe_error,
                    )
                    return
                if not isinstance(verdict, dict):
                    await self._pause_run(
                        verifying,
                        stop_reason="verification_unavailable",
                        message=_l(
                            "Independent acceptance returned no valid result, so continuous execution was paused.",
                            "独立验收没有返回有效结果，持续执行已暂停。",
                        ),
                        event_type="verification_unavailable",
                    )
                    return

                results = (
                    verdict.get("results")
                    if isinstance(verdict.get("results"), list)
                    else []
                )
                by_id = {
                    str(item.get("id") or ""): item
                    for item in results
                    if isinstance(item, dict)
                }
                any_failed = False
                acceptance_passed = False

                def apply_verdict(
                    _payload: dict[str, Any],
                    _project: dict[str, Any],
                    fresh: dict[str, Any],
                ) -> None:
                    nonlocal any_failed, acceptance_passed
                    criteria = [
                        item
                        for item in fresh.get("acceptanceCriteria") or []
                        if isinstance(item, dict)
                    ]
                    for criterion in criteria:
                        result = by_id.get(str(criterion.get("id") or ""))
                        if not isinstance(result, dict):
                            criterion["status"] = "failed"
                            criterion["evidence"] = _l(
                                "The verifier returned no conclusion for this criterion.",
                                "验收器未返回这一项的结论。",
                            )
                            any_failed = True
                            continue
                        passed = bool(result.get("passed"))
                        criterion["status"] = "passed" if passed else "failed"
                        criterion["evidence"] = str(
                            result.get("evidence") or ""
                        )
                        any_failed = any_failed or not passed
                    fresh["acceptanceCriteria"] = criteria
                    fresh["verifyReason"] = str(verdict.get("reason") or "")
                    acceptance_passed = bool(criteria) and not any_failed
                    if acceptance_passed:
                        project_runtime._workbench_mark_completed_if_acceptance_passed(
                            fresh,
                            event_body=_l(
                                "Continuous execution passed independent acceptance. All acceptance criteria passed, and the task was marked complete automatically.",
                                "持续执行独立验收通过，所有验收标准均已通过，任务自动标记为已完成。",
                            ),
                        )

                _write_session(str(run["session_id"]), apply_verdict)
                await _event(
                    self.db_path, run_id, "goal_verified", payload=verdict
                )

                if acceptance_passed:
                    completed = await _set_inactive_status(
                        self.db_path,
                        verifying,
                        "completed",
                        phase="completed",
                        stop_reason="acceptance_passed",
                    )
                    if completed:
                        language = app_language()
                        await self.sync_projection(
                            completed,
                            message=localized(
                                "Automatic acceptance passed; the task was marked complete.",
                                "自动验收通过，任务已自动标记为已完成。",
                                language=language,
                            ),
                        )
                        try:
                            _payload, final_project, final_session = (
                                _read_session(str(run["session_id"]))
                            )
                            await self._archive_completed_goal(
                                final_project, final_session, run_id
                            )
                        except Exception:
                            logger.exception(
                                "Goal-loop Plugin knowledge archive failed"
                            )
                        append_notification(
                            title=localized(
                                "Continuous execution passed acceptance",
                                "持续执行验收通过",
                                language=language,
                            ),
                            body=localized(
                                'Task "{title}" passed automatic acceptance.',
                                '任务「{title}」已通过自动验收。',
                                language=language,
                                title=session.get('title') or localized(
                                    "Untitled task", "未命名任务", language=language
                                ),
                            ),
                            tab="comment",
                            project_ref=project.get("id"),
                            source="goal_loop_passed",
                            source_label=localized(
                                "Continuous execution", "持续执行", language=language
                            ),
                            link_label=str(session.get("title") or ""),
                            meta={
                                "sessionId": str(run["session_id"]),
                                "runId": run_id,
                            },
                            language=language,
                        )
                    return

                repair_round = int(verifying.get("repair_round") or 0)
                max_repairs = int(verifying.get("max_repair_rounds") or 0)
                if repair_round >= max_repairs:
                    paused = await _set_inactive_status(
                        self.db_path,
                        verifying,
                        "paused",
                        phase="paused",
                        stop_reason="max_repair_rounds",
                    )
                    if paused:
                        language = app_language()
                        await self.sync_projection(
                            paused,
                            message=localized(
                                "Continuous execution paused after reaching the maximum repair rounds.",
                                "已达到最大返工轮数，持续执行已暂停。",
                                language=language,
                            ),
                        )
                        append_notification(
                            title=localized(
                                "Continuous execution paused",
                                "持续执行已暂停",
                                language=language,
                            ),
                            body=localized(
                                "The task reached the maximum repair rounds. Adjust the limit before continuing.",
                                "任务达到最大返工轮数，可调整限制后继续。",
                                language=language,
                            ),
                            tab="system",
                            source="goal_loop_paused",
                            source_label=localized(
                                "Continuous execution", "持续执行", language=language
                            ),
                            meta={
                                "sessionId": str(run["session_id"]),
                                "runId": run_id,
                            },
                            language=language,
                        )
                    return

                reflecting = await _update_run(
                    self.db_path, run_id, phase="reflecting"
                )
                if reflecting:
                    await self.sync_projection(
                        reflecting,
                        message=_l(
                            "Acceptance failed. Reflecting on the cause.",
                            "验收未通过，正在深度思考失败原因。",
                        ),
                    )
                await self._reflect_safely(
                    str(run["session_id"]),
                    focus=str(verdict.get("reason") or _l(
                        "Acceptance failed", "验收未通过"
                    )),
                    goal_gap=_l(
                        "Independent acceptance failed. Analyze the root cause and generate a new repair path.",
                        "独立验收未通过，需要分析失败根因并生成新的返工路径。",
                    ),
                    trigger="goal_loop_verification_failure",
                )
                _payload, project, session = _read_session(
                    str(run["session_id"])
                )
                repair_steps = await _generate_repair_steps(
                    self.agent_runtime, session, project, verdict
                )

                def append_repairs(
                    _payload: dict[str, Any],
                    _project: dict[str, Any],
                    fresh: dict[str, Any],
                ) -> None:
                    plan_items = [
                        item
                        for item in fresh.get("plan") or []
                        if isinstance(item, dict)
                    ]
                    base_order = len(plan_items)
                    for index, repair in enumerate(repair_steps, 1):
                        repair["order"] = base_order + index
                        repair["goalLoopRepairRound"] = repair_round + 1
                        plan_items.append(repair)
                    fresh["plan"] = plan_items
                    fresh["planRevision"] = (
                        int(fresh.get("planRevision") or 0) + 1
                    )
                    fresh["planDefinitionRevision"] = (
                        int(fresh.get("planDefinitionRevision") or 0) + 1
                    )
                    fresh["approvedPlanDefinitionRevision"] = fresh[
                        "planDefinitionRevision"
                    ]
                    for criterion in fresh.get("acceptanceCriteria") or []:
                        if isinstance(criterion, dict):
                            criterion["status"] = "pending"
                            criterion.pop("evidence", None)
                    fresh["status"] = "running"
                    fresh["agentReply"] = _l(
                        "Acceptance failed. Generated repair-round {round} steps.",
                        "验收未通过，已生成第 {round} 轮返工步骤。",
                        round=repair_round + 1,
                    )

                _payload, _project, updated_session = _write_session(
                    str(run["session_id"]), append_repairs
                )
                repaired = await _update_run(
                    self.db_path,
                    run_id,
                    phase="repairing",
                    repair_round=repair_round + 1,
                    plan_definition_revision=int(
                        updated_session.get("planDefinitionRevision") or 0
                    ),
                )
                await _event(
                    self.db_path,
                    run_id,
                    "repair_planned",
                    payload={
                        "repairRound": repair_round + 1,
                        "stepCount": len(repair_steps),
                    },
                )
                if repaired:
                    await self.sync_projection(repaired)
        except asyncio.CancelledError:
            # The coordinator distinguishes user cancellation from process
            # shutdown. Agent calls use cancel_on_caller_cancel=False so their
            # ContextTree stays resumable; the durable Goal Loop status is owned
            # by pause/cancel/shutdown application paths.
            raise
        except Exception:
            logger.exception("Goal-loop worker failed [run=%s]", run_id)
            current = await _get_run_by_id(self.db_path, run_id)
            if current and str(current.get("status") or "") == "running":
                safe_error = _l(
                    "Continuous execution encountered an unexpected error.",
                    "持续执行发生意外错误。",
                )
                await self._pause_run(
                    current,
                    stop_reason="goal_loop_runtime_error",
                    message=_l(
                        "Continuous execution encountered an error and was safely paused.",
                        "持续执行发生错误并已安全暂停。",
                    ),
                    event_type="goal_loop_runtime_error",
                    last_error=safe_error,
                )


async def begin_async_answer(
    db_path: str,
    session_id: str,
    question_id: str,
    answer_text: str,
) -> bool:
    """Hand a goal-loop clarification answer to the background runner.

    The normal answer endpoint resumes the agent *inside the HTTP request*, which
    blocks the request for as long as the agent keeps working (and competes with
    the UI for the event loop). For a goal loop we instead tag the waiting step
    with the answer, optimistically clear the question card, and let the runner
    resume the agent in the background — the request returns immediately.

    Returns ``True`` when the loop took ownership of the answer; ``False`` (no
    waiting goal run) tells the caller to fall back to the synchronous path.
    """
    run = await _get_run_by_session(db_path, session_id)
    if not run or str(run.get("status") or "") != "waiting_for_user":
        return False
    try:
        _payload, _project, session = _read_session(session_id)
    except KeyError:
        return False
    pending_step = session.get("pendingPlanStep") if isinstance(session.get("pendingPlanStep"), dict) else {}
    step_id = str((pending_step or {}).get("stepId") or "")
    if not step_id or not bool((pending_step or {}).get("goalLoop")):
        return False
    pending_question = (
        session.get("pendingQuestion")
        if isinstance(session.get("pendingQuestion"), dict)
        else {}
    )
    if str(pending_question.get("id") or "") != str(question_id or ""):
        return False
    target_step = next(
        (
            item
            for item in session.get("plan") or []
            if isinstance(item, dict) and str(item.get("id") or "") == step_id
        ),
        None,
    )
    agent_run_id = str(
        (target_step or {}).get("goalLoopAgentRunId")
        or pending_question.get("roundId")
        or (pending_step or {}).get("agentRunId")
        or ""
    ).strip()
    if not agent_run_id:
        return False

    now = _utc_iso()
    run = await _update_run(
        db_path,
        str(run["id"]),
        status="running",
        phase="executing",
        active_started_at=now,
        stop_reason=None,
        lease_owner=None,
        lease_until=None,
    )
    if not run:
        return False

    def apply(_payload: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
        # The agent-side pending-question state stays in place (the runner clears
        # it when it resumes); we only clear the UI card and tag the step.
        fresh.pop("pendingQuestion", None)
        fresh.pop("pendingPlanStep", None)
        for step in fresh.get("plan") or []:
            if isinstance(step, dict) and str(step.get("id") or "") == step_id:
                step["goalLoopResumeAnswer"] = {"questionId": str(question_id or ""), "answer": str(answer_text or "")}
                step["goalLoopAgentRunId"] = agent_run_id
                step["status"] = "running"
                step["currentAction"] = _l(
                    "Your reply was received. Continuing this step.",
                    "已收到你的回复，正在继续执行此步骤。",
                )
        fresh["status"] = "running"
        fresh["agentReply"] = _l(
            "Your reply was received. Continuous execution will continue in the background.",
            "已收到你的回复，持续执行将在后台继续。",
        )
        fresh["goalLoop"] = _public_run(run)

    _write_session(session_id, apply)
    await _event(db_path, str(run["id"]), "answer_received", step_id=step_id)
    await _publish(run)
    manager = _MANAGERS.get(str(db_path))
    if manager:
        manager.register_run(str(run["id"]), session_id)
        if manager.wake(str(run["id"])) is False:
            paused = await _set_inactive_status(
                db_path,
                run,
                "paused",
                phase="paused",
                stop_reason="run_conflict",
            )
            if paused:
                await manager.sync_projection(
                    paused,
                    message=_l(
                        "Another task run is already active, so continuous execution was safely paused.",
                        "任务已有其他运行，持续执行已安全暂停。",
                    ),
                )
    return True


async def resume_after_answer(db_path: str, session_id: str, *, permission_denied: bool = False) -> None:
    """Resume a goal loop after the existing Workbench answer endpoint succeeds."""
    run = await _get_run_by_session(db_path, session_id)
    if not run or str(run.get("status") or "") != "waiting_for_user":
        return
    if permission_denied:
        paused = await _update_run(
            db_path,
            str(run["id"]),
            status="paused",
            phase="paused",
            stop_reason="permission_denied",
            lease_owner=None,
            lease_until=None,
        )
        if paused:
            await _event(db_path, str(run["id"]), "permission_denied")
            manager = _MANAGERS.get(str(db_path))
            if manager:
                await manager.sync_projection(
                    paused,
                    message=_l(
                        "The permission request was denied, so continuous execution was paused.",
                        "权限请求已拒绝，持续执行已暂停。",
                    ),
                )
            else:
                def apply(
                    _payload: dict[str, Any],
                    _project: dict[str, Any],
                    session: dict[str, Any],
                ) -> None:
                    session["status"] = "paused"
                    session["goalLoop"] = _public_run(paused)
                    session["agentReply"] = _l(
                        "The permission request was denied, so continuous execution was paused.",
                        "权限请求已拒绝，持续执行已暂停。",
                    )

                try:
                    _write_session(session_id, apply)
                except KeyError:
                    logger.warning(
                        "Pause state not written to session document (session=%s); UI may revert",
                        session_id, exc_info=True,
                    )
                await _publish(paused)
        return
    now = _utc_iso()
    run = await _update_run(
        db_path,
        str(run["id"]),
        status="running",
        phase="executing",
        active_started_at=now,
        stop_reason=None,
        lease_owner=None,
        lease_until=None,
    )
    if not run:
        return

    def apply(_payload: dict[str, Any], _project: dict[str, Any], session: dict[str, Any]) -> None:
        # The answer endpoint has already settled the answered step (marked it
        # complete) and cleared pendingPlanStep, so resuming only needs to flip
        # the run back to running and let the worker pick up the NEXT step. We must
        # NOT reset the answered step to pending here — that is what made the
        # runner re-execute it and re-ask the same question.
        session.pop("pendingPlanStep", None)
        session["status"] = "running"
        session["goalLoop"] = _public_run(run)

    _write_session(session_id, apply)
    manager = _MANAGERS.get(str(db_path))
    if manager:
        manager.register_run(str(run["id"]), session_id)
        if manager.wake(str(run["id"])) is False:
            paused = await _set_inactive_status(
                db_path,
                run,
                "paused",
                phase="paused",
                stop_reason="run_conflict",
            )
            if paused:
                await manager.sync_projection(
                    paused,
                    message=_l(
                        "Another task run is already active, so continuous execution was safely paused.",
                        "任务已有其他运行，持续执行已安全暂停。",
                    ),
                )
    await _publish(run)


__all__ = [
    "GoalLoopManager",
    "WorkbenchGoalLoopTransaction",
    "begin_async_answer",
    "register_goal_loop_manager",
    "resume_after_answer",
    "sync_goal_loop_projection",
]

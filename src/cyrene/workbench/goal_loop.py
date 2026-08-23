"""Durable, server-side goal loop for Workbench task sessions.

The normal agent loop is intentionally bounded to one request.  This module
adds a harness-owned loop around those bounded slices:

    execute one plan step -> verify it -> continue -> verify the whole goal
    -> reflect/repair on failure -> repeat within user-configured limits.

SQLite is the source of truth for both loop execution and the Workbench UI
projection. Legacy JSON files are migration/export artifacts only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from cyrene.observability import debug
from cyrene.agent import _AWAITING_USER_SENTINEL, interrupt_active_run
from cyrene.runtime.run_coordinator import RunLease, run_coordinator_for
from cyrene.workbench.notifications import append_notification
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

logger = logging.getLogger(__name__)

# A single step that fails independent verification this many times in a row is
# treated as stuck: the loop reflects once, then blocks instead of retrying the
# same step until the runtime budget is burned.
_STEP_FAILURE_CAP = 3
# A step is only finished once the subagents it spawned settle. Cap that wait so
# a wedged subagent can't stall the loop forever — on timeout the step proceeds
# to verification with a warning rather than hanging.
# A stalled subagent should become visible as timed out within a few minutes;
# a 30-minute silent wait looked like a healthy run while producing no work.
_SUBAGENT_SETTLE_TIMEOUT_SECONDS = 5 * 60
_SUBAGENT_HEARTBEAT_SECONDS = 15
_MANAGERS: dict[str, "GoalLoopManager"] = {}


def _utc_now() -> datetime:
    return utc_now()


def _utc_iso() -> str:
    return utc_iso()


def _json_dumps(value: Any) -> str:
    return json_dumps(value)


def _json_loads(value: Any, fallback: Any) -> Any:
    return json_loads(value, fallback)


async def _ensure_schema(db_path: str) -> None:
    from cyrene.workbench import runtime

    runtime._configure_workbench_store(str(db_path))
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
    from cyrene.workbench import runtime as R

    payload = R._read_workbench_store()
    project, session = R._workbench_find_session(payload, session_id)
    if not project or not session:
        raise KeyError("session not found")
    return payload, project, session


def _write_session(
    session_id: str,
    mutator: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from cyrene.workbench import runtime as R

    with R._WORKBENCH_STORE_LOCK:
        payload = R._read_workbench_store()
        project, session = R._workbench_find_session(payload, session_id)
        if not project or not session:
            raise KeyError("session not found")
        mutator(payload, project, session)
        now = R._utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        # Goal-loop runs in the background and must not steal the user's
        # persisted project selection. Only explicit UI activation is allowed
        # to change activeProjectId / activeSessionId.
        R._write_workbench_store(payload)
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
        if status in {"running", "waiting_for_user", "paused", "blocked", "review", "completed", "cancelled"}:
            session["status"] = status

    try:
        _write_session(str(run["session_id"]), apply)
    except KeyError:
        return
    await _publish(run)


class WorkbenchGoalLoopTransaction:
    """Public Workbench document/planning port used by the application service."""

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
        from cyrene.workbench import runtime

        plan, acceptance, generated, _operation = await runtime._workbench_generate_plan_steps(
            session,
            project,
            feedback="目标已由用户在持续执行配置中更新，请基于新目标重新生成完整计划。",
            requested_operation="replace",
        )
        return plan, acceptance, generated

    async def generate_acceptance(
        self, session: dict[str, Any], project: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        from cyrene.workbench import runtime

        return await runtime._workbench_generate_acceptance_criteria(session, project)

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

    @staticmethod
    def interrupt(session_id: str) -> None:
        interrupt_active_run(session_id=session_id)


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


def _step_prompt(session: dict[str, Any], step: dict[str, Any]) -> str:
    lines = [
        "你正在持续执行模式中完成一个有界工作片段。",
        f"总目标：{str(session.get('goal') or session.get('title') or '').strip()}",
        f"当前步骤：{str(step.get('title') or '').strip()}",
    ]
    if step.get("description"):
        lines.append("步骤说明：" + str(step.get("description") or "").strip())
    if step.get("promptOverride"):
        lines.append("用户为本步骤指定的执行命令：" + str(step.get("promptOverride") or "").strip())
    if step.get("currentAction"):
        lines.append("上一次结果或验证反馈：" + str(step.get("currentAction") or "").strip())
    lines.extend(
        [
            "请直接使用工具完成本步骤并验证关键结果。",
            "调用 quit 或输出最终文本只会结束当前工作片段，不代表整个目标完成。",
            "如果必须获得用户输入或权限，请使用 ask_user。",
        ]
    )
    return "\n".join(lines)


async def _verify_step(
    session: dict[str, Any],
    project: dict[str, Any],
    step: dict[str, Any],
    agent_reply: str,
) -> dict[str, Any]:
    from cyrene.workbench import runtime as R

    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    prompt = (
        "你是独立步骤验收 Agent。请只根据步骤定义、工作区真实产物和必要的只读检查，"
        "判断该步骤是否已经产生足够结果，可以进入下一步骤。不要因为执行 Agent 自称完成就通过。\n\n"
        f"总目标：{session.get('goal') or session.get('title') or ''}\n"
        f"步骤：{step.get('title') or ''}\n"
        f"步骤说明：{step.get('description') or ''}\n"
        f"执行结果摘要：{agent_reply[:2000]}\n\n"
        '只返回 JSON：{"passed": true/false, "evidence": "简短依据", "retry_guidance": "未通过时下一次应如何修复"}。'
    )
    try:
        parsed = await R._workbench_run_explore_agent(
            workspace_root,
            prompt,
            max_tokens=2400,
            timeout=90,
            session_id=str(session.get("id") or ""),
            clean_context=True,
            raise_on_failure=True,
        )
    except Exception as exc:
        logger.warning("Goal-loop step verification unavailable", exc_info=True)
        safe_error = R._workbench_generation_error(exc)
        raise RuntimeError(f"步骤独立验收暂时不可用：{safe_error.message}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("passed"), bool):
        raise RuntimeError("步骤独立验收没有返回有效结果。")
    return {
        "passed": bool(parsed["passed"]),
        "evidence": str(parsed.get("evidence") or "").strip(),
        "retry_guidance": str(parsed.get("retry_guidance") or "").strip(),
    }


async def _reflect(
    session_id: str,
    *,
    focus: str,
    goal_gap: str,
    trigger: str,
) -> dict[str, Any] | None:
    from cyrene.workbench import runtime as R

    packet = await R._workbench_run_reflection(session_id, focus=focus, goal_gap=goal_gap)
    if not packet:
        return None

    def apply(_payload: dict[str, Any], project: dict[str, Any], session: dict[str, Any]) -> None:
        R._workbench_store_reflection(session, packet, trigger=trigger, project=project)

    _write_session(session_id, apply)
    return packet


async def _generate_repair_steps(
    session: dict[str, Any],
    project: dict[str, Any],
    verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    from cyrene.workbench import runtime as R

    failed = [
        item
        for item in (verdict.get("results") or [])
        if isinstance(item, dict) and not bool(item.get("passed"))
    ]
    reflection = session.get("reflection") if isinstance(session.get("reflection"), dict) else {}
    packet = reflection.get("packet") if isinstance(reflection.get("packet"), dict) else {}
    workspace_path = str(project.get("workspacePath") or "").strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    prompt = (
        "你是持续任务的返工规划 Agent。当前计划已经执行完，但独立验收未通过。"
        "请检查工作区，并只生成修复这些失败项所需的新增步骤，不要重复已经完成且无关的步骤。\n\n"
        f"目标：{session.get('goal') or ''}\n"
        f"失败验收项：{_json_dumps(failed)}\n"
        f"深度反思：{_json_dumps(packet)}\n\n"
        '只返回 JSON：{"steps":[{"title":"修复步骤","description":"具体修改和验证方式"}]}。'
        "生成 1-5 个步骤，每步必须可执行并包含验证方式。"
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed = await R._workbench_run_explore_agent(
            workspace_root,
            prompt,
            max_tokens=5400,
            timeout=120,
            session_id=str(session.get("id") or ""),
            clean_context=True,
            raise_on_failure=True,
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
                R._workbench_new_plan_step(
                    title[:160],
                    str(raw.get("description") or "").strip()[:4000],
                    0,
                    str(session.get("id") or ""),
                )
            )
    if not steps:
        failed_text = "；".join(str(item.get("evidence") or item.get("id") or "") for item in failed)
        steps = [
            R._workbench_new_plan_step(
                "修复未通过的验收项",
                ("根据独立验收证据修复问题并重新验证。" + (f" 证据：{failed_text}" if failed_text else ""))[:4000],
                0,
                str(session.get("id") or ""),
            )
        ]
    return steps


class GoalLoopManager:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
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
                    # A hard crash can leave the document projection midway
                    # through a step even though no agent owns that execution
                    # anymore. Re-queue it so the recovered worker can inspect
                    # existing side effects and execute idempotently.
                    for step in session.get("plan") or []:
                        if not isinstance(step, dict) or str(step.get("status") or "") != "running":
                            continue
                        step["status"] = "pending"
                        step["startedAt"] = None
                        step.pop("currentAction", None)
                    session["status"] = "running"
                    session["goalLoop"] = _public_run(recovered)
                    session["agentReply"] = "检测到上次执行被中断，正在从已保存进度恢复。"

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
                        message="恢复持续执行时发现该任务已有其他运行，已安全暂停。",
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

    async def _run(self, run_id: str) -> None:
        from cyrene.workbench import runtime as R

        while not self.closed:
            run = await _get_run_by_id(self.db_path, run_id)
            if not run or str(run.get("status") or "") != "running":
                return
            run = await self._lease(run)
            if not run:
                return

            public = _public_run(run) or {}
            if int(public.get("activeSeconds") or 0) >= int(run.get("max_active_seconds") or 0):
                paused = await _set_inactive_status(
                    self.db_path,
                    run,
                    "paused",
                    phase="paused",
                    stop_reason="max_runtime",
                )
                if paused:
                    await _event(self.db_path, run_id, "runtime_limit_reached")
                    await self.sync_projection(paused, message="已达到最大运行时间，持续执行已暂停。")
                    append_notification(
                        title="持续执行已暂停",
                        body="任务达到最大运行时间，可调整限制后继续。",
                        tab="system",
                        source="goal_loop_paused",
                        source_label="持续执行",
                        meta={"sessionId": str(run["session_id"]), "runId": run_id},
                    )
                return

            try:
                _payload, project, session = _read_session(str(run["session_id"]))
            except KeyError:
                cancelled = await _set_inactive_status(
                    self.db_path, run, "cancelled", phase="cancelled", stop_reason="session_missing"
                )
                if cancelled:
                    await _publish(cancelled)
                return

            plan = session.get("plan") if isinstance(session.get("plan"), list) else []
            # A step tagged by an async answer is resumed first: the agent picks
            # up its suspended round from the user's reply instead of starting the
            # step from scratch (which would re-ask the same question).
            resume_step = next(
                (s for s in plan if isinstance(s, dict) and isinstance(s.get("goalLoopResumeAnswer"), dict)),
                None,
            )
            step = resume_step or _next_step(plan)
            if step is not None:
                step_id = str(step.get("id") or "")
                resume_answer = step.get("goalLoopResumeAnswer") if isinstance(step.get("goalLoopResumeAnswer"), dict) else None
                run = await _update_run(
                    self.db_path,
                    run_id,
                    phase="executing",
                    current_step_id=step_id,
                    lease_until=(_utc_now() + timedelta(minutes=10)).isoformat(),
                )
                if not run:
                    return

                def start_step(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
                    for candidate in fresh.get("plan") or []:
                        if isinstance(candidate, dict) and str(candidate.get("id") or "") == step_id:
                            candidate["status"] = "running"
                            candidate["startedAt"] = _utc_iso()
                            # One-shot: consume the answer tag so a retry can't
                            # re-trigger the resume.
                            candidate.pop("goalLoopResumeAnswer", None)
                            candidate["currentAction"] = (
                                "正在根据你的回复继续此步骤。" if resume_answer
                                else "持续执行模式正在处理此步骤。"
                            )
                    fresh["goalLoop"] = _public_run(run)
                    fresh["status"] = "running"

                _write_session(str(run["session_id"]), start_step)
                await _event(self.db_path, run_id, "step_started", step_id=step_id)
                await self.sync_projection(run, message=f"持续执行中：{step.get('title') or '当前步骤'}")

                _, current_project, current_session = _read_session(str(run["session_id"]))
                workspace_root = R._workbench_workspace_root(current_project)
                git_before = R._workbench_git_status_snapshot(workspace_root)
                workspace_files_before = R._workbench_workspace_file_snapshot(workspace_root)
                workspace_text_before = R._workbench_workspace_text_snapshot(workspace_root)
                started_at = _utc_iso()
                memory_pair = R._workbench_compose_memory_ephemeral(
                    current_project, current_session,
                )
                ephemeral = R._workbench_compose_ephemeral_system(
                    current_project, current_session,
                    step_id=step_id, workspace_root=workspace_root,
                    memory_pair=memory_pair,
                )
                volatile_ephemeral = R._workbench_compose_volatile_ephemeral_system(
                    current_project, current_session,
                    memory_pair=memory_pair,
                )
                # Run-invariant — rides in the cache-stable system prefix (static
                # extra), not the per-run ephemeral tail.
                loop_instruction = (
                    "\n\n## 持续执行模式\n"
                    "本次只是目标循环中的一个有界工作片段。完成当前步骤后可以调用 quit，"
                    "但整个目标是否完成由外部验收器决定。不要擅自结束目标循环。"
                )
                execution_error = ""
                try:
                    if resume_answer:
                        # Resume the agent's suspended round with the user's answer
                        # (background continuation of the same slice). Carry the
                        # loop's permission mode so a full_access / auto run keeps
                        # it across the resume instead of reverting to "default".
                        reply = await R._workbench_answer_pending(
                            str(run["session_id"]),
                            str(resume_answer.get("questionId") or ""),
                            str(resume_answer.get("answer") or ""),
                            await R._workbench_resolve_workspace_dir_async(current_project),
                            permission_mode=str(run.get("permission_mode") or "auto"),
                        )
                    else:
                        reply = await R._workbench_agent_reply(
                            _step_prompt(current_session, step),
                            current_session,
                            [],
                            permission_mode=str(run.get("permission_mode") or "auto"),
                            project_workspace=R._workbench_resolve_workspace_dir(current_project),
                            ephemeral_system=ephemeral,
                            volatile_ephemeral_system=volatile_ephemeral,
                            static_system_extra=R._workbench_compose_static_system(current_project, current_session) + loop_instruction,
                        )
                except asyncio.CancelledError:
                    raise
                except R._WorkbenchAgentRunError as exc:
                    if str(exc.code).startswith("budget_"):
                        current_run = await _get_run_by_id(self.db_path, run_id) or run
                        paused = await _set_inactive_status(
                            self.db_path,
                            current_run,
                            "paused",
                            phase="paused",
                            stop_reason=str(exc.code),
                            last_error=exc.message,
                        )
                        if paused:
                            await _event(
                                self.db_path,
                                run_id,
                                "budget_blocked",
                                step_id=step_id,
                                payload={"code": exc.code, "error": exc.message},
                            )
                            await self.sync_projection(
                                paused,
                                message=f"预算限制阻止了继续执行，持续任务已暂停：{exc.message}",
                            )
                        return
                    execution_error = exc.message
                    reply = exc.message
                except Exception as exc:
                    logger.exception("Goal-loop step execution failed")
                    execution_error = f"步骤执行失败：{exc}"
                    reply = execution_error

                latest_run = await _get_run_by_id(self.db_path, run_id)
                if not latest_run or str(latest_run.get("status") or "") != "running":
                    return
                _, latest_project, latest_session = _read_session(str(run["session_id"]))
                display_reply, awaiting = R._workbench_apply_pending(latest_session, str(run["session_id"]), reply)
                if awaiting or reply == _AWAITING_USER_SENTINEL:
                    def wait_for_user(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
                        fresh.update({
                            key: value
                            for key, value in latest_session.items()
                            if key in {"pendingQuestion", "status", "agentReply"}
                        })
                        fresh["pendingPlanStep"] = {"stepId": step_id, "goalLoop": True}
                        for candidate in fresh.get("plan") or []:
                            if isinstance(candidate, dict) and str(candidate.get("id") or "") == step_id:
                                candidate["status"] = "running"
                                candidate["currentAction"] = "等待用户确认后继续。"

                    _write_session(str(run["session_id"]), wait_for_user)
                    waiting = await _set_inactive_status(
                        self.db_path,
                        latest_run,
                        "waiting_for_user",
                        phase="waiting_for_user",
                        stop_reason="user_input",
                    )
                    if waiting:
                        await _event(self.db_path, run_id, "waiting_for_user", step_id=step_id)
                        await self.sync_projection(waiting, message=display_reply)
                    return

                # A step isn't finished while subagents it spawned are still
                # running. run_agent can return before its fire-and-forget
                # subagents settle (its own monitoring caps at ~60s, and a
                # spawn+quit in one turn skips monitoring entirely), which used
                # to let the loop mark a step "completed" while a subagent kept
                # working. Block until they settle so the file snapshots and
                # verification below see the finished work.
                from cyrene import subagent as _subagent

                last_lease = _utc_now()
                last_heartbeat = last_lease

                async def _keep_waiting() -> bool:
                    nonlocal last_lease, last_heartbeat
                    current = await _get_run_by_id(self.db_path, run_id)
                    if not current or str(current.get("status") or "") != "running":
                        return False
                    # Renew the 10-min lease well before it lapses so a peer
                    # worker can't steal the run during a long subagent wait.
                    if _utc_now() - last_lease >= timedelta(minutes=5):
                        if not await self._lease(current):
                            return False
                        last_lease = _utc_now()
                    now = _utc_now()
                    if now - last_heartbeat >= timedelta(seconds=_SUBAGENT_HEARTBEAT_SECONDS):
                        await _subagent.publish_active_heartbeat(
                            session_id=str(run["session_id"]),
                            message="仍在等待子代理完成，任务尚未停止。",
                        )
                        last_heartbeat = now
                    return True

                leftover = await _subagent.wait_until_settled(
                    session_id=str(run["session_id"]),
                    timeout=_SUBAGENT_SETTLE_TIMEOUT_SECONDS,
                    on_poll=_keep_waiting,
                )
                if leftover:
                    logger.warning(
                        "Goal-loop step %s proceeding with %d subagent(s) unsettled: %s",
                        step_id, len(leftover), leftover,
                    )
                    await _subagent.timeout_subagents(
                        leftover,
                        reason="子代理超过 5 分钟没有完成，已标记超时并停止等待。",
                    )
                latest_run = await _get_run_by_id(self.db_path, run_id)
                if not latest_run or str(latest_run.get("status") or "") != "running":
                    return

                git_after = R._workbench_git_status_snapshot(workspace_root)
                workspace_files_after = R._workbench_workspace_file_snapshot(workspace_root)
                workspace_text_after = R._workbench_workspace_text_snapshot(workspace_root)
                _, latest_project, latest_session = _read_session(str(run["session_id"]))
                if execution_error:
                    verification = {
                        "passed": False,
                        "evidence": execution_error,
                        "retry_guidance": "修复 Agent 执行错误后重新运行本步骤。",
                    }
                else:
                    try:
                        verification = await _verify_step(
                            latest_session,
                            latest_project,
                            step,
                            display_reply,
                        )
                    except Exception as exc:
                        paused = await _set_inactive_status(
                            self.db_path,
                            latest_run,
                            "paused",
                            phase="paused",
                            stop_reason="step_verification_unavailable",
                            last_error=str(exc),
                        )
                        if paused:
                            await _event(
                                self.db_path,
                                run_id,
                                "step_verification_unavailable",
                                step_id=step_id,
                                payload={"error": str(exc)},
                            )
                            await self.sync_projection(
                                paused,
                                message=f"步骤独立验收暂时不可用，持续执行已暂停：{exc}",
                            )
                        return
                activity_events = R._collect_run_activity_events(
                    str(run["session_id"]), started_at, R._short_id("run"), workspace_root
                )
                tool_events = [item for item in activity_events if item.get("type") == "ToolCallEvent"]
                step_prompt = _step_prompt(latest_session, step)
                file_changes = R._workbench_collect_run_file_changes(
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
                run_record = {
                    "id": R._short_id("run"),
                    "taskId": str(run["session_id"]),
                    "userInput": step_prompt,
                    "agentResponse": display_reply,
                    "status": "failed" if execution_error else "completed",
                    "startedAt": started_at,
                    "endedAt": _utc_iso(),
                    "events": activity_events,
                    "fileChanges": file_changes,
                    "toolCalls": [
                        {"tool": item["tool"], "argsPreview": item["argsPreview"]}
                        for item in tool_events
                    ],
                    "artifacts": [],
                    "attachments": [],
                    "mode": str(run.get("permission_mode") or "auto"),
                    "error": execution_error or None,
                    "goalLoopRunId": run_id,
                    "stepVerification": verification,
                }

                passed = bool(verification.get("passed"))
                step_attempts = 0

                # Generate step outcome before finish_step so it rides inside the
                # same _write_session instead of requiring a second write.
                outcome = None
                if passed and display_reply:
                    try:
                        outcome = await asyncio.wait_for(
                            R._workbench_generate_step_outcome(step, display_reply, step_prompt),
                            timeout=10,
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

                def finish_step(_p: dict[str, Any], project_obj: dict[str, Any], fresh: dict[str, Any]) -> None:
                    nonlocal step_attempts
                    fresh.setdefault("runs", []).append(run_record)
                    fresh.setdefault("events", []).extend(activity_events)
                    fresh["agentReply"] = display_reply
                    for candidate in fresh.get("plan") or []:
                        if not isinstance(candidate, dict) or str(candidate.get("id") or "") != step_id:
                            continue
                        candidate["updatedAt"] = _utc_iso()
                        candidate["toolCalls"] = run_record["toolCalls"]
                        candidate["stepVerification"] = verification
                        if passed:
                            candidate["status"] = "completed"
                            candidate["completedAt"] = _utc_iso()
                            candidate["currentAction"] = (
                                str((verification or {}).get("evidence") or "").strip()
                                or "步骤执行完成；最终目标将在全部步骤后独立验收。"
                            )
                            if outcome:
                                candidate["outcome"] = outcome
                        else:
                            candidate["status"] = "pending"
                            candidate["startedAt"] = None
                            step_attempts = int(candidate.get("goalLoopAttempts") or 0) + 1
                            candidate["goalLoopAttempts"] = step_attempts
                            candidate["currentAction"] = (
                                str((verification or {}).get("retry_guidance") or "").strip()
                                or str((verification or {}).get("evidence") or "").strip()
                                or "步骤验收未通过，请继续修复。"
                            )
                    R._workbench_apply_step_file_changes(fresh, step_id, file_changes)
                    R._workbench_promote_file_artifacts(fresh, file_changes, _utc_iso())
                    fresh["status"] = "running"
                    fresh["goalLoop"] = _public_run(latest_run)

                _write_session(str(run["session_id"]), finish_step)
                await _event(
                    self.db_path,
                    run_id,
                    "step_verified" if passed else "step_verification_failed",
                    step_id=step_id,
                    payload=verification or {"passed": True, "evidence": "final verification deferred"},
                )
                if not passed and step_attempts >= _STEP_FAILURE_CAP:
                    # The same step keeps failing independent verification. Reflect
                    # once for a root cause, then block rather than retry until the
                    # runtime budget runs out (and the cost with it).
                    reflecting = await _update_run(self.db_path, run_id, phase="reflecting", current_step_id=None)
                    if reflecting:
                        await self.sync_projection(reflecting, message="步骤反复未通过验收，正在深度思考失败根因。")
                    await _reflect(
                        str(run["session_id"]),
                        focus=str((verification or {}).get("retry_guidance") or f"步骤「{step.get('title') or ''}」反复未通过验收"),
                        goal_gap="同一步骤连续多次独立验收未通过，需要分析根因并改变方案，而不是继续机械重试。",
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
                        await _event(
                            self.db_path,
                            run_id,
                            "step_blocked",
                            step_id=step_id,
                            payload={"attempts": step_attempts},
                        )
                        await self.sync_projection(
                            blocked,
                            message=f"步骤「{step.get('title') or '当前步骤'}」连续 {step_attempts} 次未通过独立验收，持续执行已阻塞。请调整计划或目标后再继续。",
                        )
                        append_notification(
                            title="持续执行已阻塞",
                            body=f"步骤「{step.get('title') or '当前步骤'}」反复未通过验收，需要你介入。",
                            tab="system",
                            source="goal_loop_blocked",
                            source_label="持续执行",
                            meta={"sessionId": str(run["session_id"]), "runId": run_id},
                        )
                    return
                if passed and str(run.get("reflection_mode") or "") == "frequent":
                    await _update_run(self.db_path, run_id, phase="reflecting")
                    await self.sync_projection(
                        await _get_run_by_id(self.db_path, run_id) or run,
                        message="步骤完成，正在进行深度思考。",
                    )
                    await _reflect(
                        str(run["session_id"]),
                        focus=f"步骤「{step.get('title') or ''}」完成后的方向检查",
                        goal_gap="检查当前成果是否真正缩小了目标差距，以及后续计划是否需要调整。",
                        trigger="goal_loop_step",
                    )
                elif not passed and str(run.get("reflection_mode") or "") == "frequent":
                    await _reflect(
                        str(run["session_id"]),
                        focus=str((verification or {}).get("retry_guidance") or ""),
                        goal_gap="当前步骤独立验收未通过，需要分析根因并改变执行方式。",
                        trigger="goal_loop_step_failure",
                    )
                await _update_run(self.db_path, run_id, phase="executing", current_step_id=None)
                continue

            unresolved = [
                item
                for item in plan
                if isinstance(item, dict)
                and str(item.get("status") or "pending") not in {"completed", "done", "skipped"}
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
                    await self.sync_projection(blocked, message="没有可执行步骤，任务被计划依赖阻塞。")
                return

            reflection_mode = str(run.get("reflection_mode") or "proactive")
            if reflection_mode in {"proactive", "frequent"}:
                reflecting = await _update_run(self.db_path, run_id, phase="reflecting", current_step_id=None)
                if reflecting:
                    await self.sync_projection(reflecting, message="全部步骤已处理，正在最终验收前深度思考。")
                await _reflect(
                    str(run["session_id"]),
                    focus="最终验收前检查遗漏、假完成和表面满足",
                    goal_gap="全部计划步骤已执行，需要确认是否仍存在影响验收的目标差距。",
                    trigger="goal_loop_pre_verification",
                )

            verifying = await _update_run(self.db_path, run_id, phase="verifying", current_step_id=None)
            if not verifying:
                return
            await self.sync_projection(verifying, message="正在独立验收目标。")
            _, project, session = _read_session(str(run["session_id"]))
            try:
                verdict = await R._workbench_verify_acceptance(session, project)
            except Exception as exc:
                paused = await _set_inactive_status(
                    self.db_path,
                    verifying,
                    "paused",
                    phase="paused",
                    stop_reason="verification_unavailable",
                    last_error=str(exc),
                )
                if paused:
                    await _event(self.db_path, run_id, "verification_unavailable", payload={"error": str(exc)})
                    await self.sync_projection(paused, message=f"独立验收暂时不可用，持续执行已暂停：{exc}")
                return
            if not isinstance(verdict, dict):
                paused = await _set_inactive_status(
                    self.db_path,
                    verifying,
                    "paused",
                    phase="paused",
                    stop_reason="verification_unavailable",
                )
                if paused:
                    await self.sync_projection(paused, message="独立验收没有返回有效结果，持续执行已暂停。")
                return

            results = verdict.get("results") if isinstance(verdict.get("results"), list) else []
            by_id = {str(item.get("id") or ""): item for item in results if isinstance(item, dict)}
            any_failed = False
            acceptance_passed = False

            def apply_verdict(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
                nonlocal any_failed, acceptance_passed
                criteria = [item for item in (fresh.get("acceptanceCriteria") or []) if isinstance(item, dict)]
                for criterion in criteria:
                    result = by_id.get(str(criterion.get("id") or ""))
                    if not isinstance(result, dict):
                        criterion["status"] = "failed"
                        criterion["evidence"] = "验收器未返回这一项的结论。"
                        any_failed = True
                        continue
                    passed = bool(result.get("passed"))
                    criterion["status"] = "passed" if passed else "failed"
                    criterion["evidence"] = str(result.get("evidence") or "")
                    any_failed = any_failed or not passed
                fresh["acceptanceCriteria"] = criteria
                fresh["verifyReason"] = str(verdict.get("reason") or "")
                acceptance_passed = bool(criteria) and not any_failed
                if acceptance_passed:
                    R._workbench_mark_completed_if_acceptance_passed(
                        fresh,
                        event_body="持续执行独立验收通过，所有验收标准均已通过，任务自动标记为已完成。",
                    )

            _write_session(str(run["session_id"]), apply_verdict)
            await _event(self.db_path, run_id, "goal_verified", payload=verdict)

            if acceptance_passed:
                completed = await _set_inactive_status(
                    self.db_path,
                    verifying,
                    "completed",
                    phase="completed",
                    stop_reason="acceptance_passed",
                )
                if completed:
                    await self.sync_projection(completed, message="自动验收通过，任务已自动标记为已完成。")
                    try:
                        _, final_project, final_session = _read_session(str(run["session_id"]))
                        await R._workbench_archive_run_knowledge(
                            final_project,
                            final_session,
                            {
                                "id": run_id,
                                "userInput": str(final_session.get("goal") or ""),
                                "agentResponse": str(final_session.get("agentReply") or ""),
                            },
                            R._workbench_workspace_root(final_project),
                            _utc_iso(),
                        )
                    except Exception:
                        logger.exception("Goal-loop final artifact archive failed")
                    append_notification(
                        title="持续执行验收通过",
                        body=f"任务「{session.get('title') or '未命名任务'}」已通过自动验收。",
                        tab="comment",
                        project_ref=project.get("id"),
                        source="goal_loop_passed",
                        source_label="持续执行",
                        link_label=str(session.get("title") or ""),
                        meta={"sessionId": str(run["session_id"]), "runId": run_id},
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
                    await self.sync_projection(paused, message="已达到最大返工轮数，持续执行已暂停。")
                    append_notification(
                        title="持续执行已暂停",
                        body="任务达到最大返工轮数，可调整限制后继续。",
                        tab="system",
                        source="goal_loop_paused",
                        source_label="持续执行",
                        meta={"sessionId": str(run["session_id"]), "runId": run_id},
                    )
                return

            reflecting = await _update_run(self.db_path, run_id, phase="reflecting")
            if reflecting:
                await self.sync_projection(reflecting, message="验收未通过，正在深度思考失败原因。")
            await _reflect(
                str(run["session_id"]),
                focus=str(verdict.get("reason") or "验收未通过"),
                goal_gap="独立验收未通过，需要分析失败根因并生成新的返工路径。",
                trigger="goal_loop_verification_failure",
            )
            _, project, session = _read_session(str(run["session_id"]))
            repair_steps = await _generate_repair_steps(session, project, verdict)

            def append_repairs(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
                plan_items = [item for item in (fresh.get("plan") or []) if isinstance(item, dict)]
                base_order = len(plan_items)
                for index, repair in enumerate(repair_steps, 1):
                    repair["order"] = base_order + index
                    repair["goalLoopRepairRound"] = repair_round + 1
                    plan_items.append(repair)
                fresh["plan"] = plan_items
                fresh["planRevision"] = int(fresh.get("planRevision") or 0) + 1
                fresh["planDefinitionRevision"] = int(fresh.get("planDefinitionRevision") or 0) + 1
                fresh["approvedPlanDefinitionRevision"] = fresh["planDefinitionRevision"]
                for criterion in fresh.get("acceptanceCriteria") or []:
                    if isinstance(criterion, dict):
                        criterion["status"] = "pending"
                        criterion.pop("evidence", None)
                fresh["status"] = "running"
                fresh["agentReply"] = f"验收未通过，已生成第 {repair_round + 1} 轮返工步骤。"

            _, _, updated_session = _write_session(str(run["session_id"]), append_repairs)
            repaired = await _update_run(
                self.db_path,
                run_id,
                phase="repairing",
                repair_round=repair_round + 1,
                plan_definition_revision=int(updated_session.get("planDefinitionRevision") or 0),
            )
            await _event(
                self.db_path,
                run_id,
                "repair_planned",
                payload={"repairRound": repair_round + 1, "stepCount": len(repair_steps)},
            )
            if repaired:
                await self.sync_projection(repaired)


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
                step["status"] = "running"
                step["currentAction"] = "已收到你的回复，正在继续执行此步骤。"
        fresh["status"] = "running"
        fresh["agentReply"] = "已收到你的回复，持续执行将在后台继续。"
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
                    message="任务已有其他运行，持续执行已安全暂停。",
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
                    message="权限请求已拒绝，持续执行已暂停。",
                )
            else:
                def apply(
                    _payload: dict[str, Any],
                    _project: dict[str, Any],
                    session: dict[str, Any],
                ) -> None:
                    session["status"] = "paused"
                    session["goalLoop"] = _public_run(paused)
                    session["agentReply"] = "权限请求已拒绝，持续执行已暂停。"

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
                    message="任务已有其他运行，持续执行已安全暂停。",
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

"""Durable, server-side goal loop for Workbench task sessions.

The normal agent loop is intentionally bounded to one request.  This module
adds a harness-owned loop around those bounded slices:

    execute one plan step -> verify it -> continue -> verify the whole goal
    -> reflect/repair on failure -> repeat within user-configured limits.

SQLite is the execution source of truth. ``workbench_projects.json`` remains the
UI projection so existing Workbench rendering and task history keep working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import aiosqlite
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene import debug
from cyrene.agent import _AWAITING_USER_SENTINEL, interrupt_active_run
from webui.workbench_notifications import append_notification

logger = logging.getLogger(__name__)

_RUNNING_STATUSES = {"running"}
_RESUMABLE_STATUSES = {"paused", "blocked"}
_TERMINAL_STATUSES = {"review", "completed", "cancelled"}
_REFLECTION_MODES = {"standard", "proactive", "frequent"}
_PERMISSION_MODES = {"auto", "full_access"}
# A single step that fails independent verification this many times in a row is
# treated as stuck: the loop reflects once, then blocks instead of retrying the
# same step until the runtime budget is burned.
_STEP_FAILURE_CAP = 3
_SQLITE_TIMEOUT_SECONDS = 15
_MANAGERS: dict[str, "GoalLoopManager"] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return fallback
    return parsed


async def _ensure_schema(db_path: str) -> None:
    async with aiosqlite.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {_SQLITE_TIMEOUT_SECONDS * 1000}")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS goal_loop_drafts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                base_plan_revision INTEGER NOT NULL,
                goal TEXT NOT NULL,
                goal_changed INTEGER NOT NULL DEFAULT 0,
                plan_json TEXT NOT NULL,
                acceptance_json TEXT NOT NULL,
                limits_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_session ON goal_loop_drafts(session_id);
            CREATE INDEX IF NOT EXISTS idx_goal_loop_drafts_expires ON goal_loop_drafts(expires_at);
            CREATE TABLE IF NOT EXISTS goal_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                phase TEXT NOT NULL DEFAULT 'executing',
                plan_definition_revision INTEGER NOT NULL,
                current_step_id TEXT,
                permission_mode TEXT NOT NULL DEFAULT 'auto',
                reflection_mode TEXT NOT NULL DEFAULT 'proactive',
                max_active_seconds INTEGER NOT NULL,
                max_repair_rounds INTEGER NOT NULL,
                active_seconds REAL NOT NULL DEFAULT 0,
                active_started_at TEXT,
                repair_round INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_until TEXT,
                stop_reason TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_runs_status ON goal_runs(status);
            CREATE INDEX IF NOT EXISTS idx_goal_runs_lease ON goal_runs(lease_until);
            CREATE TABLE IF NOT EXISTS goal_run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                step_id TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goal_run_events_run ON goal_run_events(run_id);
            """
        )
        await db.commit()


async def _fetch_one(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    await _ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {_SQLITE_TIMEOUT_SECONDS * 1000}")
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, args)
        row = await cursor.fetchone()
        return dict(row) if row is not None else None


async def _fetch_all(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    await _ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {_SQLITE_TIMEOUT_SECONDS * 1000}")
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, args)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def _execute(db_path: str, sql: str, args: tuple[Any, ...] = ()) -> int:
    await _ensure_schema(db_path)
    async with aiosqlite.connect(db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as db:
        await db.execute(f"PRAGMA busy_timeout = {_SQLITE_TIMEOUT_SECONDS * 1000}")
        cursor = await db.execute(sql, args)
        await db.commit()
        return int(cursor.rowcount or 0)


def _sqlite_storage_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower()
        for marker in ("database is locked", "database table is locked", "database is busy")
    )


def _storage_busy_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": "任务存储正被其他操作占用，请等待相关测试或任务结束后重试。",
            "code": "goal_loop_storage_busy",
        },
        status_code=503,
    )


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


def _validate_limits(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    goal = str(payload.get("goal") or "").strip()
    if len(goal) < 3:
        return None, "目标至少需要 3 个字符。"
    try:
        max_hours = float(payload.get("maxRuntimeHours"))
    except (TypeError, ValueError):
        return None, "最大运行时间必须是数字。"
    if max_hours < 0.5 or max_hours > 24:
        return None, "最大运行时间必须在 0.5 到 24 小时之间。"
    try:
        max_repairs = int(payload.get("maxRepairRounds"))
    except (TypeError, ValueError):
        return None, "最大返工轮数必须是整数。"
    if max_repairs < 0 or max_repairs > 10:
        return None, "最大返工轮数必须在 0 到 10 之间。"
    permission_mode = str(payload.get("permissionMode") or "auto").strip()
    if permission_mode not in _PERMISSION_MODES:
        return None, "权限模式无效。"
    if permission_mode == "full_access" and not bool(payload.get("fullAccessConfirmed")):
        return None, "使用完全访问前必须确认风险。"
    reflection_mode = str(payload.get("reflectionMode") or "proactive").strip()
    if reflection_mode not in _REFLECTION_MODES:
        return None, "深度思考强度无效。"
    return {
        "goal": goal,
        "maxRuntimeHours": max_hours,
        "maxActiveSeconds": int(max_hours * 3600),
        "maxRepairRounds": max_repairs,
        "permissionMode": permission_mode,
        "reflectionMode": reflection_mode,
    }, ""


def _read_session(session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from webui import routes as R

    payload = R._read_workbench_store()
    project, session = R._workbench_find_session(payload, session_id)
    if not project or not session:
        raise KeyError("session not found")
    return payload, project, session


def _write_session(
    session_id: str,
    mutator: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from webui import routes as R

    with R._WORKBENCH_STORE_LOCK:
        payload = R._read_workbench_store()
        project, session = R._workbench_find_session(payload, session_id)
        if not project or not session:
            raise KeyError("session not found")
        mutator(payload, project, session)
        now = R._utc_now_iso()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeProjectId"] = project.get("id")
        payload["activeSessionId"] = session_id
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
    return all(statuses.get(str(dep)) in {"completed", "done", "skipped"} for dep in (step.get("dependsOn") or []))


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
) -> dict[str, Any] | None:
    from webui import routes as R

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
            max_tokens=800,
            timeout=90,
            session_id=str(session.get("id") or ""),
            clean_context=True,
            raise_on_failure=True,
        )
    except Exception:
        logger.warning("Goal-loop step verification unavailable", exc_info=True)
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("passed"), bool):
        return None
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
    from webui import routes as R

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
    from webui import routes as R

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
            max_tokens=1800,
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
        self.closed = False

    async def startup(self) -> None:
        await _ensure_schema(self.db_path)
        rows = await _fetch_all(
            self.db_path,
            "SELECT * FROM goal_runs WHERE status = 'running'",
        )
        for row in rows:
            await _update_run(
                self.db_path,
                str(row["id"]),
                phase="recovering",
                active_started_at=_utc_iso(),
                lease_owner=None,
                lease_until=None,
            )
            self.wake(str(row["id"]))

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
                phase="recovering",
                active_seconds=active_seconds,
                active_started_at=None,
                lease_owner=None,
                lease_until=None,
            )
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def wake(self, run_id: str) -> None:
        if self.closed:
            return
        current = self.tasks.get(run_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._run(run_id))
        self.tasks[run_id] = task

        def done(completed: asyncio.Task[Any]) -> None:
            self.tasks.pop(run_id, None)
            try:
                completed.exception()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Goal-loop worker failed", exc_info=True)

        task.add_done_callback(done)

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

    async def _sync_projection(self, run: dict[str, Any], *, message: str = "") -> None:
        public = _public_run(run) or {}

        def apply(_payload: dict[str, Any], _project: dict[str, Any], session: dict[str, Any]) -> None:
            session["goalLoop"] = public
            if message:
                session["agentReply"] = message
            status = str(run.get("status") or "")
            if status in {"running", "waiting_for_user", "paused", "blocked", "review", "cancelled"}:
                session["status"] = status

        try:
            _write_session(str(run["session_id"]), apply)
        except KeyError:
            return
        await _publish(run)

    async def _run(self, run_id: str) -> None:
        from webui import routes as R

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
                    await self._sync_projection(paused, message="已达到最大运行时间，持续执行已暂停。")
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
                await self._sync_projection(run, message=f"持续执行中：{step.get('title') or '当前步骤'}")

                _, current_project, current_session = _read_session(str(run["session_id"]))
                workspace_root = R._workbench_workspace_root(current_project)
                git_before = R._workbench_git_status_snapshot(workspace_root)
                started_at = _utc_iso()
                ephemeral = R._workbench_compose_ephemeral_system(current_project, current_session)
                loop_instruction = (
                    "\n\n## 持续执行模式\n"
                    "本次只是目标循环中的一个有界工作片段。完成当前步骤后可以调用 quit，"
                    "但整个目标是否完成由外部验收器决定。不要擅自结束目标循环。"
                )
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
                            R._workbench_resolve_workspace_dir(current_project),
                            permission_mode=str(run.get("permission_mode") or "auto"),
                        )
                    else:
                        reply = await R._workbench_agent_reply(
                            _step_prompt(current_session, step),
                            current_session,
                            [],
                            permission_mode=str(run.get("permission_mode") or "auto"),
                            project_workspace=str(current_project.get("workspacePath") or ""),
                            ephemeral_system=ephemeral + loop_instruction,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Goal-loop step execution failed")
                    reply = f"步骤执行失败：{exc}"

                latest_run = await _get_run_by_id(self.db_path, run_id)
                if not latest_run or str(latest_run.get("status") or "") != "running":
                    return
                git_after = R._workbench_git_status_snapshot(workspace_root)
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
                        await self._sync_projection(waiting, message=display_reply)
                    return

                verification = await _verify_step(latest_session, latest_project, step, display_reply)
                activity_events = R._collect_run_activity_events(
                    str(run["session_id"]), started_at, R._short_id("run"), workspace_root
                )
                tool_events = [item for item in activity_events if item.get("type") == "ToolCallEvent"]
                file_changes = R._workbench_merge_file_changes(
                    [
                        *[change for event in tool_events for change in (event.get("fileChanges") or [])],
                        *R._workbench_git_status_delta(git_before, git_after, workspace_root),
                    ]
                )
                run_record = {
                    "id": R._short_id("run"),
                    "taskId": str(run["session_id"]),
                    "userInput": _step_prompt(latest_session, step),
                    "agentResponse": display_reply,
                    "status": "completed",
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
                    "error": None,
                    "goalLoopRunId": run_id,
                    "stepVerification": verification,
                }

                passed = verification is None or bool(verification.get("passed"))
                step_attempts = 0

                def finish_step(_p: dict[str, Any], project_obj: dict[str, Any], fresh: dict[str, Any]) -> None:
                    nonlocal step_attempts
                    fresh.setdefault("runs", []).append(run_record)
                    fresh.setdefault("events", []).extend(activity_events)
                    fresh["agentReply"] = display_reply
                    for candidate in fresh.get("plan") or []:
                        if not isinstance(candidate, dict) or str(candidate.get("id") or "") != step_id:
                            continue
                        candidate["updatedAt"] = _utc_iso()
                        candidate["relatedFiles"] = file_changes
                        candidate["toolCalls"] = run_record["toolCalls"]
                        candidate["stepVerification"] = verification
                        if passed:
                            candidate["status"] = "completed"
                            candidate["completedAt"] = _utc_iso()
                            candidate["currentAction"] = (
                                str((verification or {}).get("evidence") or "").strip()
                                or "步骤执行完成；最终目标将在全部步骤后独立验收。"
                            )
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
                        await self._sync_projection(reflecting, message="步骤反复未通过验收，正在深度思考失败根因。")
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
                        await self._sync_projection(
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
                    await self._sync_projection(
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
                    await self._sync_projection(blocked, message="没有可执行步骤，任务被计划依赖阻塞。")
                return

            reflection_mode = str(run.get("reflection_mode") or "proactive")
            if reflection_mode in {"proactive", "frequent"}:
                reflecting = await _update_run(self.db_path, run_id, phase="reflecting", current_step_id=None)
                if reflecting:
                    await self._sync_projection(reflecting, message="全部步骤已处理，正在最终验收前深度思考。")
                await _reflect(
                    str(run["session_id"]),
                    focus="最终验收前检查遗漏、假完成和表面满足",
                    goal_gap="全部计划步骤已执行，需要确认是否仍存在影响验收的目标差距。",
                    trigger="goal_loop_pre_verification",
                )

            verifying = await _update_run(self.db_path, run_id, phase="verifying", current_step_id=None)
            if not verifying:
                return
            await self._sync_projection(verifying, message="正在独立验收目标。")
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
                    await self._sync_projection(paused, message=f"独立验收暂时不可用，持续执行已暂停：{exc}")
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
                    await self._sync_projection(paused, message="独立验收没有返回有效结果，持续执行已暂停。")
                return

            results = verdict.get("results") if isinstance(verdict.get("results"), list) else []
            by_id = {str(item.get("id") or ""): item for item in results if isinstance(item, dict)}
            any_failed = False

            def apply_verdict(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
                nonlocal any_failed
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

            _write_session(str(run["session_id"]), apply_verdict)
            await _event(self.db_path, run_id, "goal_verified", payload=verdict)

            if not any_failed:
                completed = await _set_inactive_status(
                    self.db_path,
                    verifying,
                    "review",
                    phase="review",
                    stop_reason="acceptance_passed",
                )
                if completed:
                    await self._sync_projection(completed, message="自动验收通过，持续执行已停止，等待你的最终确认。")
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
                    await self._sync_projection(paused, message="已达到最大返工轮数，持续执行已暂停。")
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
                await self._sync_projection(reflecting, message="验收未通过，正在深度思考失败原因。")
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
                await self._sync_projection(repaired)


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
        manager.wake(str(run["id"]))
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
        manager.wake(str(run["id"]))
    await _publish(run)


def register_goal_loop_routes(router: APIRouter, app: Any, db_path: str) -> GoalLoopManager:
    manager = GoalLoopManager(str(db_path))
    _MANAGERS[str(db_path)] = manager

    @app.on_event("startup")
    async def _goal_loop_startup() -> None:
        await manager.startup()

    @app.on_event("shutdown")
    async def _goal_loop_shutdown() -> None:
        await manager.shutdown()

    @router.post("/api/task-sessions/{session_id}/goal-loop/preview")
    async def preview_goal_loop(session_id: str, request: Request):
        from webui import routes as R

        body = await request.json()
        limits, error = _validate_limits(body)
        if not limits:
            return JSONResponse({"error": error}, status_code=400)
        try:
            _payload, project, session = _read_session(session_id)
        except KeyError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        if str(session.get("status") or "") != "planning":
            return JSONResponse({"error": "只有计划确认阶段可以启动持续执行。", "code": "invalid_status"}, status_code=409)
        try:
            base_revision = int(body.get("basePlanDefinitionRevision"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid basePlanDefinitionRevision"}, status_code=400)
        if base_revision != int(session.get("planDefinitionRevision") or 0):
            return JSONResponse({"error": "计划已发生变化，请重新打开配置。", "code": "stale_plan_revision"}, status_code=409)
        try:
            current_run = await _get_run_by_session(db_path, session_id)
            # Check draft storage before an expensive planning-agent call. This
            # also clears expired rows while the database is known to be writable.
            await _execute(
                db_path,
                "DELETE FROM goal_loop_drafts WHERE expires_at < ?",
                (_utc_iso(),),
            )
        except Exception as exc:
            if not _sqlite_storage_busy(exc):
                raise
            logger.warning("Goal-loop preview storage is busy for session %s", session_id)
            return _storage_busy_response()
        if current_run and str(current_run.get("status") or "") not in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "该任务已有持续执行实例。", "code": "goal_loop_exists"}, status_code=409)

        goal = str(limits["goal"])
        goal_changed = goal.strip() != str(session.get("goal") or "").strip()
        draft_session = json.loads(_json_dumps(session))
        draft_session["goal"] = goal
        if goal_changed:
            draft_session["plan"] = []
            draft_session["acceptanceCriteria"] = []
            plan, acceptance, from_llm, _operation = await R._workbench_generate_plan_steps(
                draft_session,
                project,
                feedback="目标已由用户在持续执行配置中更新，请基于新目标重新生成完整计划。",
                requested_operation="replace",
            )
        else:
            plan = json.loads(_json_dumps(session.get("plan") or []))
            draft_session["plan"] = plan
            acceptance, from_llm = await R._workbench_generate_acceptance_criteria(draft_session, project)
        if not plan:
            return JSONResponse({"error": "无法生成可执行计划。"}, status_code=503)
        if not acceptance:
            return JSONResponse({"error": "无法生成验收条件。"}, status_code=503)

        draft_id = f"goal_draft_{uuid.uuid4().hex[:16]}"
        now = _utc_now()
        expires_at = now + timedelta(minutes=30)
        try:
            await _execute(
                db_path,
                """
                INSERT INTO goal_loop_drafts
                (id, session_id, project_id, base_plan_revision, goal, goal_changed,
                 plan_json, acceptance_json, limits_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    session_id,
                    str(project.get("id") or ""),
                    base_revision,
                    goal,
                    1 if goal_changed else 0,
                    _json_dumps(plan),
                    _json_dumps(acceptance),
                    _json_dumps(limits),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        except Exception as exc:
            if not _sqlite_storage_busy(exc):
                raise
            logger.warning(
                "Goal-loop preview could not persist draft for session %s",
                session_id,
            )
            return _storage_busy_response()
        return {
            "ok": True,
            "draftId": draft_id,
            "goalChanged": goal_changed,
            "goal": goal,
            "plan": plan,
            "acceptanceCriteria": acceptance,
            "limits": limits,
            "planSource": "llm" if from_llm else "fallback",
            "expiresAt": expires_at.isoformat(),
        }

    @router.post("/api/task-sessions/{session_id}/goal-loop/start")
    async def start_goal_loop(session_id: str, request: Request):
        body = await request.json()
        draft_id = str(body.get("draftId") or "").strip()
        draft = await _fetch_one(
            db_path,
            "SELECT * FROM goal_loop_drafts WHERE id = ? AND session_id = ?",
            (draft_id, session_id),
        )
        if not draft:
            return JSONResponse({"error": "目标配置草稿不存在或已过期。", "code": "draft_not_found"}, status_code=404)
        try:
            if datetime.fromisoformat(str(draft["expires_at"])) <= _utc_now():
                return JSONResponse({"error": "目标配置草稿已过期，请重新生成。", "code": "draft_expired"}, status_code=409)
        except ValueError:
            return JSONResponse({"error": "目标配置草稿无效。"}, status_code=409)
        limits = _json_loads(draft.get("limits_json"), {})
        plan = _json_loads(draft.get("plan_json"), [])
        acceptance = _json_loads(draft.get("acceptance_json"), [])
        try:
            _payload, project, session = _read_session(session_id)
        except KeyError:
            return JSONResponse({"error": "session not found"}, status_code=404)
        base_revision = int(draft.get("base_plan_revision") or 0)
        if base_revision != int(session.get("planDefinitionRevision") or 0):
            return JSONResponse({"error": "计划已发生变化，请重新生成目标配置。", "code": "stale_plan_revision"}, status_code=409)
        existing = await _get_run_by_session(db_path, session_id)
        if existing and str(existing.get("status") or "") not in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "该任务已有持续执行实例。", "code": "goal_loop_exists"}, status_code=409)
        if existing:
            await _execute(db_path, "DELETE FROM goal_runs WHERE id = ?", (str(existing["id"]),))

        next_revision = base_revision + (1 if bool(draft.get("goal_changed")) else 0)
        run_id = f"goal_run_{uuid.uuid4().hex[:16]}"
        now = _utc_iso()
        await _execute(
            db_path,
            """
            INSERT INTO goal_runs
            (id, session_id, project_id, objective, status, phase,
             plan_definition_revision, current_step_id, permission_mode,
             reflection_mode, max_active_seconds, max_repair_rounds,
             active_seconds, active_started_at, repair_round,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, 'running', 'executing', ?, NULL, ?, ?, ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                run_id,
                session_id,
                str(project.get("id") or ""),
                str(draft.get("goal") or ""),
                next_revision,
                str(limits.get("permissionMode") or "auto"),
                str(limits.get("reflectionMode") or "proactive"),
                int(limits.get("maxActiveSeconds") or 7200),
                int(limits.get("maxRepairRounds") or 3),
                now,
                now,
                now,
            ),
        )
        run = await _get_run_by_id(db_path, run_id)

        def apply(_payload: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
            fresh["goal"] = str(draft.get("goal") or "")
            fresh["plan"] = plan
            fresh["acceptanceCriteria"] = acceptance
            fresh["status"] = "running"
            fresh["planRevision"] = int(fresh.get("planRevision") or 0) + 1
            fresh["planDefinitionRevision"] = next_revision
            fresh["approvedPlanDefinitionRevision"] = next_revision
            fresh["goalLoop"] = _public_run(run)
            fresh["agentReply"] = "持续执行已启动，Agent 将执行计划并循环返工直到验收通过或达到退出条件。"
            fresh.setdefault("events", []).append({
                "id": R._short_id("event"),
                "type": "GoalLoopStarted",
                "createdAt": now,
                "body": "用户确认启动持续执行到验收通过。",
            })

        from webui import routes as R

        payload, project, session = _write_session(session_id, apply)
        await _execute(db_path, "DELETE FROM goal_loop_drafts WHERE id = ?", (draft_id,))
        await _event(db_path, run_id, "started", payload={"limits": limits})
        if run:
            await _publish(run)
        manager.wake(run_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(run), **payload}

    @router.get("/api/task-sessions/{session_id}/goal-loop")
    async def get_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run:
            return {"ok": True, "goalLoop": None}
        events = await _fetch_all(
            db_path,
            "SELECT * FROM goal_run_events WHERE run_id = ? ORDER BY id DESC LIMIT 100",
            (str(run["id"]),),
        )
        return {
            "ok": True,
            "goalLoop": _public_run(run),
            "events": [
                {
                    "id": item["id"],
                    "type": item["event_type"],
                    "stepId": item.get("step_id") or "",
                    "payload": _json_loads(item.get("payload_json"), {}),
                    "createdAt": item.get("created_at") or "",
                }
                for item in reversed(events)
            ],
        }

    @router.post("/api/task-sessions/{session_id}/goal-loop/pause")
    async def pause_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") != "running":
            return JSONResponse({"error": "没有正在运行的持续任务。"}, status_code=409)
        interrupt_active_run(session_id=session_id)
        paused = await _set_inactive_status(db_path, run, "paused", phase="paused", stop_reason="user_paused")
        if paused:
            await _event(db_path, str(run["id"]), "paused")
            await manager._sync_projection(paused, message="持续执行已暂停，当前进度已保留。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(paused), **payload}

    @router.post("/api/task-sessions/{session_id}/goal-loop/resume")
    async def resume_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") not in _RESUMABLE_STATUSES:
            return JSONResponse({"error": "当前持续任务不能恢复。"}, status_code=409)
        now = _utc_iso()
        resumed = await _update_run(
            db_path,
            str(run["id"]),
            status="running",
            phase="executing",
            active_started_at=now,
            stop_reason=None,
            last_error=None,
            lease_owner=None,
            lease_until=None,
        )

        def apply(_p: dict[str, Any], _project: dict[str, Any], fresh: dict[str, Any]) -> None:
            for step in fresh.get("plan") or []:
                if not isinstance(step, dict):
                    continue
                if str(step.get("status") or "") == "running":
                    step["status"] = "pending"
                    step["startedAt"] = None
                # Give every not-yet-finished step a fresh per-step failure budget
                # so a resume after a stuck-step block is not blocked again at once.
                if str(step.get("status") or "") not in {"completed", "done", "skipped"}:
                    step["goalLoopAttempts"] = 0
            fresh["status"] = "running"
            fresh["goalLoop"] = _public_run(resumed)
            fresh["agentReply"] = "持续执行已恢复。"

        payload, project, session = _write_session(session_id, apply)
        if resumed:
            await _event(db_path, str(run["id"]), "resumed")
            await _publish(resumed)
            manager.wake(str(run["id"]))
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(resumed), **payload}

    @router.post("/api/task-sessions/{session_id}/goal-loop/cancel")
    async def cancel_goal_loop(session_id: str):
        run = await _get_run_by_session(db_path, session_id)
        if not run or str(run.get("status") or "") in _TERMINAL_STATUSES | {"cancelled"}:
            return JSONResponse({"error": "没有可取消的持续任务。"}, status_code=409)
        interrupt_active_run(session_id=session_id)
        cancelled = await _set_inactive_status(
            db_path, run, "cancelled", phase="cancelled", stop_reason="user_cancelled"
        )
        if cancelled:
            await _event(db_path, str(run["id"]), "cancelled")
            await manager._sync_projection(cancelled, message="持续执行已取消，当前进度和文件改动已保留。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(cancelled), **payload}

    @router.patch("/api/task-sessions/{session_id}/goal-loop/limits")
    async def update_goal_loop_limits(session_id: str, request: Request):
        body = await request.json()
        run = await _get_run_by_session(db_path, session_id)
        if not run:
            return JSONResponse({"error": "持续任务不存在。"}, status_code=404)
        try:
            max_hours = float(body.get("maxRuntimeHours", int(run["max_active_seconds"]) / 3600))
            max_repairs = int(body.get("maxRepairRounds", run["max_repair_rounds"]))
        except (TypeError, ValueError):
            return JSONResponse({"error": "退出条件格式无效。"}, status_code=400)
        if max_hours < 0.5 or max_hours > 24 or max_repairs < 0 or max_repairs > 10:
            return JSONResponse({"error": "退出条件超出允许范围。"}, status_code=400)
        reflection_mode = str(body.get("reflectionMode") or run.get("reflection_mode") or "proactive")
        if reflection_mode not in _REFLECTION_MODES:
            return JSONResponse({"error": "深度思考强度无效。"}, status_code=400)
        updated = await _update_run(
            db_path,
            str(run["id"]),
            max_active_seconds=int(max_hours * 3600),
            max_repair_rounds=max_repairs,
            reflection_mode=reflection_mode,
        )
        if updated:
            await manager._sync_projection(updated, message="持续执行限制已更新。")
        payload, project, session = _read_session(session_id)
        return {"ok": True, "project": project, "session": session, "goalLoop": _public_run(updated), **payload}

    return manager


__all__ = ["GoalLoopManager", "register_goal_loop_routes", "resume_after_answer", "begin_async_answer"]

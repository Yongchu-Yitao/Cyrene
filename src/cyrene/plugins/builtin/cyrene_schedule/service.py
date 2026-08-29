"""Storage, execution, and delivery service owned by the schedule Plugin pack."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyrene.core.plugin import PluginContext, default_plugin_impl_directory
from cyrene.config import WORKSPACE_DIR
from cyrene.localization import app_language, localized

from .migrations import initialize_schedule_database
from .repository import ScheduledTask, ScheduleRepository, TaskTimeTotals

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduleScope:
    project_id: str
    session_id: str
    chat_id: int
    source: str


class _ScheduledWorkbenchRun:
    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id)

    async def publish(self, _event: dict[str, Any]) -> None:
        return None


class ScheduleRuntimeService:
    """Narrow runtime used by this pack's tools and application routes."""

    def __init__(
        self,
        db_path: str,
        *,
        bot: Any = None,
        plugin_directory: str | Path | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.bot = bot
        self.plugin_directory = Path(
            plugin_directory or default_plugin_impl_directory()
        ).expanduser().resolve()
        self.repository = ScheduleRepository(self.db_path)
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._active_runs: dict[str, asyncio.Task[Any]] = {}

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            await initialize_schedule_database(self.db_path)
            self._ready = True

    async def create_reminder(
        self,
        *,
        chat_id: int,
        prompt: str,
        due_at: str,
        project_id: str,
        origin_session_id: str = "",
    ) -> str:
        """Create a one-shot reminder through the active Schedule service."""

        await self.ensure_ready()
        return await self.repository.create(
            chat_id=chat_id,
            prompt=prompt,
            schedule_type="once",
            schedule_value=due_at,
            next_run=due_at,
            project_id=project_id,
            origin_session_id=origin_session_id,
            action_type="agent_task",
        )

    async def reminder(self, task_id: str) -> ScheduledTask | None:
        await self.ensure_ready()
        return await self.repository.get(task_id)

    async def edit_reminder(
        self,
        task_id: str,
        *,
        prompt: str,
        due_at: str,
    ) -> bool:
        await self.ensure_ready()
        return await self.repository.edit(
            task_id,
            {
                "prompt": prompt,
                "schedule_type": "once",
                "schedule_value": due_at,
                "next_run": due_at,
            },
        )

    async def set_reminder_status(self, task_id: str, status: str) -> bool:
        await self.ensure_ready()
        return await self.repository.update_status(task_id, status)

    async def delete_reminder(self, task_id: str) -> bool:
        await self.ensure_ready()
        return await self.repository.delete(task_id)

    async def time_totals(self) -> TaskTimeTotals:
        await self.ensure_ready()
        return await self.repository.time_totals()

    def scope(self, context: PluginContext) -> ScheduleScope:
        data = context.data
        session_id = str(data.get("session_id") or context.tree_id or "").strip()
        project_id = str(data.get("project_id") or "").strip()
        if not project_id and session_id:
            from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

            project_id = str(resolve_workbench_project_id_for_session(session_id) or "")
        try:
            chat_id = int(data.get("chat_id", -1))
        except (TypeError, ValueError):
            chat_id = -1
        return ScheduleScope(
            project_id=project_id,
            session_id=session_id,
            chat_id=chat_id,
            source=str(data.get("source") or "agent").strip() or "agent",
        )

    @staticmethod
    def workspace_for_project(project_ref: str) -> Path:
        from cyrene.workbench.sessions.context import read_projects

        requested = str(project_ref or "").strip()
        for project in read_projects():
            if not isinstance(project, dict):
                continue
            if requested not in {
                str(project.get("id") or "").strip(),
                str(project.get("dataKey") or "").strip(),
            }:
                continue
            workspace = str(project.get("workspacePath") or "").strip()
            if workspace:
                return Path(workspace).expanduser().resolve()
        return Path(WORKSPACE_DIR).expanduser().resolve()

    async def cancel_active(
        self, task_id: str, reason: str = "schedule cancelled"
    ) -> bool:
        running = self._active_runs.get(str(task_id))
        if running is None or running.done():
            return False
        running.cancel(str(reason))
        if running is not asyncio.current_task():
            await asyncio.gather(running, return_exceptions=True)
        return True

    async def delete_project(self, project_id: str) -> int:
        await self.ensure_ready()
        tasks = await self.repository.list(str(project_id or "default"))
        if tasks:
            await asyncio.gather(
                *(
                    self.cancel_active(task.id, "schedule project deleted")
                    for task in tasks
                ),
                return_exceptions=True,
            )
        return await self.repository.delete_project(str(project_id or "default"))

    async def run_agent(self, task: ScheduledTask, run_id: str) -> str:
        from cyrene.workbench.core_adapter.chat_runtime import run_workbench_chat
        from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_data_key

        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("scheduled Agent execution requires an asyncio task")
        self._active_runs[task.id] = current
        public_project_id = (
            resolve_workbench_project_id_for_data_key(task.project_id) or task.project_id
        )
        session_id = f"schedule_{task.id}_{run_id}"
        language = app_language()
        system_extra = localized(
            "This is an autonomous scheduled execution. Complete the requested work now "
            "with the available tools. Do not pause for clarification, wait for a reply, "
            "or send progress messages. If the requested output itself contains questions "
            "for the user, include them in the final result. Return one concise final "
            "result for delivery by the scheduler. If the task cannot be completed, state "
            "the concrete blocker.",
            "这是一次自主执行的定时任务。请立即使用可用工具完成请求，不要暂停以澄清问题、"
            "等待回复或发送进度消息。如果请求的产出本身包含需要用户回答的问题，请将其写入"
            "最终结果。只返回一份简洁的最终结果，供调度器投递；如果无法完成，请说明具体阻塞项。",
            language=language,
        )
        memory_snapshot = None
        from cyrene.core.plugin import application_plugin_service

        memory_service = application_plugin_service("memory")
        snapshot_loader = getattr(memory_service, "current_snapshot", None)
        if callable(snapshot_loader):
            loaded = await asyncio.to_thread(
                snapshot_loader,
                str(public_project_id or ""),
            )
            if isinstance(loaded, dict):
                memory_snapshot = dict(loaded)
        try:
            result = await run_workbench_chat(
                run=_ScheduledWorkbenchRun(run_id),
                user_message=task.prompt,
                bot=None,
                host_chat_id=-1,
                db_path=self.db_path,
                session_id=session_id,
                workspace_dir=str(self.workspace_for_project(task.project_id)),
                client_request_id=run_id,
                permission_mode=task.permission_mode,
                public_user_message=task.prompt,
                soul_enabled=True,
                workspace_enabled=True,
                system_extra=system_extra,
                project_id=str(public_project_id or ""),
                project_memory_snapshot=memory_snapshot,
                session_title=localized(
                    "Scheduled task {task_id}",
                    "定时任务 {task_id}",
                    language=language,
                    task_id=task.id,
                ),
                memory_write_enabled=False,
                memory_trigger_enabled=False,
                memory_archive_enabled=False,
                conversation_source="scheduled_task",
                plugin_directory=self.plugin_directory,
            )
            return result.text
        finally:
            if self._active_runs.get(task.id) is current:
                self._active_runs.pop(task.id, None)

    async def deliver(
        self,
        task: ScheduledTask,
        text: str,
        *,
        run_id: str,
        error: bool = False,
    ) -> dict[str, Any]:
        language = app_language()
        body = str(text or "").strip()
        if not body:
            body = (
                localized(
                    "The scheduled task failed.",
                    "定时任务执行失败。",
                    language=language,
                )
                if error
                else task.prompt
            )
        result: dict[str, Any] = {"workbench": False, "bot": False}
        try:
            from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_data_key
            from .projection import create_scheduled_chat

            public_project_id = (
                resolve_workbench_project_id_for_data_key(task.project_id)
                or task.project_id
            )
            projected = await create_scheduled_chat(
                self.db_path,
                public_project_id,
                body,
                chat_id=f"wbschedule_{run_id[:24]}",
                source_chat_id=task.origin_session_id,
                lang=language,
            )
            result["workbench"] = projected is not None
            if projected:
                result["chat_id"] = projected.get("chat_id")
        except Exception:
            logger.exception("Failed to project scheduled run %s into Workbench", run_id)

        if self.bot is not None and task.chat_id >= 0:
            try:
                await self.bot.send_message(chat_id=task.chat_id, text=body)
                result["bot"] = True
            except Exception:
                logger.exception("Failed to deliver scheduled run %s through bot", run_id)

        try:
            from cyrene.runtime.notifications import notify
            from cyrene.workbench.application.notifications import append_notification

            title = (
                localized(
                    "Scheduled task failed",
                    "定时任务失败",
                    language=language,
                )
                if error
                else localized(
                    "Scheduled task completed",
                    "定时任务完成",
                    language=language,
                )
            )
            summary = body if len(body) <= 160 else body[:160] + "…"
            await notify(title, summary, channel="auto")
            append_notification(
                title=title,
                body=summary,
                tab="system",
                project_ref=task.project_id,
                source="scheduled_task_run",
                source_label=localized("Schedule", "日程", language=language),
                link_label=localized("Schedule", "日程", language=language),
                meta={
                    "taskId": task.id,
                    "runId": run_id,
                    "status": "error" if error else "success",
                    "chatId": result.get("chat_id", ""),
                    "actionType": task.action_type,
                },
                language=language,
            )
            result["notified"] = True
        except Exception:
            logger.exception("Failed to notify scheduled run %s", run_id)
            result["notified"] = False
        return result


__all__ = ["ScheduleRuntimeService", "ScheduleScope"]

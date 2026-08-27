"""Narrow host services available to the editable schedule Plugin pack."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.plugin import PluginContext, default_plugin_impl_directory

from cyrene.config import WORKSPACE_DIR
from cyrene.runtime.persistence.migrations import initialize_runtime_database
from cyrene.runtime.persistence.scheduler import ScheduledTask, SchedulerRepository
from cyrene.runtime.persistence.schema import RUNTIME_SCHEMA

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
        # Scheduled runs are projected once, as one durable result chat, after
        # the Agent has reached a terminal response. Kernel events remain in
        # the Agent tree for recovery and diagnostics.
        return None


class ScheduleRuntimeService:
    """Storage, Agent execution, cancellation, and result delivery only.

    Schedule validation and lifecycle policy deliberately live in the seeded,
    user-editable ``cyrene_schedule`` Plugin pack.
    """

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
        self.repository = SchedulerRepository(self.db_path)
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._active_runs: dict[str, asyncio.Task[Any]] = {}

    def configure(
        self,
        *,
        bot: Any = None,
        plugin_directory: str | Path | None = None,
    ) -> None:
        if bot is not None:
            self.bot = bot
        if plugin_directory is not None:
            self.plugin_directory = Path(plugin_directory).expanduser().resolve()

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            await initialize_runtime_database(self.db_path, RUNTIME_SCHEMA)
            self._ready = True

    def scope(self, context: PluginContext) -> ScheduleScope:
        data = context.data
        session_id = str(data.get("session_id") or context.tree_id or "").strip()
        project_id = str(data.get("project_id") or "").strip()
        if not project_id and session_id:
            from cyrene.workbench.context import resolve_workbench_project_id_for_session

            project_id = str(resolve_workbench_project_id_for_session(session_id) or "")
        raw_chat_id = data.get("chat_id", -1)
        try:
            chat_id = int(raw_chat_id)
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
        from cyrene.workbench.context import read_projects

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

    async def cancel_active(self, task_id: str, reason: str = "schedule cancelled") -> bool:
        running = self._active_runs.get(str(task_id))
        if running is None or running.done():
            return False
        running.cancel(str(reason))
        if running is not asyncio.current_task():
            await asyncio.gather(running, return_exceptions=True)
        return True

    async def delete_project(self, project_id: str) -> int:
        """Cancel live occurrences before removing every schedule in a project."""

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
        """Run or recover the stable Agent tree for one scheduled occurrence."""

        from agent.workbench.chat_runtime import run_workbench_chat
        from cyrene.workbench.context import resolve_workbench_project_id_for_data_key

        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("scheduled Agent execution requires an asyncio task")
        self._active_runs[task.id] = current
        public_project_id = (
            resolve_workbench_project_id_for_data_key(task.project_id) or task.project_id
        )
        session_id = f"schedule_{task.id}_{run_id}"
        system_extra = (
            "This is an autonomous scheduled execution. Complete the requested work now "
            "with the available tools. Do not pause for clarification, wait for a reply, "
            "or send progress messages. If the requested output itself contains questions "
            "for the user, include them in the final result. Return one concise final "
            "result for delivery by the scheduler. If the task cannot be completed, state "
            "the concrete blocker."
        )
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
                session_title=f"Scheduled task {task.id}",
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
        """Project one idempotent result into Workbench and notification channels."""

        body = str(text or "").strip()
        if not body:
            body = "定时任务执行失败。" if error else task.prompt
        result: dict[str, Any] = {"workbench": False, "bot": False}
        try:
            from cyrene.workbench.context import resolve_workbench_project_id_for_data_key
            from cyrene.workbench.proactive_chat_service import create_proactive_chat

            public_project_id = (
                resolve_workbench_project_id_for_data_key(task.project_id) or task.project_id
            )
            projected = await create_proactive_chat(
                self.db_path,
                public_project_id,
                body,
                chat_id=f"wbschedule_{run_id[:24]}",
                source_chat_id=task.origin_session_id,
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
            from cyrene.workbench.notifications import append_notification

            title = "定时任务失败" if error else "定时任务完成"
            summary = body if len(body) <= 160 else body[:160] + "…"
            await notify(title, summary, channel="auto")
            append_notification(
                title=title,
                body=summary,
                tab="system",
                project_ref=task.project_id,
                source="scheduled_task_run",
                source_label="日程",
                link_label="日程",
                meta={
                    "taskId": task.id,
                    "runId": run_id,
                    "status": "error" if error else "success",
                    "chatId": result.get("chat_id", ""),
                    "actionType": task.action_type,
                },
            )
            result["notified"] = True
        except Exception:
            logger.exception("Failed to notify scheduled run %s", run_id)
            result["notified"] = False
        return result


_SERVICES: dict[str, ScheduleRuntimeService] = {}


def get_schedule_runtime(
    db_path: str,
    *,
    bot: Any = None,
    plugin_directory: str | Path | None = None,
) -> ScheduleRuntimeService:
    key = str(Path(db_path).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = ScheduleRuntimeService(
            key,
            bot=bot,
            plugin_directory=plugin_directory,
        )
        _SERVICES[key] = service
    else:
        service.configure(bot=bot, plugin_directory=plugin_directory)
    return service


__all__ = [
    "ScheduleRuntimeService",
    "ScheduleScope",
    "get_schedule_runtime",
]

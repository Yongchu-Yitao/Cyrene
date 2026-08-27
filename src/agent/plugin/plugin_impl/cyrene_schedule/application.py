"""Workbench routes and services owned by the editable schedule pack."""

from __future__ import annotations

import asyncio

from agent.plugin import PluginApplicationContext


def setup_application(context: PluginApplicationContext) -> None:
    from cyrene.workbench.context import configure_store, read_projects
    from cyrene.workbench.notifications import append_notification
    from cyrene.workbench.schedule_repository import WorkspaceProjectResolver
    from cyrene.workbench.schedule_service import ScheduleApplicationService
    from route.workbench.schedule import register_workbench_schedule_routes

    configure_store(context.db_path)

    def find_project(project_id: str):
        target = str(project_id or "").strip()
        if not target:
            return None
        return next(
            (
                dict(project)
                for project in read_projects()
                if isinstance(project, dict)
                and str(project.get("id") or "").strip() == target
            ),
            None,
        )

    application = ScheduleApplicationService(
        context.db_path,
        WorkspaceProjectResolver(
            find_project_lightweight=find_project,
            read_projects=read_projects,
        ),
        append_notification,
        entities=context.services.get("entities"),
        bot=context.bot,
        plugin_directory=context.plugin_directory,
    )
    register_workbench_schedule_routes(
        context.router,
        application_service=application,
    )
    context.provide("schedules", application.gateway.service)
    context.provide("schedule_application", application)

    async def search(query: str, limit: int):
        needle = " ".join(str(query or "").casefold().split())
        projects = [item for item in read_projects() if isinstance(item, dict)]
        scopes = [
            (
                str(project.get("id") or ""),
                str(project.get("name") or "Workspace"),
                application.workspace_resolver.resolve(
                    str(project.get("id") or project.get("dataKey") or "default")
                ),
            )
            for project in projects
        ] or [("", "Workspace", "default")]
        task_groups, entity_groups = await asyncio.gather(
            asyncio.gather(
                *(
                    application.tasks_for_project(scope)
                    for _project, _name, scope in scopes
                )
            ),
            asyncio.gather(
                *(
                    application.entities.list(
                        has_due_date=True,
                        project_id=scope,
                        limit=500,
                    )
                    for _project, _name, scope in scopes
                )
            ),
        )
        results = []
        for (project_id, project_name, _scope), tasks, entities in zip(
            scopes,
            task_groups,
            entity_groups,
        ):
            for task in tasks:
                prompt = str(task.get("prompt") or "")
                if needle and needle not in " ".join(prompt.casefold().split()):
                    continue
                results.append(
                    {
                        "id": str(task.get("id") or ""),
                        "type": "schedule",
                        "title": prompt or "Scheduled task",
                        "snippet": prompt[:160],
                        "projectId": project_id,
                        "projectName": project_name,
                        "taskId": str(task.get("id") or ""),
                        "scheduleType": task.get("schedule_type") or "once",
                        "scheduleValue": task.get("schedule_value") or "",
                        "nextRun": task.get("next_run") or "",
                        "category": (
                            "task_once"
                            if task.get("schedule_type") == "once"
                            else "task_recurring"
                        ),
                    }
                )
                if len(results) >= max(1, int(limit or 10)):
                    return results
            for entity in entities:
                title = str(entity.get("title") or "")
                content = str(entity.get("content") or "")
                searchable = " ".join(f"{title} {content}".casefold().split())
                if needle and needle not in searchable:
                    continue
                results.append(
                    {
                        "id": str(entity.get("id") or ""),
                        "type": "schedule",
                        "title": title or "Event",
                        "snippet": (content or title)[:160],
                        "projectId": project_id,
                        "projectName": project_name,
                        "entityId": str(entity.get("id") or ""),
                        "dueDate": entity.get("due_date") or "",
                        "category": "entity_due",
                    }
                )
                if len(results) >= max(1, int(limit or 10)):
                    return results
        return results

    context.provide_search("schedule", search)
    context.expose_frontend("schedule")


__all__ = ["setup_application"]

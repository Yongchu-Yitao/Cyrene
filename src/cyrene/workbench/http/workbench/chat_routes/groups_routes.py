from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from cyrene.workbench.chat import chat_groups
from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


async def _replace_chat_groups(context: ChatRouteContext, body: dict[str, Any]):
    project_id = str(body.get("projectId") or "").strip()
    if not await asyncio.to_thread(context.runtime().find_project_lightweight, project_id):
        return localized_error_response(
            "Project not found.", "未找到项目。", 404, "project_not_found"
        )
    try:
        return await chat_groups.replace_project_groups(
            project_id,
            body.get("groups") if isinstance(body.get("groups"), list) else [],
            base_groups=(body.get("baseGroups") if isinstance(body.get("baseGroups"), list) else None),
            mutation_intent=(body.get("intent") if isinstance(body.get("intent"), dict) else None),
        )
    except ValueError:
        logger.warning("Invalid chat-group replacement request", exc_info=True)
        return localized_error_response(
            "The chat group configuration is invalid.",
            "对话群组配置无效。",
            400,
            "invalid_chat_groups",
        )
    except Exception:
        logger.exception("Failed to persist chat groups for project %s", project_id)
        return localized_error_response(
            "Chat groups could not be saved.",
            "无法保存对话群组。",
            500,
            "chat_group_persistence_failed",
        )


def _register_group_list_route(router: APIRouter, context: ChatRouteContext) -> None:
    @router.get("/api/workbench/chat-groups")
    async def api_workbench_chat_groups(project: str = ""):
        project_id = str(project or "").strip()
        if not project_id:
            return localized_error_response(
                "A project is required.", "请选择项目。", 400, "project_required"
            )
        if not await asyncio.to_thread(context.runtime().find_project_lightweight, project_id):
            return localized_error_response(
                "Project not found.", "未找到项目。", 404, "project_not_found"
            )
        return await asyncio.to_thread(chat_groups.get_project_groups, project_id)


def _register_group_write_routes(router: APIRouter, context: ChatRouteContext) -> None:
    @router.put("/api/workbench/chat-groups")
    async def api_workbench_replace_chat_groups(body_model: api_models.ChatGroupsReplaceBody):
        return await _replace_chat_groups(context, api_models.body_dict(body_model))

    @router.post("/api/workbench/chat-groups/migrate")
    async def api_workbench_migrate_chat_groups(body_model: api_models.ChatGroupsReplaceBody):
        """Idempotently import the browser-owned projection from older releases."""
        body = api_models.body_dict(body_model)
        project_id = str(body.get("projectId") or "").strip()
        existing = await asyncio.to_thread(chat_groups.get_project_groups, project_id)
        if not existing.get("migrationRequired"):
            return existing
        return await _replace_chat_groups(context, body)


def _register_group_metadata_route(router: APIRouter, context: ChatRouteContext) -> None:
    @router.post("/api/workbench/chat-groups/metadata")
    async def api_workbench_chat_group_metadata(body_model: api_models.ChatGroupMetadataBody):
        body = api_models.body_dict(body_model)
        project_id = str(body.get("projectId") or "").strip()
        group_id = str(body.get("groupId") or "").strip()
        signature = str(body.get("signature") or "")
        metadata_context = None
        if project_id:
            try:
                metadata_context = await asyncio.to_thread(
                    chat_groups.get_group_metadata_context,
                    project_id,
                    group_id,
                    signature=signature,
                )
            except LookupError:
                logger.info("Chat group metadata target was not found", exc_info=True)
                return localized_error_response(
                    "Chat group not found.",
                    "未找到对话群组。",
                    404,
                    "chat_group_not_found",
                )
            except RuntimeError:
                logger.info("Chat group metadata context is stale", exc_info=True)
                return localized_error_response(
                    "The chat group changed. Refresh it and try again.",
                    "对话群组已发生变化，请刷新后重试。",
                    409,
                    "chat_group_conflict",
                )
        try:
            metadata = await context.service.generate_chat_group_metadata(
                metadata_context["members"] if metadata_context else body.get("members") if isinstance(body.get("members"), list) else [],
                lang=str(body.get("lang") or ""),
                title_locked=(bool(metadata_context["group"].get("titleLocked")) if metadata_context else bool(body.get("titleLocked"))),
                current_title=(str(metadata_context["group"].get("title") or "") if metadata_context else str(body.get("currentTitle") or "")),
            )
        except ValueError:
            logger.warning("Invalid chat-group metadata request", exc_info=True)
            return localized_error_response(
                "The chat group metadata request is invalid.",
                "对话群组元数据请求无效。",
                400,
                "invalid_chat_group_metadata",
            )
        except Exception:
            logger.exception("Failed to generate chat group metadata")
            return localized_error_response(
                "Chat group metadata could not be generated.",
                "无法生成对话群组元数据。",
                502,
                "chat_group_metadata_generation_failed",
            )
        persisted_group = None
        if metadata_context:
            try:
                persisted = await chat_groups.update_group_metadata(
                    project_id,
                    group_id,
                    signature=metadata_context["signature"],
                    metadata=metadata,
                )
            except (LookupError, RuntimeError):
                logger.info("Chat group changed while saving metadata", exc_info=True)
                return localized_error_response(
                    "The chat group changed. Refresh it and try again.",
                    "对话群组已发生变化，请刷新后重试。",
                    409,
                    "chat_group_conflict",
                )
            persisted_group = next(
                (item for item in persisted.get("groups", []) if str(item.get("id") or "") == group_id),
                None,
            )
        return {"ok": True, "groupId": group_id, "metadata": metadata, "group": persisted_group}


def register_groups_routes(router: APIRouter, context: ChatRouteContext) -> None:
    _register_group_list_route(router, context)
    _register_group_write_routes(router, context)
    _register_group_metadata_route(router, context)

"""HTTP adapters for the versioned Workbench project-memory prompt."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench import chat as chat_service
from cyrene.workbench import memory as structured_memory
from cyrene.workbench import project_memory_prompt as memory_prompt
from cyrene.workbench.compat import runtime_service
from route import schemas as api_models
from route.errors import error_response


def register_project_memory_routes(router: APIRouter, db_path: str = "") -> None:
    memory_prompt.configure_store(db_path)

    async def _project_exists(project_id: str) -> bool:
        runtime = runtime_service()
        return bool(await asyncio.to_thread(runtime._workbench_find_project_lightweight, project_id))

    @router.get("/api/projects/{project_id}/memory-prompt")
    async def get_project_memory_prompt(project_id: str, include_memories: bool = True):
        if not await _project_exists(project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        try:
            payload = await asyncio.to_thread(memory_prompt.get_project_memory_prompt, project_id)
            if include_memories:
                memories = await asyncio.to_thread(
                    structured_memory.build_memory_payload,
                    project_id,
                    include_hidden=True,
                )
                payload["memories"] = memories.get("memories") or []
            return payload
        except Exception:  # noqa: BLE001
            memory_prompt.logger.exception("Failed to read project-memory prompt for %s", project_id)
            return error_response("Memory prompt load failed", 500, "memory_prompt_load_failed")

    @router.patch("/api/projects/{project_id}/memory-prompt")
    async def update_project_memory_prompt(
        project_id: str,
        body_model: api_models.ProjectMemoryPromptUpdateBody,
    ):
        if not await _project_exists(project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        body = api_models.body_dict(body_model)
        try:
            payload, changed = await asyncio.to_thread(
                memory_prompt.update_project_memory_prompt,
                project_id,
                str(body.get("prompt") or ""),
                base_modified_at=str(body.get("baseModifiedAt") or ""),
            )
            return {**payload, "status": "saved" if changed else "unchanged"}
        except memory_prompt.ProjectMemoryConflict as exc:
            return JSONResponse(
                {"error": str(exc), "code": "optimistic_conflict"},
                status_code=409,
            )
        except memory_prompt.InvalidProjectMemoryOutput as exc:
            return JSONResponse({"error": str(exc), "code": "invalid_prompt"}, status_code=400)
        except Exception:  # noqa: BLE001
            memory_prompt.logger.exception("Failed to edit project-memory prompt for %s", project_id)
            return error_response("Memory prompt update failed", 500, "memory_prompt_update_failed")

    @router.post("/api/projects/{project_id}/memory-prompt/restore")
    async def restore_project_memory_prompt(
        project_id: str,
        body_model: api_models.ProjectMemoryPromptRestoreBody,
    ):
        if not await _project_exists(project_id):
            return JSONResponse({"error": "project not found"}, status_code=404)
        body = api_models.body_dict(body_model)
        try:
            payload, changed = await asyncio.to_thread(
                memory_prompt.restore_project_memory_prompt,
                project_id,
                str(body.get("modifiedAt") or ""),
                base_modified_at=str(body.get("baseModifiedAt") or ""),
            )
            return {**payload, "status": "saved" if changed else "unchanged"}
        except KeyError:
            return JSONResponse({"error": "memory version not found"}, status_code=404)
        except memory_prompt.ProjectMemoryConflict as exc:
            return JSONResponse(
                {"error": str(exc), "code": "optimistic_conflict"},
                status_code=409,
            )
        except Exception:  # noqa: BLE001
            memory_prompt.logger.exception("Failed to restore project-memory prompt for %s", project_id)
            return error_response("Memory prompt restore failed", 500, "memory_prompt_restore_failed")

    @router.post("/api/workbench/chats/{chat_id}/memory-learning")
    async def trigger_chat_memory_learning(chat_id: str):
        if str(chat_id or "").startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chats do not have an exact model-context snapshot", "code": "no_completed_context"},
                status_code=409,
            )
        payload = await asyncio.to_thread(chat_service._read_chats_store)
        chat = chat_service._find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if str(chat.get("kind") or "chat") != "chat":
            return JSONResponse({"error": "only root conversations can generate project memory"}, status_code=400)
        project_id = str(chat.get("projectId") or "")
        result = memory_prompt.schedule_learning_from_completed_chat(
            project_id,
            chat_id,
            source="conversation_menu",
            reason="manual_menu",
        )
        if result.get("status") == "error":
            status = 409 if result.get("type") == "no_completed_context" else 400
            return JSONResponse(
                {"error": result.get("message"), "code": result.get("type")},
                status_code=status,
            )
        return JSONResponse(result, status_code=202 if result.get("status") == "queued" else 200)


__all__ = ["register_project_memory_routes"]

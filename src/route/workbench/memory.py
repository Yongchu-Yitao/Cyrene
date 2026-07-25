"""FastAPI adapters for workspace-scoped Workbench memory."""

# Service symbols are bound below so the HTTP layer does not own memory
# persistence behavior.
# ruff: noqa: F821

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from route import schemas as api_models
from route.errors import error_response
from cyrene.workbench import memory as _service

globals().update({
    name: value
    for name, value in vars(_service).items()
    if not name.startswith("__")
})


def register_workbench_memory_routes(router: APIRouter, db_path: str = "") -> None:
    """Register workspace-scoped memory routes for the Workbench UI."""
    if db_path:
        configure_store(db_path)

    @router.get("/api/workbench/memory")
    async def wb_list_memory(workspace: str = "default"):
        try:
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to list Workbench memory for %s", workspace)
            return error_response("List failed", 500, "memory_list_failed")

    @router.post("/api/workbench/memory")
    async def wb_create_memory(
        body_model: api_models.MemoryCreateBody, workspace: str = "default"
    ):
        body = api_models.body_dict(body_model)

        content = str(body.get("content") or "").strip()
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)

        category = str(body.get("category") or "").strip().lower()
        if category not in _CATEGORY_LABELS:
            category = "fact"
        source = str(body.get("source") or "manual").strip().lower()
        if source not in _SOURCE_LABELS:
            source = "manual"
        confidence = str(body.get("confidence") or "").strip().lower()

        today = _today()
        entry: dict[str, Any] = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "content": content,
            # Keep ``type`` in sync with category for any legacy reader.
            "type": category,
            "category": category,
            "source": source,
            "tags": _normalize_tags(body.get("tags")),
            "first_seen": today,
            "last_mentioned": today,
            "mention_count": 1,
            "emotional_valence": 0,
        }
        if confidence in _CONFIDENCE_LABELS:
            entry["confidence"] = confidence

        try:
            entries = _load(workspace)
            _append_history(entry, "created")
            entries.append(entry)
            _save(workspace, entries)
            payload = _build_payload(workspace)
            payload["id"] = entry["id"]
            return payload
        except Exception:  # noqa: BLE001
            logger.exception("Failed to create Workbench memory for %s", workspace)
            return error_response("Create failed", 500, "memory_create_failed")

    @router.patch("/api/workbench/memory/{mem_id}")
    async def wb_update_memory(
        mem_id: str,
        body_model: api_models.MemoryUpdateBody,
        workspace: str = "default",
    ):
        body = api_models.body_dict(body_model)

        try:
            entries = _load(workspace)
            target = None
            for e in entries:
                if _entry_id(e) == mem_id:
                    target = e
                    break
            if target is None:
                return JSONResponse({"error": "memory not found"}, status_code=404)

            # Persist the resolved id so future edits stay stable even after the
            # content (and thus its content-hash fallback id) changes.
            target["id"] = mem_id

            if "content" in body:
                content = str(body.get("content") or "").strip()
                if not content:
                    return JSONResponse({"error": "content cannot be empty"}, status_code=400)
                target["content"] = content
            if "category" in body:
                cat = str(body.get("category") or "").strip().lower()
                if cat in _CATEGORY_LABELS:
                    target["category"] = cat
                    target["type"] = cat
            if "source" in body:
                src = str(body.get("source") or "").strip().lower()
                if src in _SOURCE_LABELS:
                    target["source"] = src
            if "confidence" in body:
                conf = str(body.get("confidence") or "").strip().lower()
                if conf in _CONFIDENCE_LABELS:
                    target["confidence"] = conf
                else:
                    target.pop("confidence", None)
            if "tags" in body:
                target["tags"] = _normalize_tags(body.get("tags"))
            if "stale" in body:
                # Retire (or revive) a memory: stale entries stay on the page but
                # are no longer injected into agent runs.
                new_stale = bool(body.get("stale"))
                old_stale = bool(target.get("stale"))
                target["stale"] = new_stale
                if new_stale and not old_stale:
                    _append_history(target, "stale")
                elif not new_stale and old_stale:
                    _append_history(target, "revived")
            # An edit counts as a fresh touch — drives the "更新时间".
            target["last_mentioned"] = _today()
            if any(k in body for k in ("content", "category", "source", "confidence", "tags")):
                _append_history(target, "edited")

            _save(workspace, entries)
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to update Workbench memory %s for %s", mem_id, workspace
            )
            return error_response("Update failed", 500, "memory_update_failed")

    @router.delete("/api/workbench/memory/{mem_id}")
    async def wb_delete_memory(mem_id: str, workspace: str = "default"):
        try:
            entries = _load(workspace)
            kept = [e for e in entries if _entry_id(e) != mem_id]
            if len(kept) == len(entries):
                return JSONResponse({"error": "memory not found"}, status_code=404)
            _save(
                workspace,
                kept,
                base_value=getattr(entries, "_workbench_base", entries),
            )
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to delete Workbench memory %s for %s", mem_id, workspace
            )
            return error_response("Delete failed", 500, "memory_delete_failed")


__all__ = ["register_workbench_memory_routes"]

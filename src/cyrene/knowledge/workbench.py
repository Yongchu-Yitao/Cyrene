"""Archive Workbench run results and produced files into project knowledge."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from cyrene.runtime.attachments import (
    EXPORTS_DIR,
    attachment_kind_from_meta,
    register_generated_attachment,
)
from cyrene.config import get_knowledge_db_path
from cyrene.runtime.database import init_knowledge_db
from cyrene.knowledge import ingest, store


def _safe_id(value: Any, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return cleaned or fallback


def _render_run_markdown(
    *,
    title: str,
    goal: str,
    user_input: str,
    agent_response: str,
    file_changes: list[dict[str, Any]],
) -> str:
    lines = [f"# {title or 'Workbench task result'}"]
    if goal:
        lines.extend(["", "## Goal", "", goal])
    if user_input:
        lines.extend(["", "## Request", "", user_input])
    lines.extend(["", "## Result", "", agent_response])
    paths = [
        str(item.get("path") or "").strip()
        for item in file_changes
        if isinstance(item, dict)
        and str(item.get("path") or "").strip()
        and str(item.get("status") or "").lower() != "deleted"
    ]
    if paths:
        lines.extend(["", "## Produced files", ""])
        lines.extend(f"- `{path}`" for path in paths)
    return "\n".join(lines).strip() + "\n"


async def archive_workbench_run(
    *,
    data_key: str,
    session_id: str,
    run_id: str,
    title: str,
    goal: str,
    user_input: str,
    agent_response: str,
    file_changes: list[dict[str, Any]] | None = None,
    workspace_root: str | Path | None = None,
    include_summary: bool = True,
) -> list[dict[str, Any]]:
    """Persist a completed run summary and/or its readable files in project knowledge."""
    response = str(agent_response or "").strip()
    changes = file_changes if isinstance(file_changes, list) else []
    if not response and not changes:
        return []

    db_path = str(get_knowledge_db_path(data_key or "default"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_knowledge_db(db_path)

    documents: list[dict[str, Any]] = []
    if include_summary and response:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        run_key = _safe_id(run_id, "run")
        summary_path = EXPORTS_DIR / f"workbench_task_{run_key}.md"
        summary_text = _render_run_markdown(
            title=str(title or "").strip(),
            goal=str(goal or "").strip(),
            user_input=str(user_input or "").strip(),
            agent_response=response,
            file_changes=changes,
        )
        summary_path.write_text(summary_text, encoding="utf-8")
        summary_doc = await store.upsert_document_by_path(
            db_path,
            path=str(summary_path.resolve()),
            source="workbench_task",
            name=f"{str(title or 'Workbench task').strip()}.md",
            title=str(title or "Workbench task result").strip(),
            content_type="text/markdown",
            kind="code",
            size=summary_path.stat().st_size,
            tags=["workbench", "task-result"],
            metadata={"session_id": session_id, "run_id": run_id},
            content_hash=store.content_hash_file(summary_path),
        )
        if summary_doc.get("status") in {"pending", "error"}:
            await ingest.index_document(db_path, summary_doc["id"])
        documents.append(summary_doc)

    root = Path(workspace_root).resolve() if workspace_root else None
    seen_paths: set[str] = set()
    for item in changes:
        if not isinstance(item, dict) or str(item.get("status") or "").lower() == "deleted":
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path or raw_path in seen_paths:
            continue
        seen_paths.add(raw_path)
        source_path = Path(raw_path)
        if not source_path.is_absolute() and root is not None:
            source_path = root / source_path
        try:
            source_path = source_path.resolve()
        except OSError:
            continue
        if root is not None and source_path != root and root not in source_path.parents:
            continue
        if not source_path.exists() or not source_path.is_file():
            continue

        registered = register_generated_attachment(str(source_path), display_name=source_path.name)
        exported = Path(str(registered.get("path") or ""))
        if not exported.exists():
            continue
        content_type = str(
            registered.get("content_type")
            or mimetypes.guess_type(str(exported))[0]
            or "application/octet-stream"
        )
        kind = attachment_kind_from_meta(content_type, exported.name)
        doc = await store.upsert_document_by_path(
            db_path,
            path=str(exported.resolve()),
            source="workbench_artifact",
            name=source_path.name,
            title=source_path.stem,
            content_type=content_type,
            kind=kind,
            size=exported.stat().st_size,
            tags=["workbench", "artifact"],
            metadata={
                "session_id": session_id,
                "run_id": run_id,
                "workspace_path": raw_path,
            },
            content_hash=store.content_hash_file(exported),
        )
        if doc.get("status") in {"pending", "error"}:
            await ingest.index_document(db_path, doc["id"])
        documents.append(doc)

    return documents


async def migrate_default_project_knowledge() -> dict[str, Any]:
    """Copy the Workbench default project's own docs out of the shared global KB.

    The historical default project keys knowledge on ``dataKey == "default"`` →
    ``kb_default.db``, which the startup catalog also fills with the entire
    global attachment domain (every project's uploads/exports). Knowledge now
    keys on the project **id**, so the default project reads ``kb_<id>.db``. This
    one-time, non-destructive migration re-ingests the default project's own
    docs (its task archives + files produced by its own agent sessions) into the
    id db, leaving ``kb_default.db`` intact for historical data and API
    compatibility. Idempotent: paths already present in the target are skipped.
    """
    from cyrene.workbench import context as wc

    projects = wc._read_projects()
    default_project = next(
        (
            p
            for p in projects
            if wc._safe_workbench_data_key(p.get("dataKey") or p.get("id")) == "default"
        ),
        None,
    )
    if not default_project:
        return {"migrated": 0, "reason": "no_default_project"}

    default_id = str(default_project.get("id") or "")
    target_key = wc._safe_workbench_data_key(default_id)
    if target_key == "default":
        # Id sanitizes back to "default" — nothing to decouple.
        return {"migrated": 0, "reason": "key_not_decoupled"}

    source_db = str(get_knowledge_db_path("default"))
    target_db = str(get_knowledge_db_path(target_key))
    if source_db == target_db or not Path(source_db).exists():
        return {"migrated": 0, "reason": "no_source"}

    await init_knowledge_db(target_db)
    existing_paths = {
        str(doc.get("path") or "")
        for doc in await store.list_documents(target_db, limit=0)
    }

    migrated = 0
    for doc in await store.list_documents(source_db, limit=0):
        path = str(doc.get("path") or "")
        if not path or path in existing_paths:
            continue
        source = str(doc.get("source") or "")
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        session_id = str(metadata.get("session_id") or "")
        # Attributable to the default project: a Workbench run archive (those are
        # only ever written for the project that produced them) or a file from one
        # of the default project's own agent sessions. Catalog-synced uploads and
        # exports (no session linkage) are NOT the default project's — they are the
        # global domain and must stay behind in kb_default.db.
        attributable = source in {"workbench_task", "workbench_artifact"} or (
            bool(session_id)
            and wc.resolve_workbench_project_id_for_session(session_id) == default_id
        )
        if not attributable or not Path(path).exists():
            continue
        new_doc = await store.upsert_document_by_path(
            target_db,
            path=path,
            source=source or "import",
            name=doc.get("name") or Path(path).name,
            title=doc.get("title") or "",
            content_type=doc.get("content_type") or "application/octet-stream",
            kind=doc.get("kind") or "",
            size=int(doc.get("size") or 0),
            tags=doc.get("tags") or [],
            metadata=metadata,
            content_hash=doc.get("content_hash") or store.content_hash_file(Path(path)),
        )
        existing_paths.add(str(new_doc.get("path") or path))
        if new_doc.get("status") in {"pending", "error"}:
            await ingest.index_document(target_db, new_doc["id"])
        migrated += 1

    return {"migrated": migrated, "target": target_key, "reason": "ok"}


__all__ = ["archive_workbench_run", "migrate_default_project_knowledge"]

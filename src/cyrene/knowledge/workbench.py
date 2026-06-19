"""Archive Workbench run results and produced files into project knowledge."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from cyrene.attachments import (
    EXPORTS_DIR,
    attachment_kind_from_meta,
    register_generated_attachment,
)
from cyrene.config import get_knowledge_db_path
from cyrene.db import init_knowledge_db
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
) -> list[dict[str, Any]]:
    """Persist a completed run summary and its readable files in project knowledge."""
    response = str(agent_response or "").strip()
    if not response:
        return []

    db_path = str(get_knowledge_db_path(data_key or "default"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_knowledge_db(db_path)
    changes = file_changes if isinstance(file_changes, list) else []

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

    documents = [summary_doc]
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


__all__ = ["archive_workbench_run"]

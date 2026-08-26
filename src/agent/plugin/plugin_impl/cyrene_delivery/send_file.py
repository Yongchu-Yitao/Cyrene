"""Tool implementation for send_file."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    json_result,
    resolve_exportable_path,
    asyncio,
    build_public_attachment_payload,
    logger,
    register_generated_attachment,
)

TOOL_NAME = 'send_file'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_file(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    path_arg = str(args.get("path", "") or "").strip()
    if not path_arg:
        return "Error: 'path' is required."

    from cyrene.agent.context import (
        get_current_agent_id,
        get_current_client_request_id,
        get_current_round_id,
        get_current_session_id,
    )
    from cyrene.agent.session import append_system_message
    from cyrene.agent.message import insert_intermediate_user_reply

    if get_current_agent_id() != "main":
        return "Only the main agent can send a file to the WebUI."

    path = resolve_exportable_path(path_arg)
    if not path.exists() or not path.is_file():
        return f"Error: file not found: {path}"

    text = str(args.get("text", "") or "").strip()
    registered = register_generated_attachment(str(path), display_name=str(args.get("name", "") or "").strip() or None)
    attachment = build_public_attachment_payload(registered)

    # Register in knowledge base for legacy/non-Workbench sessions. Workbench
    # tasks archive only final deliverables after review/completion, so sending
    # a file mid-run must not immediately pollute project knowledge.
    try:
        from cyrene.knowledge import store, ingest
        from cyrene.workbench.context import (
            ensure_knowledge_db_for_session,
            resolve_workbench_session_kind,
        )
        import mimetypes
        doc_path = registered.get("path", "")
        current_session_id = str(get_current_session_id() or "")
        session_kind = resolve_workbench_session_kind(current_session_id)
        if doc_path and session_kind not in {"task", "init"}:
            from pathlib import Path
            import mimetypes
            doc_file = Path(doc_path)
            content_type = mimetypes.guess_type(str(doc_file))[0] or "application/octet-stream"
            from cyrene.runtime.attachments import attachment_kind_from_meta
            kind = attachment_kind_from_meta(content_type, doc_file.name)
            content_hash = store.content_hash_file(doc_file)
            _kb_db_path = await ensure_knowledge_db_for_session(get_current_session_id())
            doc = await store.upsert_document_by_path(
                _kb_db_path,
                path=str(doc_file.resolve()),
                source="generated",
                name=registered.get("name", doc_file.name),
                content_type=content_type,
                kind=kind,
                size=doc_file.stat().st_size if doc_file.exists() else 0,
                metadata={
                    "sent_to_chat": True,
                    "session_id": str(get_current_session_id() or ""),
                },
                content_hash=content_hash,
            )
            if doc.get("status") in {"pending", "error"}:
                asyncio.create_task(ingest.index_document(_kb_db_path, doc["id"]))
    except Exception as e:
        logger.debug(f"Failed to register generated file in knowledge base: {e}")

    round_id = str(get_current_round_id() or "").strip()
    client_request_id = str(get_current_client_request_id() or "").strip()
    if round_id:
        await insert_intermediate_user_reply(
            text,
            round_id=round_id,
            client_request_id=client_request_id,
            attachments=[attachment],
        )
    else:
        await append_system_message(
            text,
            message_meta={"attachments": [attachment]},
        )
    if _notify_state is not None:
        _notify_state["sent"] = True
    return json_result({
        "status": "sent",
        "attachment": attachment,
    })


handler = _tool_send_file

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_file"]

"""Persistent resources pinned in the Workbench topbar.

Files are global agent context. Browser resources deliberately expose only a
read-only snapshot path; mutating browser tools remain bound to the caller's
own session.
"""

from __future__ import annotations

import threading
import uuid
import re
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.workbench.store import read_document, write_document

_KEY = "workbench_pinned_resources"
_DB_PATH = ""
_LOCK = threading.RLock()


def configure(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = str(db_path or "")


def _default() -> dict[str, Any]:
    return {"version": 1, "resources": []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict[str, Any]:
    if not _DB_PATH:
        return _default()
    return read_document(_DB_PATH, _KEY, _default)


def _write(payload: dict[str, Any]) -> None:
    if _DB_PATH:
        write_document(_DB_PATH, _KEY, payload, _default)


def list_resources() -> list[dict[str, Any]]:
    with _LOCK:
        payload = _read()
        resources = [
            dict(item)
            for item in payload.get("resources", [])
            if isinstance(item, dict) and item.get("id")
        ]
    return [
        {**item, **_conversation_summary(item)}
        if item.get("kind") == "conversation"
        else item
        for item in resources
    ]


def _bounded_message_text(message: Any, limit: int) -> str:
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return ""
    text = re.sub(r"\s+", " ", message["content"]).strip()
    return text[:limit]


def _conversation_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Build bounded, read-only context without exposing the full transcript."""
    chat_id = str(item.get("conversationId") or item.get("ownerSessionId") or "").strip()
    if not chat_id:
        return {"conversationId": "", "availability": "missing"}
    try:
        from cyrene.workbench.chat_application import public_chat_light
        from cyrene.workbench.chat_repository import ChatRepository

        chat = ChatRepository(_DB_PATH).get(chat_id)
        if not chat:
            return {"conversationId": chat_id, "availability": "missing"}
        public = public_chat_light(chat)
    except Exception:
        return {"conversationId": chat_id, "availability": "unavailable"}

    visible_messages = [
        message
        for message in (chat.get("messages") or [])
        if isinstance(message, dict)
        and str(message.get("role") or "") in {"user", "assistant"}
        and not bool(message.get("systemInitiated"))
        and not bool(message.get("notificationCard"))
        and isinstance(message.get("content"), str)
        and message.get("content", "").strip()
    ]
    user_messages = [message for message in visible_messages if message.get("role") == "user"]
    assistant_messages = [
        message
        for message in visible_messages
        if message.get("role") == "assistant" and not bool(message.get("intermediate"))
    ]
    pending = chat.get("pendingQuestion") if isinstance(chat.get("pendingQuestion"), dict) else {}
    open_question = next(
        (
            re.sub(r"\s+", " ", str(pending.get(key) or "")).strip()[:600]
            for key in ("question", "prompt", "text", "message")
            if str(pending.get(key) or "").strip()
        ),
        "",
    )
    generated_files = [
        str(file.get("name") or Path(str(file.get("path") or "")).name).strip()
        for file in (chat.get("generatedFiles") or [])
        if isinstance(file, dict) and str(file.get("name") or file.get("path") or "").strip()
    ][-8:]
    return {
        "conversationId": chat_id,
        "availability": "available",
        "title": str(public.get("title") or item.get("title") or "Conversation").strip(),
        "status": str(public.get("runStatus") or public.get("status") or "idle"),
        "updatedAt": str(public.get("updatedAt") or item.get("updatedAt") or ""),
        "summary": {
            "goal": _bounded_message_text(user_messages[0], 800) if user_messages else "",
            "currentRequest": _bounded_message_text(user_messages[-1], 800) if user_messages else "",
            "latestResult": _bounded_message_text(assistant_messages[-1], 1600) if assistant_messages else "",
            "openQuestion": open_question,
            "artifacts": generated_files,
        },
    }


def upsert_resource(raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in {"file", "browser", "snippet", "conversation"}:
        raise ValueError("kind must be file, browser, snippet, or conversation")
    if kind == "snippet":
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError("snippet text is required")
        from cyrene.runtime.attachments import EXPORTS_DIR

        summary = str(raw.get("title") or text.splitlines()[0] or "摘录").strip()
        summary = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", summary)
        summary = re.sub(r"\s+", " ", summary).strip(" .-")[:40] or "摘录"
        display_name = summary if summary.lower().endswith(".md") else summary + ".md"
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        # Keep the user-facing Unicode title separate from the on-disk key.
        # Export routes historically sanitize path segments, and a long CJK
        # title can also exceed filesystem byte limits despite its short
        # character count.
        stored_name = f"{uuid.uuid4().hex}.md"
        stored_path = (EXPORTS_DIR / stored_name).resolve()
        stored_path.write_text(text.rstrip() + "\n", encoding="utf-8")
        raw = {
            **raw,
            "kind": "file",
            "sourceKind": "snippet",
            "title": display_name,
            "name": display_name,
            "path": str(stored_path),
            "url": f"/api/workbench/exports/{stored_name}",
            "content_type": "text/markdown",
            "size": stored_path.stat().st_size,
            "stableRef": str(raw.get("stableRef") or stored_name),
            "file": {
                "id": stored_name,
                "name": display_name,
                "url": f"/api/workbench/exports/{stored_name}",
                "content_type": "text/markdown",
                "kind": "file",
                "size": stored_path.stat().st_size,
            },
        }
        kind = "file"
    owner_session_id = str(raw.get("ownerSessionId") or raw.get("owner_session_id") or "").strip()
    if not owner_session_id:
        raise ValueError("ownerSessionId is required")
    stable_ref = str(
        raw.get("stableRef")
        or raw.get("stable_ref")
        or raw.get("path")
        or raw.get("url")
        or owner_session_id
    ).strip()
    resource_id = str(raw.get("id") or "").strip() or "pin_" + uuid.uuid4().hex[:16]
    now = _now()
    item: dict[str, Any] = {
        "id": resource_id,
        "kind": kind,
        "ownerSessionId": owner_session_id,
        "ownerProjectId": str(raw.get("ownerProjectId") or "").strip(),
        "ownerProjectName": str(raw.get("ownerProjectName") or "").strip(),
        "title": str(raw.get("title") or raw.get("name") or ("Browser" if kind == "browser" else "file")).strip(),
        "url": str(raw.get("url") or "").strip(),
        "stableRef": stable_ref,
        "createdAt": str(raw.get("createdAt") or now),
        "updatedAt": now,
    }
    if kind == "file":
        source_kind = str(raw.get("sourceKind") or "").strip()
        library_item_id = str(raw.get("libraryItemId") or "").strip()
        item.update({
            "name": str(raw.get("name") or raw.get("title") or "file").strip(),
            "path": str(raw.get("path") or "").strip(),
            "content_type": str(raw.get("content_type") or raw.get("contentType") or "").strip(),
            "size": int(raw.get("size") or 0),
            "file": dict(raw.get("file") or {}),
            **({"sourceKind": source_kind} if source_kind else {}),
            **({"libraryItemId": library_item_id} if library_item_id else {}),
        })
        if item["path"]:
            try:
                item["path"] = str(Path(item["path"]).expanduser().resolve())
            except Exception:
                pass
    elif kind == "browser":
        item.update({
            "tabId": str(raw.get("tabId") or "").strip(),
            "favicon": str(raw.get("favicon") or "").strip(),
            "readOnlyForOtherSessions": True,
        })
    elif kind == "conversation":
        item.update({
            "conversationId": str(raw.get("conversationId") or owner_session_id).strip(),
            "readOnlyForOtherSessions": True,
        })

    with _LOCK:
        payload = _read()
        resources = [
            current for current in payload.get("resources", [])
            if isinstance(current, dict)
        ]
        duplicate = next(
            (
                current for current in resources
                if str(current.get("kind") or "") == kind
                and str(current.get("ownerSessionId") or "") == owner_session_id
                and str(current.get("stableRef") or "") == stable_ref
            ),
            None,
        )
        if duplicate:
            item["id"] = str(duplicate.get("id") or resource_id)
            item["createdAt"] = str(duplicate.get("createdAt") or now)
        resources = [
            current for current in resources
            if str(current.get("id") or "") != item["id"]
        ]
        payload["resources"] = [item, *resources][:50]
        _write(payload)
    return item


def remove_resource(resource_id: str) -> bool:
    target = str(resource_id or "").strip()
    if not target:
        return False
    with _LOCK:
        payload = _read()
        before = payload.get("resources", [])
        after = [
            item for item in before
            if not isinstance(item, dict) or str(item.get("id") or "") != target
        ]
        if len(after) == len(before):
            return False
        payload["resources"] = after
        _write(payload)
        return True


def get_resource(resource_id: str) -> dict[str, Any] | None:
    target = str(resource_id or "").strip()
    return next(
        (item for item in list_resources() if str(item.get("id") or "") == target),
        None,
    )


def browser_snapshot_target(resource_id: str, caller_session_id: str) -> dict[str, Any]:
    item = get_resource(resource_id)
    if not item or item.get("kind") != "browser":
        raise ValueError("Pinned browser resource not found")
    owner = str(item.get("ownerSessionId") or "").strip()
    caller = str(caller_session_id or "").strip()
    return {
        "resource": item,
        "ownerSessionId": owner,
        "readOnly": bool(caller and caller != owner),
    }


def global_agent_context(current_session_id: str = "") -> str:
    resources = list_resources()
    if not resources:
        return ""
    lines = [
        "<pinned_topbar_resources>",
        "The user pinned these Workbench resources globally. Treat files as user-provided context.",
        "Pinned browsers owned by another session are strictly read-only: use browser.snapshot with resource_id; never navigate, click, type, reload, upload, or otherwise mutate them.",
        "Pinned conversations are bounded read-only summaries. Never continue, stop, message, or otherwise control another conversation unless the user explicitly requests that separate action.",
    ]
    for item in resources:
        kind = str(item.get("kind") or "")
        rid = str(item.get("id") or "")
        owner = str(item.get("ownerSessionId") or "")
        if kind == "file":
            lines.append(
                f'- file resource_id="{rid}" name="{item.get("name") or item.get("title") or "file"}" '
                f'path="{item.get("path") or ""}" url="{item.get("url") or ""}"'
            )
        elif kind == "browser":
            access = "owner-control" if owner == str(current_session_id or "") else "read-only"
            lines.append(
                f'- browser resource_id="{rid}" title="{item.get("title") or "Browser"}" '
                f'url="{item.get("url") or ""}" owner_session="{owner}" access="{access}"'
            )
        elif kind == "conversation":
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            access = "owner" if owner == str(current_session_id or "") else "read-only"
            lines.extend([
                (
                    f'- <conversation resource_id="{escape(rid, quote=True)}" '
                    f'title="{escape(str(item.get("title") or "Conversation"), quote=True)}" '
                    f'project="{escape(str(item.get("ownerProjectName") or item.get("ownerProjectId") or ""), quote=True)}" '
                    f'project_id="{escape(str(item.get("ownerProjectId") or ""), quote=True)}" '
                    f'owner_session="{escape(owner, quote=True)}" '
                    f'status="{escape(str(item.get("status") or "unknown"), quote=True)}" '
                    f'updated_at="{escape(str(item.get("updatedAt") or ""), quote=True)}" '
                    f'access="{access}">'
                ),
                f'  <goal>{escape(str(summary.get("goal") or ""))}</goal>',
                f'  <current_request>{escape(str(summary.get("currentRequest") or ""))}</current_request>',
                f'  <latest_result>{escape(str(summary.get("latestResult") or ""))}</latest_result>',
                f'  <open_question>{escape(str(summary.get("openQuestion") or ""))}</open_question>',
                f'  <artifacts>{escape(", ".join(summary.get("artifacts") or []))}</artifacts>',
                "  </conversation>",
            ])
    lines.append("</pinned_topbar_resources>")
    return "\n".join(lines)


def pinned_file_paths() -> dict[str, str]:
    paths: dict[str, str] = {}
    for item in list_resources():
        if item.get("kind") != "file":
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        name = str(item.get("name") or Path(path).name)
        paths[name] = path
        paths[Path(path).name] = path
    return paths


__all__ = [
    "browser_snapshot_target",
    "configure",
    "get_resource",
    "global_agent_context",
    "list_resources",
    "pinned_file_paths",
    "remove_resource",
    "upsert_resource",
]

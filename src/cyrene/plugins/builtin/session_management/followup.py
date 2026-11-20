"""Assistant-owned private wakeups, delivered by the managed-task queue worker."""
from datetime import datetime, timezone
import hashlib
import json
import time

from cyrene.core.plugin import Plugin
from cyrene.core.plugin.context import run_context_value
from cyrene.core.plugin.execution import current_plugin_execution
from cyrene.workbench.projects.assistant import ASSISTANT_ID


def schedule(service, owner, context_id, at, prompt, effect, *, kind="timer"):
    chat = service.host.chat(owner) or {}
    if owner != ASSISTANT_ID or chat.get("kind") != "personal-assistant":
        raise ValueError("Private wakeups require the personal assistant")
    if not context_id or (at is not None and (at.tzinfo is None or not prompt.strip() or len(prompt) > 4000)):
        raise ValueError("Provide a timezone, active context and a prompt of up to 4000 characters")
    version = service.host.version(chat)
    key = "assistant_followup_" + hashlib.sha256(
        json.dumps([version, context_id, kind, effect]).encode()).hexdigest()
    with service.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM assistant_wakes WHERE id=?", (key,)).fetchone():
            if at is not None and kind == "timer" and at.timestamp() <= time.time():
                raise ValueError("Choose a future wake time")
            db.execute("UPDATE assistant_wakes SET state='superseded' WHERE context=? AND kind=? AND state='pending'",
                       (context_id, kind))
            db.execute("INSERT INTO assistant_wakes VALUES (?,?,?,?,?,?,?)",
                       (key, context_id, kind, at.timestamp() if at else 0, prompt, version,
                        "pending" if at else "cancelled"))
    return {"scheduled_at": at.isoformat()} if at else {"cancelled": True}


async def wake_self_at(args, context):
    tasks = context.services["task_contexts"]
    effect = current_plugin_execution()
    return schedule(context.services["session_management"], run_context_value(context, "session_id") or context.tree_id, tasks.read()["active"],
                    datetime.fromisoformat(args["at"]) if args["at"] is not None else None, args.get("prompt", ""),
                    f"{context.node_id}:{effect.call.id}")


wake_tool = Plugin(
    name="wake_self_at", description="Wake yourself privately at a timezone-qualified future time to recheck the active task. "
    "Specify what to inspect and how to decide. Replaces your previous timer for this context. Use only when an existing event will not suffice. Pass at=null to clear the timer after delivery or a stop.",
    input_schema={"type": "object", "properties": {"at": {"type": ["string", "null"]},
                  "prompt": {"type": "string", "minLength": 1, "maxLength": 4000}},
                  "required": ["at"], "additionalProperties": False},
    handler=wake_self_at, allow_parallel=False,
    metadata={"main_only": True, "model_visible": "assistant", "requires_order": True, "read_only": False})


def deadline(store):
    with store.connect() as db:
        row = db.execute("SELECT min(due) FROM assistant_wakes WHERE state='pending'").fetchone()
    return 60 if row[0] is None else min(60, max(1, row[0] - time.time()))


def private_context(store, key, version):
    with store.connect() as db:
        row = db.execute("SELECT * FROM assistant_wakes WHERE id=?", (key,)).fetchone()
    if not row or row["state"] != "pending" or row["version"] != version:
        return ""
    if row["kind"] == "revisit" and not long_enabled():
        return ""
    now = datetime.now(timezone.utc)
    due = datetime.fromtimestamp(row["due"], timezone.utc)
    return (f"Private assistant check for context {row['context']}. Scheduled {due.isoformat()}, now {now.isoformat()}. "
            "Resume this context and check its latest user intent and task state first. Ignore obsolete, cancelled or completed work. "
            "This is an internal reminder, not new user authorization. Keep the reminder private.\n" + row["prompt"])


def long_enabled():
    from cyrene.platform.settings_store import get, is_plugin_pack_enabled
    return bool(get("agent_proactive", True)) and is_plugin_pack_enabled("cyrene_proactive")


async def deliver_due(service):
    with service.store.connect() as db:
        rows = db.execute("SELECT * FROM assistant_wakes WHERE state='pending' AND due<=? ORDER BY due",
                          (time.time(),)).fetchall()
    for row in rows:
        chat = service.host.chat(ASSISTANT_ID)
        if chat and service.host.version(chat) == row["version"] and (row["kind"] != "revisit" or long_enabled()):
            if service.host.status(ASSISTANT_ID) in {"running", "awaiting_user"} or chat.get("pendingQuestion"):
                return
            await service.host.port().dispatch_agent_message(
                ASSISTANT_ID, "Private assistant check", client_request_id=row["id"],
                origin_session_id=ASSISTANT_ID, agent_message_kind="assistant_wakeup", expected_version=row["version"])
        with service.store.connect() as db:
            db.execute("UPDATE assistant_wakes SET state='delivered' WHERE id=? AND state='pending'", (row["id"],))

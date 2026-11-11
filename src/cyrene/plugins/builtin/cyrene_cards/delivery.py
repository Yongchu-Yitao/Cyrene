"""Durable UI submissions delivered through the host's normal human-message port."""

import asyncio

from cyrene.platform.database_notifications import database_changes, wait_for_database_change
import hashlib
import json
import logging

from .schemas import CardError

logger = logging.getLogger(__name__)


def submission_message(receipt):
    spec = receipt["spec_snapshot"]
    action = next(item for item in spec["actions"] if item["action_id"] == receipt["action_id"])
    # Include readable labels/selected rows as well as exact machine values.
    fields = {}
    for block in spec["blocks"]:
        for field in block.get("fields", []):
            key = field["field_id"]
            if key in receipt["values"]:
                value = receipt["values"][key]
                label = next((o["label"] for o in field.get("options", []) if o["value"] == value), value)
                fields[key] = {"label": field["label"], "value": label}
        selection = block.get("selection")
        if selection and selection["field_id"] in receipt["values"]:
            selected = receipt["values"][selection["field_id"]]
            selected = selected if isinstance(selected, list) else [selected]
            items = block.get("rows", block.get("places", []))
            fields[selection["field_id"]] = [item for item in items if item.get("row_id", item.get("place_id")) in selected]
    data = {
        "card_id": receipt["card_id"], "event_id": receipt["event_id"],
        "origin": receipt["origin"], "revision": receipt["base_revision"],
        "input_revision": receipt["input_revision"], "kind": receipt["kind"],
        "target_context_id": action.get("target_context_id"), "task_id": action.get("task_id"),
        "action": action, "values": receipt["values"], "display_values": fields,
    }
    return (f'我在卡片「{spec["title"]}」中点击了「{action["label"]}」。请根据这次提交继续处理。\n'
            '以下是本次提交的固定快照；后续草稿不代表这次提交。若 action 指定 target_context_id，请恢复该任务上下文并继续；task_id 指定已有执行任务时使用 task_update，不要另起重复任务。回访卡片可跨上下文编辑，普通卡片需恢复来源上下文。\n'
            + json.dumps(data, ensure_ascii=False, indent=2))


class CardDelivery:
    def __init__(self, service, port):
        self.service = service
        self.port = port
        self.lock = asyncio.Lock()
        self.worker = None

    async def start(self):
        self.worker = asyncio.create_task(self._work(), name="card-submission-delivery")

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    def _read(self, owner, card_id, event_id):
        with self.service.store.connect() as db:
            row = db.execute("SELECT e.document,d.version,d.state,d.detail FROM events e JOIN deliveries d "
                             "ON e.owner=d.owner AND e.event_id=d.event_id "
                             "WHERE e.owner=? AND e.card_id=? AND e.event_id=?", (owner, card_id, event_id)).fetchone()
        if row is None:
            raise CardError("card_not_found", "Submission not found in this card")
        return dict(row)

    def _save(self, owner, event_id, status, detail):
        with self.service.store.connect(write=True) as db:
            db.execute("UPDATE deliveries SET state=?,detail=? WHERE owner=? AND event_id=?",
                       (status, json.dumps(detail, ensure_ascii=False), owner, event_id))

    async def deliver(self, owner, card_id, event_id, *, retry=False):
        async with self.lock:
            row = await asyncio.to_thread(self._read, owner, card_id, event_id)
            if row["state"] == "sent" or (row["state"] == "failed" and not retry):
                return {**json.loads(row["detail"]), "status": row["state"]}
            # Keep a retry durable too, so a process restart can finish it.
            await asyncio.to_thread(self._save, owner, event_id, "pending", {})
            request_id = "card_" + hashlib.sha256(f"{owner}:{event_id}".encode()).hexdigest()
            try:
                result = await self.port().dispatch_user_message(
                    owner, submission_message(json.loads(row["document"])),
                    client_request_id=request_id, expected_version=row["version"],
                )
                detail = {"session_id": owner, "mode": result["status"], "request_id": request_id}
                status = "sent"
            except Exception as exc:
                logger.warning("Card submission delivery failed: %s", exc)
                # Admission/maintenance conflicts are transient. Other failures
                # remain visible and require an explicit retry, like chat sends.
                status = "pending" if getattr(exc, "status_code", None) == 409 else "failed"
                detail = {"message": str(exc) or "Message could not be sent", "session_id": owner}
            await asyncio.to_thread(self._save, owner, event_id, status, detail)
            return {**detail, "status": status}

    def has_pending(self):
        with self.service.store.connect() as db:
            return db.execute("SELECT 1 FROM deliveries WHERE state='pending' LIMIT 1").fetchone() is not None

    async def tick(self):
        def pending():
            with self.service.store.connect() as db:
                return db.execute("SELECT e.owner,e.card_id,e.event_id FROM events e JOIN deliveries d "
                                  "ON e.owner=d.owner AND e.event_id=d.event_id WHERE d.state='pending' "
                                  "ORDER BY e.seq LIMIT 20").fetchall()
        for row in await asyncio.to_thread(pending):
            await self.deliver(row["owner"], row["card_id"], row["event_id"])

    async def _work(self):
        # Subscribe before replaying durable state: no enqueue can fall between
        # the empty check and sleep. Keep the original retry cadence when busy.
        with database_changes(self.service.store.path) as changed:
            while True:
                changed.clear()
                pending = True
                try:
                    await self.tick()
                    pending = await asyncio.to_thread(self.has_pending)
                except Exception:
                    logger.exception("Card delivery recovery failed")
                if pending:
                    await asyncio.sleep(2)
                else:
                    await wait_for_database_change(changed)

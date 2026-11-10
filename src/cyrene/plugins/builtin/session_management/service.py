"""Assistant-owned sessions, serialized control and durable attachment delivery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path

from .host import KIND, OWNER
from .store import Store
from .organization import organize_task

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_LOAD_CHARS = 60000


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class SessionManagementService:
    def __init__(self, directory, host):
        self.store = Store(Path(directory) / "sessions.sqlite3")
        self.host = host
        self.loop = None
        self.lock = None
        self.worker = None

    def initialize_assistant(self):
        """Create the one application-wide assistant if it does not exist."""
        from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID

        chat = self.host.chat(ASSISTANT_ID)
        if chat:
            return chat
        port = self.host.port()
        chat = port.service.create_chat(
            ASSISTANT_SCOPE_ID, "Cyrene", soul_active=True,
            workspace_active=True, project_memory_active=False,
        )
        chat.update(id=ASSISTANT_ID, kind="personal-assistant")
        return port.service.repository.insert(chat)

    async def reset_assistant(self):
        from cyrene.workbench.projects.assistant import ASSISTANT_ID

        async def perform():
            async with self.lock:
                await asyncio.to_thread(self.initialize_assistant)
                return await asyncio.to_thread(self.host.reset_assistant, ASSISTANT_ID, self.store)

        return await self._on_owner(perform)

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self.worker = asyncio.create_task(self._worker(), name="session-management-delivery")

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        self.loop = None

    async def _on_owner(self, operation):
        if self.loop is None or self.loop.is_closed():
            raise RuntimeError("Task execution is not running")
        if asyncio.get_running_loop() is self.loop:
            return await operation()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(operation(), self.loop))

    def _owned(self, owner, target):
        record = self.store.owned(owner, target)
        chat = self.host.chat(target)
        if not chat or chat.get("kind") != KIND or chat.get(OWNER) != owner:
            raise ValueError("Task was deleted or its ownership changed")
        if not self.host.chat(owner):
            raise ValueError("Task owner no longer exists")
        return record, chat

    def _files(self, paths, workspace):
        if len(paths) > 10:
            raise ValueError("At most ten files can be attached")
        result = []
        total = 0
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(workspace) / path
            if not path.is_file():
                raise ValueError(f"Attachment is not a file: {path}")
            # Bound the actual read too; a file may grow between stat and read.
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            total += len(content)
            if not content or total > MAX_FILE_BYTES:
                raise ValueError("Attachments must be nonempty and total at most 20 MiB")
            result.append(self.host.attachment(content, path.name))
        return result

    def export_path(self, owner, path):
        """Allow the assistant to deliver files from its own task workspaces."""
        resolved = Path(path).expanduser().resolve()
        for record in self.store.sessions(owner):
            try:
                _, chat = self._owned(owner, record["id"])
            except ValueError:
                continue
            workspace = chat.get("workspaceOverride")
            if workspace and resolved.is_relative_to(Path(workspace).expanduser().resolve()):
                return resolved
        return None

    async def command(self, owner, operation, args, effect, workspace):
        async def perform():
            async with self.lock:
                key = f"{owner}:{operation}:{effect}"
                fingerprint = digest(args)
                replay = self.store.replay(key, fingerprint)
                if replay is not None:
                    return replay
                self.store.reserve(key, fingerprint)
                if operation == "session_create":
                    target = "managed_" + digest(key)[:24]
                    from cyrene.workbench.chat.chat_application import normalize_workspace_override

                    task_workspace = normalize_workspace_override(args.get("workspace_path"))
                    if not task_workspace:
                        raise ValueError("workspace_path is required")
                    await asyncio.to_thread(self.host.create, owner, target, args["title"], task_workspace)
                    self.store.register(target, owner)
                    result = {"session_id": target, "status": "idle"}
                    if args.get("message") or args.get("files"):
                        message = await self._enqueue(owner, target, args, key, workspace)
                        result.update(message)
                else:
                    target = args["session_id"]
                    self._owned(owner, target)
                    result = await self._command_existing(owner, target, operation, args, key, workspace)
                self.store.save(key, fingerprint, result)
                return result

        return await self._on_owner(perform)

    async def _command_existing(self, owner, target, operation, args, key, workspace):
        if operation == "session_organize":
            result = organize_task(self.store, self.host, owner, target, args)
        elif operation == "session_send_message":
            result = await self._enqueue(owner, target, args, key, workspace)
            if self.host.status(target) == "running":
                # Admit updates to ready running tasks during this tool call.
                # Durable ordering/retries still cover blocked targets and crashes.
                for row in self.store.pending(target=target):
                    if row["id"] == result["message_id"]:
                        await self._deliver_message(row)
                result["status"] = self.store.message(result["message_id"])["status"]
        elif operation == "session_answer_question":
            result = await self.host.answer(owner, target, args["question_id"], args["answer"], "managed_answer_" + digest(key)[:24])
        elif operation == "session_stop":
            self.store.pause(target)
            stopped = await self.host.stop(target)
            result = {
                "session_id": target,
                "status": "stopped",
                "cancelled_running_execution": bool(stopped),
                "queued_messages_paused": self.store.pending_count(target),
            }
        else:
            raise ValueError("Unknown session operation")
        return result

    async def _enqueue(self, owner, target, args, key, workspace):
        message_id = "managed_msg_" + digest(key)[:24]
        # A crash after acceptance must reuse the original file snapshots.
        existing = self.store.message(message_id)
        if existing is None:
            _, chat = self._owned(owner, target)
            text = str(args.get("message") or "").strip()
            if len(text) > 20000 or (not text and not args.get("files")):
                raise ValueError("Provide a message (at most 20000 characters) or files")
            attachments = await asyncio.to_thread(self._files, args.get("files", []), workspace)
            self.store.enqueue(
                owner,
                target,
                message_id,
                {
                    "content": text,
                    "attachments": attachments,
                    "version": self.host.version(chat),
                },
            )
        return {"session_id": target, "message_id": message_id, "status": self.store.message(message_id)["status"]}

    async def list(self, owner):
        async def perform():
            async with self.lock:
                result = []
                for record in self.store.sessions(owner):
                    try:
                        _, chat = self._owned(owner, record["id"])
                    except ValueError:
                        continue
                    checkpoint = self.host.checkpoint(record["id"])
                    result.append(
                        {
                            **{key: checkpoint[key] for key in
                               ("run_id", "node_id", "cancel_reason", "interruption_kind", "recovery_action") if key in checkpoint},
                            "session_id": record["id"],
                            "presentation": self.store.presentation(record["id"]),
                            "title": chat.get("title", ""),
                            "workspace_path": chat.get("workspaceOverride", ""),
                            "status": "stopped" if record["paused"] else self.host.status(record["id"]),
                            "pending_messages": self.store.pending_count(record["id"]),
                            "delivery_error": self.store.delivery_error(record["id"]),
                            "pending_question": chat.get("pendingQuestion"),
                            "created_at": chat.get("createdAt", ""),
                            "updated_at": chat.get("updatedAt", ""),
                        }
                    )
                return {"sessions": result}

        return await self._on_owner(perform)

    async def blocks(self, owner, args, *, load=False):
        async def perform():
            async with self.lock:
                target = args["session_id"]
                _, chat = self._owned(owner, target)
                snapshot = await asyncio.to_thread(self.host.blocks, target)
                ids = args.get("block_ids", [])
                # Only task evidence is exposed, never executor system prompts.
                blocks = [block for block in snapshot["blocks"] if block.get("kind") == "task"]
                if ids:
                    by_id = {b["block_id"]: b for b in blocks}
                    if any(i not in by_id for i in ids):
                        raise ValueError("Unknown context block ID; refresh the block directory")
                    blocks = [by_id[i] for i in dict.fromkeys(ids)]
                if load and not ids:
                    raise ValueError("Select explicit block_ids to load")
                result = {"session_id": target, "title": chat.get("title", ""), "revision": snapshot["revision"], "captured_at": datetime.now(timezone.utc).isoformat()}
                if load:
                    if sum(len(b["content"]) for b in blocks) > MAX_LOAD_CHARS:
                        raise ValueError("Selected blocks exceed 60000 characters; inspect smaller blocks first")
                    result["blocks"] = blocks
                else:
                    offset, limit = args.get("offset", 0), args.get("limit", 50)
                    page = blocks[offset : offset + limit]
                    result["blocks"] = [
                        {k: v for k, v in b.items() if k != "content"}
                        | (
                            {
                                "content": b["content"][args.get("content_offset", 0) : args.get("content_offset", 0) + args.get("content_limit", 4000)],
                                "content_offset": args.get("content_offset", 0),
                                "truncated": len(b["content"]) > args.get("content_offset", 0) + args.get("content_limit", 4000),
                            }
                            if ids
                            else {"preview": b["content"][:200]}
                        )
                        for b in page
                    ]
                    result["next_offset"] = offset + len(page) if offset + len(page) < len(blocks) else None
                return result

        return await self._on_owner(perform)

    async def capture_notification(self, target, node_id):
        # Called by the tree's ContextChange Hook after a committed mutation.
        record = self.store.session(target)
        if record is None:
            return
        owner = record["owner"]
        try:
            _, chat = self._owned(owner, target)
        except ValueError:
            return
        event = await asyncio.to_thread(self.host.notification, target, node_id)
        if event and self.store.session(target) == record and self.host.chat(target):
            event = {**event, "session_id": target}
            event_id = "managed_event_" + digest([target, self.host.version(chat), event.get("run_id"), event.get("node_id"), event["status"]])
            self.store.notify(
                event_id,
                owner,
                target,
                {
                    "event": event,
                    "owner_version": self.host.version(self.host.chat(owner)),
                },
            )

    async def notify_tasks(self):
        async with self.lock:
            await self._notify_tasks()

    async def _notify_tasks(self):
        for row in self.store.notifications():
            try:
                if await self.host.notify(row["owner"], row["target"], row["id"], json.loads(row["payload"])):
                    self.store.acknowledge(row["id"])
            except Exception:
                logger.exception("Managed session notification will retry")

    def delivery_failed(self, row, error):
        owner = self.host.chat(row["owner"])
        if owner is None:
            return
        self.store.notify("delivery_failure_" + row["id"], row["owner"], row["target"], {
            "owner_version": self.host.version(owner),
            "event": {"status": "failed", "phase": "delivery", "session_id": row["target"],
                      "message_id": row["id"], "error": error},
        })

    async def tick(self):
        await self.notify_tasks()
        for row in self.store.pending():
            async with self.lock:
                if self.store.message(row["id"]) is None:
                    continue
                await self._deliver_message(row)
        await self.notify_tasks()

    async def _deliver_message(self, row):
        try:
            record, _chat = self._owned(row["owner"], row["target"])
            if record["paused"]:
                return
            accepted = await self.host.deliver(row["owner"], row["target"], row["id"], json.loads(row["payload"]))
            if accepted:
                self.store.update(row["id"], "delivered")
            else:
                self.store.defer(row["id"])
        except ValueError as exc:
            self.store.update(row["id"], "failed", str(exc))
            self.delivery_failed(row, str(exc))
        except Exception as exc:
            self.store.update(row["id"], "failed" if row["attempts"] >= 11 else "queued", str(exc))
            if row["attempts"] >= 11:
                self.delivery_failed(row, str(exc))

    async def _worker(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Managed session queue failed")
            await asyncio.sleep(1)

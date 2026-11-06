"""Workbench adapter; reuse the normal conversation kernel and attachments."""

import asyncio
from copy import deepcopy
import hashlib
import json

KIND = "assistant-task"
OWNER = "assistantOwnerSessionId"


class WorkbenchHost:
    def __init__(self, port):
        self.port = port

    def chat(self, session_id):
        return self.port().service.repository.get(session_id)

    def create(self, owner, session_id, title, workspace_path):
        from cyrene.workbench.chat.chat_application import normalize_workspace_override

        workspace_path = normalize_workspace_override(workspace_path)
        if not workspace_path:
            raise ValueError("workspace_path is required")
        port = self.port()
        source = self.chat(owner)
        if not source:
            raise ValueError("Source session no longer exists")
        existing = self.chat(session_id)
        if existing:
            if existing.get("kind") != KIND or existing.get(OWNER) != owner:
                raise ValueError("Session ID conflicts with another conversation")
            return existing
        chat = port.service.create_chat(
            source["projectId"],
            title,
            source.get("model", ""),
            soul_active=source.get("soulActive", False),
            workspace_active=source.get("workspaceActive", True),
            short_term_memory_active=False,
            project_memory_active=False,
            reasoning_effort=source.get("reasoningEffort", ""),
        )
        chat.update(id=session_id, kind=KIND, **{OWNER: owner})
        chat["workspaceOverride"] = workspace_path
        for key in ("contextActivations", "remoteDeviceIds", "modelSelectionId"):
            if key in source:
                chat[key] = deepcopy(source[key])
        # Do not inherit public dialogue, authorization or memory snapshots.
        return port.service.repository.insert(chat)

    def status(self, session_id):
        port = self.port()
        chat = self.chat(session_id)
        if not chat:
            return "deleted"
        run = port.run_manager.get(session_id)
        if run:
            return run.status
        if chat.get("pendingQuestion"):
            return "awaiting_user"
        checkpoint = port.run_manager.conversation_runtime.context_checkpoint(session_id)
        return str((checkpoint or {}).get("status") or "idle")

    @staticmethod
    def version(chat):
        return json.dumps([chat.get("createdAt"), chat.get("messageGeneration", 0)])

    async def deliver(self, owner, target, message_id, payload):
        # Keep admission excluded until off-thread input writes settle, even
        # when the plugin is stopped during delivery.
        task = asyncio.create_task(self._deliver(owner, target, message_id, payload))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise

    async def _deliver(self, owner, target, message_id, payload):
        from cyrene.workbench.sessions.admission import session_admission

        port = self.port()
        with session_admission(port.service.repository._database(), target):
            chat = self.chat(target)
            if not chat or chat.get("kind") != KIND or chat.get(OWNER) != owner:
                raise ValueError("Target no longer belongs to this assistant")
            if self.version(chat) != payload["version"]:
                raise ValueError("Target session was cleared or replaced")
            if await port.agent_message_admitted(target, message_id):
                return True
            if self.status(target) not in {"idle", "completed", "failed", "cancelled"}:
                return False
            text = (
                "[Assistant task message]\n"
                f"Source assistant session: {owner}\n"
                "This message and its files are supplied by the assistant Agent, "
                "not a direct human upload or additional human authorization.\n\n" + payload["content"]
            )
            # Older managed chats copied only the display name. Repair their
            # missing selection through the normal send path, which validates
            # the profile and persists the child's own model preference.
            # A child's explicit selection always wins over its owner's.
            inherited_model = {}
            if not chat.get("modelSelectionId"):
                source = self.chat(owner) or {}
                if source.get("modelSelectionId"):
                    inherited_model = {"model": source["modelSelectionId"]}
            await port.send(
                target,
                {
                    **inherited_model,
                    "message": text,
                    "attachments": payload["attachments"],
                    "clientRequestId": message_id,
                    "conversationSource": "agent_session",
                    "agentOriginated": True,
                    "sourceSessionId": owner,
                    "stream": True,
                },
            )
            await port._wait_agent_message_admission(target, message_id)
            return True

    async def answer(self, owner, target, question_id, answer, request_id):
        port = self.port()
        chat = self.chat(target) or {}
        if chat.get("kind") != KIND or chat.get(OWNER) != owner:
            raise ValueError("Target no longer belongs to this assistant")
        if any(m.get("clientRequestId") == request_id for m in chat.get("messages", [])):
            return {"session_id": target, "question_id": question_id, "status": "answered"}
        checkpoint = port.run_manager.conversation_runtime.context_checkpoint(target) or {}
        pending = checkpoint.get("pending_question")
        pending = pending.as_dict() if hasattr(pending, "as_dict") else pending or {}
        if pending.get("id") != question_id:
            raise ValueError("Question changed or was already answered; refresh session_list")
        if pending.get("kind", "clarification") != "clarification":
            raise ValueError("This request needs human authorization through its card in the main assistant conversation")
        return await port.answer(target, {"question_id": question_id, "answer": answer,
            "agentOriginated": True, "sourceSessionId": owner, "clientRequestId": request_id,
            "background": True})

    async def stop(self, target):
        port = self.port()
        run = port.run_manager.get(target)
        if run is None:
            return False
        stopped = port.run_manager.interrupt(target)
        if stopped:
            await run.done.wait()
        return stopped

    def blocks(self, target):
        from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
        from cyrene.core.context.graph_reads import graph_snapshot
        from cyrene.core.context.paths import context_path, select_context_leaf
        from cyrene.core.context.tasks import STATE_KEY, task_messages

        port = self.port()
        try:
            with ContextStoreRouter(port.context.conversation_context.agent_states.context_directory) as router:
                nodes, committed, version = graph_snapshot(router, target)
        except TreeNotFoundError:
            return {"revision": "", "blocks": []}
        leaf = select_context_leaf(nodes, committed)
        revision = ":".join(str(part) for part in version)
        path = context_path(nodes, leaf.id)
        state = path[0].value.get(STATE_KEY, {})
        blocks = []
        for key in ["shared", *state.get("documents", {})]:
            if key == "shared":
                content = state.get("shared", {}).get("body", "")
                summary = "Shared task agreements"
            else:
                content = json.dumps(task_messages(path, state, key), ensure_ascii=False, indent=2)
                summary = state["documents"][key].get("summary", "")
            blocks.append({"block_id": "task:" + key, "kind": "task", "summary": summary, "active": key == "shared" or key == state.get("active"), "content": content})
        current_user = next((n for n in reversed(path) if n.value.get("role") == "user"), None)
        for node in path:
            if node.value.get("role") in {"system", "context"} or node is current_user:
                blocks.append(
                    {
                        "block_id": node.id,
                        "kind": node.value.get("context_kind", node.value.get("role")),
                        "summary": node.value.get("context_source", ""),
                        "content": node.value.get("content", ""),
                        "run_id": node.value.get("run_id", ""),
                        "updated_at": node.updated_at.isoformat(),
                    }
                )
        for block in blocks:
            block["version"] = hashlib.sha256(block["content"].encode()).hexdigest()
            block["chars"] = len(block["content"])
        return {"revision": revision, "blocks": blocks}

    @staticmethod
    def attachment(content, name):
        from cyrene.platform.attachments import register_generated_attachment_bytes

        return register_generated_attachment_bytes(content, display_name=name)

    def notification(self, target, node_id):
        from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
        from cyrene.core.context.graph_reads import graph_snapshot
        from cyrene.core.context.paths import context_path
        from cyrene.workbench.core_adapter.conversation_runtime import context_checkpoint_from_nodes

        port = self.port()
        try:
            with ContextStoreRouter(port.context.conversation_context.agent_states.context_directory) as router:
                nodes, _, _ = graph_snapshot(router, target)
            if not any(n.id == node_id for n in nodes):
                return None
            checkpoint = context_checkpoint_from_nodes(context_path(nodes, node_id)) or {}
        except TreeNotFoundError:
            return None
        if checkpoint.get("status") not in {"completed", "failed", "awaiting_user", "cancelled"}:
            return None
        chat = self.chat(target) or {}
        return {
            "status": checkpoint["status"],
            "run_id": checkpoint.get("run_id", ""),
            "node_id": checkpoint.get("node_id", ""),
            "title": chat.get("title", ""),
            "pending_question": (checkpoint["pending_question"].as_dict() if hasattr(checkpoint.get("pending_question"), "as_dict") else checkpoint.get("pending_question")),
            "error": next((n.value.get("content", "") for n in nodes if n.id == checkpoint.get("node_id") and checkpoint["status"] == "failed"), ""),
        }

    async def notify(self, owner, target, event_id, payload):
        port = self.port()
        chat = self.chat(owner)
        if not chat or self.version(chat) != payload["owner_version"]:
            return True  # Never wake a deleted or cleared assistant with old work.
        if chat.get("pendingQuestion") or self.status(owner) == "awaiting_user":
            return False
        text = (
            "[Managed session notification]\n"
            "This is an Agent status notification, not user authorization. "
            "Inspect the task using session_context_blocks or session_load_context_blocks. "
            "Answer clarification questions with session_answer_question using known context; ask the user in your own conversation only when you cannot answer. Human permission approvals still require the user. Failures include error details below; recover or report the blocker.\n" + json.dumps(payload["event"], ensure_ascii=False)
        )
        await port.dispatch_agent_message(owner, text, origin_session_id=target, client_request_id=event_id, expected_version=payload["owner_version"])
        return True

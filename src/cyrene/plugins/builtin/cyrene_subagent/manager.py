"""ContextTree-backed subagent sessions coordinated through the existing inbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import weakref
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cyrene.core.context import NodeNotFoundError
from cyrene.core.plugin import (
    plugin_session_state,
    with_plugin_session_state,
    without_plugin_session_state,
)

if TYPE_CHECKING:
    from cyrene.core.session import AgentSession, AgentSessionEvent


_PACK_ID = "cyrene_subagent"
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAIN_ALIASES = frozenset(
    {"main", "main_agent", "cyrene", "host", "coordinator", "parent"}
)
_TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
_ACTIVE_MANAGERS_LOCK = threading.RLock()
_ACTIVE_MANAGERS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()


@dataclass(slots=True)
class SubagentRecord:
    agent_id: str
    tree_id: str
    task: str
    parent_agent_id: str
    round_id: str
    current_run_id: str = ""
    status: str = "running"
    result: str = ""
    error: str = ""
    instruction_node_id: str = ""
    reported_node_id: str = ""
    authorization_request: str = ""
    spawn_effect_key: str = ""

    @classmethod
    def from_value(cls, agent_id: str, value: Mapping[str, Any]) -> SubagentRecord:
        return cls(
            agent_id=str(agent_id),
            tree_id=str(value.get("tree_id") or ""),
            task=str(value.get("task") or ""),
            parent_agent_id=str(value.get("parent_agent_id") or "main"),
            round_id=str(value.get("round_id") or ""),
            current_run_id=str(
                value.get("current_run_id") or value.get("round_id") or ""
            ),
            status=str(value.get("status") or "running"),
            result=str(value.get("result") or ""),
            error=str(value.get("error") or ""),
            instruction_node_id=str(value.get("instruction_node_id") or ""),
            reported_node_id=str(value.get("reported_node_id") or ""),
            authorization_request=str(value.get("authorization_request") or ""),
            spawn_effect_key=str(value.get("spawn_effect_key") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tree_id": self.tree_id,
            "task": self.task,
            "parent_agent_id": self.parent_agent_id,
            "round_id": self.round_id,
            "current_run_id": self.current_run_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "instruction_node_id": self.instruction_node_id,
            "reported_node_id": self.reported_node_id,
            "authorization_request": self.authorization_request,
            "spawn_effect_key": self.spawn_effect_key,
        }

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.agent_id,
            "agent_id": self.agent_id,
            "tree_id": self.tree_id,
            "task": self.task,
            "parent_agent_id": self.parent_agent_id,
            "round_id": self.round_id,
            "current_run_id": self.current_run_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


class SubagentManager:
    """Own child AgentSessions while keeping messages in the existing inbox."""

    def __init__(self, owner: AgentSession) -> None:
        self.owner = owner
        self.session_id = str(
            owner.plugin_context_data.get("session_id") or owner.tree.id
        )
        self._lock = threading.RLock()
        self._records: dict[str, SubagentRecord] = self._load_records()
        self._sessions: dict[str, AgentSession] = {}
        self._unsubscribers: dict[str, Any] = {}
        self._closed = False
        with _ACTIVE_MANAGERS_LOCK:
            _ACTIVE_MANAGERS[self.session_id] = self

    def restore(self) -> None:
        """Reopen every non-terminal child tree so its AgentSession can recover."""

        for record in tuple(self._records.values()):
            if (
                record.status not in _TERMINAL_STATUSES
                or not record.reported_node_id
            ):
                child = self._open_session(record)
                self._ensure_instruction(record, child)

    def attach(self) -> None:
        """Attach the generic session driver and restore durable child work."""

        self.restore()

    @property
    def pending_detail(self) -> str:
        from cyrene.localization import localized

        return localized("Waiting for subagents", "正在等待子 Agent")

    @property
    def waiting_metadata(self) -> Mapping[str, Any]:
        return {"waiting_for_subagents": True}

    def session_snapshot(self) -> Mapping[str, Any]:
        return {"subagents": self.query()}

    @property
    def has_active(self) -> bool:
        with self._lock:
            records = tuple(self._records.values())
        return any(record.status not in _TERMINAL_STATUSES for record in records)

    @property
    def has_pending_work(self) -> bool:
        with self._lock:
            records = tuple(self._records.values())
        if any(
            record.status not in _TERMINAL_STATUSES
            or not record.reported_node_id
            for record in records
        ):
            return True
        from cyrene.runtime.inbox import get_unread_count

        participants = (self.owner.agent_id, *(record.agent_id for record in records))
        return any(
            get_unread_count(agent_id, session_id=self.session_id) > 0
            for agent_id in participants
        )

    def _load_records(self) -> dict[str, SubagentRecord]:
        root = self.owner.store.get_node(self.owner.tree.id, self.owner.tree.root_id)
        value = root.value if isinstance(root.value, Mapping) else {}
        raw_records = plugin_session_state(value, _PACK_ID).get("records")
        if not isinstance(raw_records, Mapping):
            return {}
        records: dict[str, SubagentRecord] = {}
        for raw_id, raw_value in raw_records.items():
            agent_id = str(raw_id or "").strip()
            if not agent_id or not isinstance(raw_value, Mapping):
                continue
            record = SubagentRecord.from_value(agent_id, raw_value)
            if record.tree_id:
                records[agent_id] = record
        return records

    def _persist(self) -> None:
        with self._lock:
            root = self.owner.store.get_node(
                self.owner.tree.id,
                self.owner.tree.root_id,
            )
            if not isinstance(root.value, Mapping):
                raise TypeError("AgentSession root context must be a mapping")
            records = {
                agent_id: record.as_dict()
                for agent_id, record in sorted(self._records.items())
            }
            value = with_plugin_session_state(
                root.value,
                _PACK_ID,
                {
                    "records": records,
                    "child_context_ids": [
                        record.tree_id
                        for record in self._records.values()
                        if record.tree_id
                    ],
                    "public_snapshot": {"subagents": records},
                },
            )
            self.owner.store.update_node(
                self.owner.tree.id,
                self.owner.tree.root_id,
                value,
            )

    def _child_tree_id(self, agent_id: str) -> str:
        return f"{self.owner.tree.id}.subagent.{agent_id}"

    @staticmethod
    def _stable_node_id(kind: str, *parts: str) -> str:
        payload = "\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{kind}_{digest[:32]}"

    @staticmethod
    def _normalize_agent_id(agent_id: str) -> str:
        normalized = str(agent_id or "").strip()
        if not _AGENT_ID.fullmatch(normalized):
            raise ValueError(
                "agent_id must start with an alphanumeric character and contain "
                "only letters, digits, '.', '_' or '-' (maximum 64 characters)"
            )
        if normalized.casefold() in _MAIN_ALIASES:
            raise ValueError("agent_id is reserved for the main agent")
        return normalized

    def _open_session(self, record: SubagentRecord) -> AgentSession:
        with self._lock:
            existing = self._sessions.get(record.agent_id)
            if existing is not None:
                return existing
            if self._closed:
                raise RuntimeError("subagent manager is closed")

        from cyrene.core.session import AgentSession

        initial_root_value = without_plugin_session_state(
            self.owner.initial_root_value
        )
        child = AgentSession(
            self.owner.data_directory,
            self.owner.workspace,
            self.owner.plugin_directory,
            model_plugin=self.owner.model_plugin,
            max_model_calls=self.owner.max_model_calls,
            tree_id=record.tree_id,
            registry=self.owner.registry,
            initial_root_value=initial_root_value,
            plugin_context_data=self.owner.plugin_context_data,
            plugin_services=self.owner.plugin_services,
            application_scope=self.owner.application_scope,
            agent_id=record.agent_id,
            parent_agent_id=record.parent_agent_id,
            load_plugins=False,
        )
        unsubscribe = child.subscribe(
            lambda event, agent_id=record.agent_id: self._on_child_event(
                agent_id, event
            )
        )
        with self._lock:
            raced = self._sessions.get(record.agent_id)
            if raced is None:
                self._sessions[record.agent_id] = child
                self._unsubscribers[record.agent_id] = unsubscribe
                return child
        unsubscribe()
        child.close()
        return raced

    def _ensure_instruction(
        self,
        record: SubagentRecord,
        child: AgentSession,
    ) -> None:
        """Mount the durable main-agent instruction exactly once."""

        node_id = record.instruction_node_id or self._stable_node_id(
            "subagent_instruction",
            self.session_id,
            record.agent_id,
            record.task,
        )
        if not record.instruction_node_id:
            with self._lock:
                record.instruction_node_id = node_id
            self._persist()
        try:
            child.store.get_node(child.tree.id, node_id)
        except NodeNotFoundError:
            pass
        else:
            return
        if str(child.snapshot().get("status") or "") != "idle":
            return
        child.submit(
            record.task,
            run_id=record.round_id or None,
            node_id=node_id,
            permission_user_request=record.authorization_request,
            metadata={
                "source": "main_agent_instruction",
                "from_agent": record.parent_agent_id,
                "agent_id": record.agent_id,
                "parent_tree_id": self.owner.tree.id,
                "parent_node_id": str(self.owner.snapshot().get("leaf_id") or ""),
                "parent_run_id": record.round_id,
                "session_id": self.session_id,
                "round_id": record.round_id,
            },
        )
        with self._lock:
            record.current_run_id = child.current_run_id
        self._persist()

    def _on_child_event(self, agent_id: str, event: AgentSessionEvent) -> None:
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            if event.type == "session.state":
                status = str(event.data.get("status") or "")
                if record.status in _TERMINAL_STATUSES:
                    return
                if status:
                    record.status = status
            elif event.type == "run.failed":
                record.status = "failed"
                record.error = str(event.data.get("content") or "Agent run failed")
            elif event.type == "run.cancelled":
                record.status = "cancelled"
                record.error = str(event.data.get("cancel_reason") or "cancelled")
            else:
                return
        self._persist()

    async def spawn(
        self,
        requester_id: str,
        agent_id: str,
        task: str,
        *,
        effect_key: str = "",
    ) -> Mapping[str, Any]:
        requester = str(requester_id or "main").strip()
        if requester != self.owner.agent_id:
            raise PermissionError("Only the main agent can spawn subagents")
        normalized_id = self._normalize_agent_id(agent_id)
        normalized_task = str(task or "").strip()
        if not normalized_task:
            raise ValueError("task cannot be empty")
        current_round_id = str(self.owner.current_run_id or "")
        normalized_effect_key = str(effect_key or "")
        with self._lock:
            existing = self._records.get(normalized_id)
            if existing is not None and (
                (
                    normalized_effect_key
                    and existing.spawn_effect_key == normalized_effect_key
                )
                or (
                    existing.task == normalized_task
                    and existing.round_id == current_round_id
                )
            ):
                return existing.public_dict()
            if existing is not None:
                raise ValueError(f"subagent id already exists: {normalized_id}")
            record = SubagentRecord(
                agent_id=normalized_id,
                tree_id=self._child_tree_id(normalized_id),
                task=normalized_task,
                parent_agent_id=self.owner.agent_id,
                round_id=current_round_id,
                current_run_id=current_round_id,
                instruction_node_id=self._stable_node_id(
                    "subagent_instruction",
                    self.session_id,
                    normalized_id,
                    normalized_task,
                ),
                authorization_request=self.owner.permission_user_request,
                spawn_effect_key=normalized_effect_key,
            )
            self._records[normalized_id] = record
        self._persist()
        try:
            child = self._open_session(record)
            self._ensure_instruction(record, child)
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = str(exc)
                record.reported_node_id = self._stable_node_id(
                    "subagent_spawn_failed",
                    self.session_id,
                    normalized_id,
                    normalized_effect_key,
                )
            self._persist()
            raise
        return record.public_dict()

    def _resolve_target(self, target: str) -> str:
        normalized = str(target or "").strip()
        if normalized.casefold() in _MAIN_ALIASES:
            return self.owner.agent_id
        with self._lock:
            if normalized in self._records:
                return normalized
        raise ValueError(f"unknown subagent: {normalized}")

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        *,
        effect_key: str = "",
    ) -> Mapping[str, Any]:
        sender = str(from_agent or "").strip()
        target = self._resolve_target(to_agent)
        message = str(content or "").strip()
        if not message:
            raise ValueError("content cannot be empty")
        with self._lock:
            known_senders = {self.owner.agent_id, *self._records}
        if sender not in known_senders:
            raise ValueError(f"unknown sending agent: {sender}")

        from cyrene.runtime.inbox import send_message

        message_id = await send_message(
            sender,
            target,
            "message",
            message,
            round_id=str(self.owner.current_run_id or ""),
            session_id=self.session_id,
            dedup_key=(
                f"agent-message:{self.session_id}:{sender}:{target}:{effect_key}"
                if effect_key
                else ""
            ),
        )
        if not message_id:
            raise RuntimeError(f"failed to deliver inbox message to {target}")
        return {"message_id": message_id, "from": sender, "to": target}

    async def broadcast(
        self,
        from_agent: str,
        content: str,
        *,
        effect_key: str = "",
    ) -> Mapping[str, Any]:
        sender = str(from_agent or "").strip()
        message = str(content or "").strip()
        if not message:
            raise ValueError("content cannot be empty")
        with self._lock:
            participants = [self.owner.agent_id, *sorted(self._records)]
        if sender not in participants:
            raise ValueError(f"unknown sending agent: {sender}")
        targets = [agent_id for agent_id in participants if agent_id != sender]
        delivered: list[str] = []
        errors: dict[str, str] = {}
        for target in targets:
            try:
                await self.send(
                    sender,
                    target,
                    message,
                    effect_key=(f"{effect_key}:{target}" if effect_key else ""),
                )
            except Exception as exc:
                errors[target] = str(exc)
            else:
                delivered.append(target)
        return {"from": sender, "delivered": delivered, "errors": errors}

    def query(self, round_id: str = "") -> Mapping[str, Any]:
        requested_round = str(round_id or "").strip()
        with self._lock:
            records = [
                record.public_dict()
                for record in self._records.values()
                if not requested_round or record.round_id == requested_round
            ]
        return {
            "session_id": self.session_id,
            "main_agent_id": self.owner.agent_id,
            "round_id": requested_round or str(self.owner.current_run_id or ""),
            "subagents": sorted(records, key=lambda item: str(item["agent_id"])),
        }

    @staticmethod
    def _render_message(message: Mapping[str, Any]) -> str:
        sender = str(message.get("from") or "unknown")
        message_type = str(message.get("type") or "message")
        content = str(message.get("content") or "")
        return (
            "Message received through the agent inbox:\n\n"
            f"[from {sender}] ({message_type})\n{content}"
        )

    async def _deliver_inbox(self, session: AgentSession, agent_id: str) -> bool:
        if not session.is_idle:
            return False
        from cyrene.runtime.inbox import mark_read_count, read_unread_messages

        messages = await read_unread_messages(agent_id, session_id=self.session_id)
        if not messages:
            return False
        message = messages[0]
        message_id = str(message.get("message_id") or "")
        if not message_id:
            raise RuntimeError(f"Inbox message for {agent_id} has no message_id")
        node_id = self._stable_node_id(
            "agent_inbox",
            self.session_id,
            agent_id,
            message_id,
        )
        try:
            session.store.get_node(session.tree.id, node_id)
        except NodeNotFoundError:
            pass
        else:
            await mark_read_count(agent_id, 1, session_id=self.session_id)
            return True
        rendered = self._render_message(message)
        if agent_id == self.owner.agent_id:
            run_id = str(message.get("round_id") or self.owner.current_run_id or "")
        else:
            with self._lock:
                record = self._records[agent_id]
                child_run_id = record.current_run_id or record.round_id
            run_id = str(message.get("round_id") or child_run_id or "")
        session.submit(
            rendered,
            run_id=run_id or None,
            node_id=node_id,
            permission_user_request=(
                self.owner.permission_user_request
                if (
                    agent_id == self.owner.agent_id
                    or str(message.get("from") or "") == self.owner.agent_id
                )
                else session.permission_user_request
            ),
            metadata={
                "source": "agent_inbox",
                "message_id": message_id,
                "from_agent": str(message.get("from") or ""),
                "message_type": str(message.get("type") or "message"),
                "message": deepcopy(message),
            },
        )
        await mark_read_count(agent_id, 1, session_id=self.session_id)
        if agent_id != self.owner.agent_id:
            with self._lock:
                self._records[agent_id].status = "running"
                self._records[agent_id].current_run_id = session.current_run_id
            self._persist()
        return True

    async def _deliver_child_inboxes(self) -> bool:
        from cyrene.runtime.inbox import get_unread_count

        delivered = False
        with self._lock:
            records = tuple(self._records.values())
        for record in records:
            if get_unread_count(record.agent_id, session_id=self.session_id) <= 0:
                continue
            child = self._open_session(record)
            delivered = await self._deliver_inbox(child, record.agent_id) or delivered
        return delivered

    async def _report_finished_children(self) -> bool:
        from cyrene.runtime.inbox import send_message

        reported = False
        with self._lock:
            items = tuple(self._sessions.items())
        for agent_id, child in items:
            snapshot = child.snapshot()
            if str(snapshot.get("status") or "") != "idle":
                continue
            with self._lock:
                record = self._records[agent_id]
                run_id = record.current_run_id or child.current_run_id or record.round_id
                reported_node_id = record.reported_node_id
            output = child.final_output(run_id or None)
            if output is None:
                continue
            node_id = str(output.get("node_id") or "")
            if not node_id or node_id == reported_node_id:
                continue
            failed = bool(output.get("error"))
            cancelled = bool(output.get("cancelled"))
            content = str(output.get("content") or output.get("cancel_reason") or "")
            status = "cancelled" if cancelled else "failed" if failed else "done"
            message_id = await send_message(
                agent_id,
                self.owner.agent_id,
                "result",
                content,
                round_id=run_id,
                session_id=self.session_id,
                dedup_key=(
                    f"subagent-result:{self.session_id}:{agent_id}:{node_id}"
                ),
            )
            if not message_id:
                raise RuntimeError(
                    f"failed to deliver result from subagent {agent_id}"
                )
            with self._lock:
                record.status = status
                record.result = "" if failed or cancelled else content
                record.error = content if failed or cancelled else ""
                record.reported_node_id = node_id
                record.current_run_id = run_id
            self._persist()
            reported = True
        return reported

    async def drive(self) -> bool:
        """Run children to safe turn boundaries and inject resulting main inbox."""

        with self._lock:
            record_count = len(self._records)
        max_cycles = max(
            16,
            self.owner.max_model_calls * max(2, record_count + 1) * 4,
        )
        for _cycle in range(max_cycles):
            with self._lock:
                records = tuple(self._records.values())
            for record in records:
                if (
                    record.status not in _TERMINAL_STATUSES
                    or not record.reported_node_id
                ):
                    child = self._open_session(record)
                    self._ensure_instruction(record, child)
            with self._lock:
                sessions = tuple(self._sessions.values())
            if sessions:
                await asyncio.gather(*(session.drain() for session in sessions))

            if await self._deliver_child_inboxes():
                continue
            await self._report_finished_children()
            if await self._deliver_inbox(self.owner, self.owner.agent_id):
                return True

            with self._lock:
                active = any(
                    record.status not in _TERMINAL_STATUSES
                    or not record.reported_node_id
                    for record in self._records.values()
                )
            if not active:
                return False

            # A restored record may not have opened before drive was entered.
            stalled: list[SubagentRecord] = []
            with self._lock:
                pending_records = tuple(self._records.values())
            for record in pending_records:
                if (
                    record.status not in _TERMINAL_STATUSES
                    or not record.reported_node_id
                ):
                    child = self._open_session(record)
                    self._ensure_instruction(record, child)
                    if str(child.snapshot().get("status") or "") == "idle":
                        run_id = (
                            record.current_run_id
                            or child.current_run_id
                            or record.round_id
                        )
                        if child.final_output(run_id or None) is None:
                            stalled.append(record)
            if stalled:
                from cyrene.runtime.inbox import send_message

                for record in stalled:
                    error = "Subagent became idle without a terminal response."
                    with self._lock:
                        record.status = "failed"
                        record.error = error
                        record.reported_node_id = self._stable_node_id(
                            "subagent_stalled",
                            self.session_id,
                            record.agent_id,
                            record.current_run_id,
                        )
                    message_id = await send_message(
                        record.agent_id,
                        self.owner.agent_id,
                        "result",
                        error,
                        round_id=record.current_run_id or record.round_id,
                        session_id=self.session_id,
                        dedup_key=(
                            f"subagent-stalled:{self.session_id}:"
                            f"{record.agent_id}:{record.current_run_id}"
                        ),
                    )
                    if not message_id:
                        raise RuntimeError(
                            f"failed to report stalled subagent {record.agent_id}"
                        )
                self._persist()
        self.request_cancel_all("subagent_coordination_limit")
        raise RuntimeError(
            "Subagent coordination exceeded its bounded turn limit"
        )

    def request_cancel_all(self, reason: str) -> None:
        with self._lock:
            active_ids = {
                record.agent_id
                for record in self._records.values()
                if record.status not in _TERMINAL_STATUSES
            }
            sessions = tuple(
                (agent_id, session)
                for agent_id, session in self._sessions.items()
                if agent_id in active_ids
            )
            for agent_id in active_ids:
                record = self._records[agent_id]
                record.status = "cancelled"
                record.error = str(reason)
                record.reported_node_id = self._stable_node_id(
                    "subagent_cancelled",
                    self.session_id,
                    agent_id,
                    record.current_run_id,
                )
        for agent_id, session in sessions:
            session.request_cancel(reason)
        self._persist()

    async def cancel_all(self, reason: str) -> None:
        with self._lock:
            sessions = tuple(
                session
                for session in self._sessions.values()
                if str(session.snapshot().get("status") or "") != "idle"
            )
        await asyncio.gather(
            *(session.cancel(reason, timeout=5.0) for session in sessions),
            return_exceptions=True,
        )
        self._persist()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribers = tuple(self._unsubscribers.values())
            sessions = tuple(self._sessions.values())
            self._unsubscribers.clear()
            self._sessions.clear()
        for unsubscribe in unsubscribers:
            unsubscribe()
        for session in sessions:
            session.close()
        with _ACTIVE_MANAGERS_LOCK:
            if _ACTIVE_MANAGERS.get(self.session_id) is self:
                _ACTIVE_MANAGERS.pop(self.session_id, None)


def active_subagent_manager(session_id: str) -> SubagentManager | None:
    with _ACTIVE_MANAGERS_LOCK:
        return _ACTIVE_MANAGERS.get(str(session_id or ""))


def active_subagent_snapshot(session_id: str) -> Mapping[str, Any] | None:
    manager = active_subagent_manager(session_id)
    return manager.query() if manager is not None else None


def format_subagent_query(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


__all__ = [
    "SubagentManager",
    "SubagentRecord",
    "active_subagent_manager",
    "active_subagent_snapshot",
    "format_subagent_query",
]

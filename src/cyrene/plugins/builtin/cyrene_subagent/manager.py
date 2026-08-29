"""ContextTree-backed subagent sessions coordinated through the existing inbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
import weakref
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Any

from cyrene.core.context import NodeNotFoundError
from cyrene.core.plugin import (
    plugin_session_state,
    with_plugin_session_state,
    without_plugin_session_state,
)
from cyrene.localization import localized

from .contracts import (
    DISCUSSION_MODE,
    SUMMARY_MODE,
    TERMINAL_STATUSES,
    DiscussionLimits,
    DiscussionState,
    ExecutionLimits,
    FinishRequest,
    SubagentRecord,
    normalized_criteria,
    normalized_mode,
    utc_now,
)

if TYPE_CHECKING:
    from cyrene.core.session import AgentSession, AgentSessionEvent


_PACK_ID = "cyrene_subagent"
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAIN_ALIASES = frozenset(
    {"main", "main_agent", "cyrene", "danny", "host", "coordinator", "parent"}
)
_ACTIVE_MANAGERS_LOCK = threading.RLock()
_ACTIVE_MANAGERS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()


class SubagentManager:
    """Own child AgentSessions while keeping messages in the existing inbox."""

    def __init__(self, owner: AgentSession) -> None:
        self.owner = owner
        self.session_id = str(
            owner.plugin_context_data.get("session_id") or owner.tree.id
        )
        self._lock = threading.RLock()
        self._records: dict[str, SubagentRecord] = self._load_records()
        self._history: list[dict[str, Any]] = self._load_history()
        self._discussions: dict[str, DiscussionState] = self._load_discussions()
        self._sessions: dict[str, AgentSession] = {}
        self._unsubscribers: dict[str, Any] = {}
        self._tool_batch_progress: dict[str, bool] = {}
        try:
            self._event_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None
        self._closed = False
        with _ACTIVE_MANAGERS_LOCK:
            _ACTIVE_MANAGERS[self.session_id] = self

    def restore(self) -> None:
        """Reopen every non-terminal child tree so its AgentSession can recover."""

        for record in tuple(self._records.values()):
            if (
                record.status not in TERMINAL_STATUSES
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
        return any(record.status not in TERMINAL_STATUSES for record in records)

    @property
    def has_pending_work(self) -> bool:
        with self._lock:
            records = tuple(self._records.values())
        if any(
            record.status not in TERMINAL_STATUSES
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

    def _pack_state(self) -> dict[str, Any]:
        root = self.owner.store.get_node(self.owner.tree.id, self.owner.tree.root_id)
        value = root.value if isinstance(root.value, Mapping) else {}
        return plugin_session_state(value, _PACK_ID)

    def _load_history(self) -> list[dict[str, Any]]:
        raw = self._pack_state().get("history")
        return [dict(item) for item in raw if isinstance(item, Mapping)] \
            if isinstance(raw, list) else []

    def _load_discussions(self) -> dict[str, DiscussionState]:
        raw = self._pack_state().get("discussions")
        if not isinstance(raw, Mapping):
            return {}
        return {
            str(key): DiscussionState.from_value(str(key), item)
            for key, item in raw.items()
            if str(key).strip() and isinstance(item, Mapping)
        }

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
            public_records = {
                agent_id: record.public_dict()
                for agent_id, record in sorted(self._records.items())
            }
            value = with_plugin_session_state(
                root.value,
                _PACK_ID,
                {
                    "records": records,
                    "history": [dict(item) for item in self._history[-500:]],
                    "discussions": {
                        key: state.as_dict()
                        for key, state in sorted(self._discussions.items())
                    },
                    "child_context_ids": [
                        record.tree_id
                        for record in self._records.values()
                        if record.tree_id
                    ],
                    "public_snapshot": {
                        "subagents": public_records,
                        "subagent_history": [
                            dict(item) for item in self._history[-500:]
                        ],
                        "subagent_discussions": {
                            key: state.as_dict()
                            for key, state in sorted(self._discussions.items())
                        },
                    },
                },
            )
            self.owner.store.update_node(
                self.owner.tree.id,
                self.owner.tree.root_id,
                value,
            )

    def _child_tree_id(self, agent_id: str, generation: int = 1) -> str:
        suffix = "" if generation <= 1 else f".g{generation}"
        return f"{self.owner.tree.id}.subagent.{agent_id}{suffix}"

    @staticmethod
    def _stable_node_id(kind: str, *parts: str) -> str:
        payload = "\x00".join(str(part) for part in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{kind}_{digest[:32]}"

    async def _publish_runtime_event(self, event: Mapping[str, Any]) -> None:
        self._event_loop = asyncio.get_running_loop()
        raw = self.owner.plugin_context_data.get("run_context")
        run_context = raw if isinstance(raw, Mapping) else {}
        writer = (
            self.owner.plugin_services.get("runtime_events")
            or run_context.get("runtime_event_writer")
            or self.owner.plugin_context_data.get("runtime_event_writer")
        )
        if not callable(writer):
            return
        result = writer(dict(event))
        if asyncio.iscoroutine(result):
            await result

    def _schedule_runtime_event(self, event: Mapping[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._event_loop
            if loop is None or loop.is_closed():
                return
            asyncio.run_coroutine_threadsafe(
                self._publish_runtime_event(event), loop
            )
            return
        self._event_loop = loop
        loop.create_task(self._publish_runtime_event(event))

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
        plugin_context_data = self.owner.plugin_context_data
        plugin_context_data.update({
            "subagent_mode": record.mode,
            "subagent_role": record.role,
            "subagent_discussion_id": record.discussion_id,
            "subagent_generation": record.generation,
        })
        if record.use_secondary:
            plugin_context_data["model_route"] = "secondary"
        plugin_services = self.owner.plugin_services
        plugin_services["subagents"] = self
        context_limit_resolver = plugin_services.get("model_context_limit")
        configured_limit = (
            ExecutionLimits.current().max_context_tokens
            if record.mode == "execution" else 0
        )
        if callable(context_limit_resolver) and (
            record.use_secondary or configured_limit
        ):
            def subagent_context_limit(
                tree_id: str,
                *,
                route: str = "primary",
            ) -> int:
                resolved = max(
                    0,
                    int(
                        context_limit_resolver(
                            tree_id,
                            route="secondary" if record.use_secondary else route,
                        )
                        or 0
                    ),
                )
                if configured_limit and resolved:
                    return min(configured_limit, resolved)
                return configured_limit or resolved

            plugin_services["model_context_limit"] = subagent_context_limit
        child = AgentSession(
            self.owner.data_directory,
            self.owner.workspace,
            self.owner.plugin_directory,
            model_plugin=self.owner.model_plugin,
            max_model_calls=(8 if record.mode == SUMMARY_MODE else None),
            tree_id=record.tree_id,
            registry=self.owner.registry,
            initial_root_value=initial_root_value,
            plugin_context_data=plugin_context_data,
            plugin_services=plugin_services,
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
            record.touch()
        self._persist()

    def _on_child_event(self, agent_id: str, event: AgentSessionEvent) -> None:
        publish = False
        with self._lock:
            record = self._records.get(agent_id)
            if record is None:
                return
            if event.type == "session.state":
                status = str(event.data.get("status") or "")
                if record.status in TERMINAL_STATUSES or record.status == "waiting":
                    return
                if status:
                    record.status = "running" if status != "idle" else record.status
                    publish = True
            elif event.type in {"assistant.tool_calls", "assistant.completed"}:
                record.metrics.model_turns += 1
                usage = event.data.get("usage")
                usage = usage if isinstance(usage, Mapping) else {}
                observation = event.data.get("usage_observation")
                observation = observation if isinstance(observation, Mapping) else usage
                prompt = int(
                    observation.get("prompt_tokens")
                    or observation.get("input_tokens") or 0
                )
                completion = int(
                    observation.get("completion_tokens")
                    or observation.get("output_tokens") or 0
                )
                record.metrics.prompt_tokens += max(0, prompt)
                record.metrics.completion_tokens += max(0, completion)
                record.metrics.total_tokens += max(
                    0, int(observation.get("total_tokens") or prompt + completion)
                )
                try:
                    from cyrene.model_runtime.pricing import (
                        effective_price,
                        estimate_cost,
                        to_usd,
                    )

                    price = to_usd(effective_price(str(event.data.get("model") or "")))
                    record.metrics.estimated_cost_usd += max(
                        0.0,
                        estimate_cost(
                            price,
                            prompt,
                            completion,
                            cache_hit_tokens=int(
                                observation.get("cached_prompt_tokens") or 0
                            ),
                            cache_miss_tokens=int(
                                observation.get("cache_miss_tokens") or 0
                            ),
                        ),
                    )
                except Exception:
                    pass
                self._update_resource_finalization(record)
                publish = True
            elif event.type == "tools.completed":
                progress = self._tool_batch_progress.pop(record.agent_id, False)
                record.metrics.no_progress_turns = (
                    0 if progress else record.metrics.no_progress_turns + 1
                )
                self._update_resource_finalization(record)
                publish = True
            elif event.type == "run.failed":
                record.status = "failed"
                record.outcome = "error"
                record.stop_reason = "run_failed"
                record.error = str(event.data.get("content") or "Agent run failed")
                publish = True
            elif event.type == "run.cancelled":
                record.status = "cancelled"
                record.outcome = "cancelled"
                record.stop_reason = str(
                    event.data.get("cancel_reason") or "cancelled"
                )
                record.error = str(event.data.get("cancel_reason") or "cancelled")
                publish = True
            else:
                return
            record.touch()
        self._persist()
        if publish:
            self._schedule_runtime_event({
                "type": "subagent_update",
                "session_id": self.session_id,
                "runId": record.round_id,
                "round_id": record.round_id,
                "agent_id": record.agent_id,
                "status": record.status,
                "task": record.task,
                "mode": record.mode,
                "role": record.role,
                "outcome": record.outcome,
                "metrics": record.metrics.as_dict(),
            })

    @staticmethod
    def _elapsed_seconds(record: SubagentRecord) -> float:
        try:
            started = datetime.fromisoformat(record.lease_started_at.replace("Z", "+00:00"))
            return max(0.0, time.time() - started.timestamp())
        except (TypeError, ValueError):
            return 0.0

    def _update_resource_finalization(self, record: SubagentRecord) -> None:
        if record.finalization_reason:
            return
        if record.mode == DISCUSSION_MODE:
            limits = DiscussionLimits.current(record.discussion_max_messages)
            state = self._discussions.get(record.discussion_id)
            if self._elapsed_seconds(record) >= limits.max_wall_seconds:
                record.finalization_reason = "discussion_wall_time_exhausted"
            elif record.metrics.lease_tool_calls >= limits.max_tool_calls:
                record.finalization_reason = "discussion_tool_budget_exhausted"
            elif state is not None and state.no_new_info_rounds >= limits.no_new_info_rounds:
                record.finalization_reason = "discussion_no_new_information"
            return
        if record.mode == SUMMARY_MODE:
            return
        limits = ExecutionLimits.current()
        if self._elapsed_seconds(record) >= limits.max_wall_seconds:
            record.finalization_reason = "execution_wall_time_exhausted"
        elif record.metrics.lease_tool_calls >= limits.max_tool_calls:
            record.finalization_reason = "execution_tool_budget_exhausted"
        elif (
            limits.max_cost_usd > 0
            and record.metrics.estimated_cost_usd >= limits.max_cost_usd
        ):
            record.finalization_reason = "execution_cost_budget_exhausted"
        elif record.metrics.no_progress_turns >= limits.no_progress_turns:
            record.finalization_reason = "execution_no_progress"

    def mode_context(self, agent_id: str) -> str:
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return ""
            finalization_reason = record.finalization_reason
            criteria = list(record.success_criteria)
            mode = record.mode
            role = record.role
            discussion_id = record.discussion_id
            execution_limits = ExecutionLimits.current()
            checkpoint_due = bool(
                record.metrics.lease_tool_calls
                and record.metrics.lease_tool_calls % execution_limits.checkpoint_calls == 0
            )
            discussion_limits = DiscussionLimits.current(
                record.discussion_max_messages
            )
        completion_contract = (
            "Before finishing, write the complete result in normal assistant "
            "content, then call `quit` alone with completion_status."
        )
        if criteria:
            completion_contract += (
                " For completed work, criteria_evidence must contain a non-empty "
                "evidence entry whose criterion exactly matches every item below:\n"
                + "\n".join(f"- {item}" for item in criteria)
            )
        if mode == DISCUSSION_MODE:
            role_instructions = ""
            if role == "moderator":
                role_instructions = (
                    " You are the moderator: speak first, advance one participant "
                    "at a time, stop repetition, and synthesize the discussion."
                )
            elif role == "participant":
                role_instructions = (
                    " You are a participant: wait for a substantive prompt, then "
                    "make one evidence-bearing contribution without greetings or "
                    "readiness messages."
                )
            text = (
                "## Discussion Worker Mode\n"
                f"Discussion: {discussion_id}. Role: {role or 'participant'}.\n"
                "Use send_agent_message for targeted peer coordination and broadcast "
                "only when every peer needs the same new information. Do not send to "
                "the main Agent; it collects your final response automatically. "
                "Avoid greetings, acknowledgements, repeated claims, and empty status "
                "updates."
                f"{role_instructions}\n"
                "Runtime limits: "
                f"{discussion_limits.max_rounds} rounds, "
                f"{discussion_limits.max_messages_per_agent} messages from you, "
                f"{discussion_limits.max_total_messages} total messages, "
                f"{discussion_limits.max_message_chars} characters per message, "
                f"{discussion_limits.max_tool_calls} tool calls, and "
                f"{discussion_limits.max_wall_seconds} seconds.\n"
                + completion_contract
            )
        elif mode == SUMMARY_MODE:
            text = (
                "## Summary Worker Mode\n"
                "Synthesize the supplied peer transcripts and inbox communications "
                "into one faithful result for the parent. Preserve disagreements and "
                "evidence; do not start new work. " + completion_contract
            )
        else:
            text = (
                "## Execution Worker Mode\n"
                "Work independently toward the assigned deliverable. Peer messaging "
                "is unavailable in execution mode. Re-check explicit success criteria "
                f"every {execution_limits.checkpoint_calls} tool calls and report an "
                "honest partial or blocked outcome when completion is impossible. "
                "Resource fuses: "
                f"{execution_limits.max_tool_calls} tool calls, "
                f"{execution_limits.max_wall_seconds} seconds, "
                f"{execution_limits.no_progress_turns} no-progress turns, "
                f"${execution_limits.max_cost_usd:.2f}, and "
                + (
                    f"{execution_limits.max_context_tokens} context tokens. "
                    if execution_limits.max_context_tokens else
                    "the selected model's context limit. "
                )
                + completion_contract
            )
        if finalization_reason:
            text += (
                "\n\n## Finalization Required\n"
                f"The runtime fuse `{finalization_reason}` was reached. Stop new tool "
                "work, provide the best evidence-backed result available, and call quit "
                "with partial or blocked status."
            )
        elif mode == "execution" and checkpoint_due:
            text += (
                "\n\n## Execution Checkpoint\n"
                "Re-evaluate every success criterion against concrete evidence now. "
                "Continue working if any achievable criterion remains unmet; this "
                "checkpoint is not itself a stop signal."
            )
        return text

    def review_tool(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return {"decision": "allow"}
            self._update_resource_finalization(record)
            if record.finish.accepted and tool_name != "quit":
                return {
                    "decision": "block",
                    "reason": "quit was accepted; return the complete final response now",
                }
            if record.finalization_reason and tool_name != "quit":
                return {
                    "decision": "block",
                    "reason": (
                        f"subagent finalization required: {record.finalization_reason}; "
                        "return the best available result and call quit"
                    ),
                }
            if record.mode != DISCUSSION_MODE and tool_name in {
                "send_agent_message", "broadcast_agent_message"
            }:
                return {
                    "decision": "block",
                    "reason": "peer communication requires discussion mode",
                }
            return {"decision": "allow", "arguments": dict(arguments)}

    def record_tool_result(
        self,
        agent_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        if tool_name == "quit":
            return
        encoded_args = json.dumps(
            dict(arguments), ensure_ascii=False, sort_keys=True, default=str
        )
        signature = hashlib.sha256(
            f"{tool_name}:{encoded_args}".encode("utf-8")
        ).hexdigest()
        result_text = str(result.get("value") or result.get("error") or "")
        fingerprint = hashlib.sha256(
            " ".join(result_text.split()).encode("utf-8")
        ).hexdigest()
        observation_markers = (
            "read", "search", "fetch", "list", "get", "query", "recall",
            "snapshot", "status", "check", "inspect", "analyze",
        )
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return
            record.metrics.tool_calls += 1
            record.metrics.lease_tool_calls += 1
            is_observation = any(
                marker in tool_name.casefold() for marker in observation_markers
            )
            progress = (
                fingerprint not in record.seen_result_fingerprints
                if is_observation else
                signature not in record.seen_tool_signatures
                or fingerprint not in record.seen_result_fingerprints
            )
            record.seen_tool_signatures.append(signature)
            record.seen_result_fingerprints.append(fingerprint)
            self._tool_batch_progress[record.agent_id] = (
                self._tool_batch_progress.get(record.agent_id, False) or progress
            )
            self._update_resource_finalization(record)
            record.touch()
        self._persist()

    def complete_tool_batch(self, agent_id: str) -> None:
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return
            progress = self._tool_batch_progress.pop(record.agent_id, False)
            record.metrics.no_progress_turns = (
                0 if progress else record.metrics.no_progress_turns + 1
            )
            self._update_resource_finalization(record)
            record.touch()
        self._persist()

    def observe_context(self, agent_id: str, tokens: int, token_limit: int) -> None:
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None or record.mode != "execution":
                return
            configured = ExecutionLimits.current().max_context_tokens
            effective = configured or max(0, int(token_limit or 0))
            if effective and int(tokens or 0) >= effective:
                record.finalization_reason = "execution_context_budget_exhausted"
                record.touch()
        self._persist()

    def request_finish(
        self,
        agent_id: str,
        completion_status: str,
        criteria_evidence: Any,
    ) -> Mapping[str, Any]:
        normalized_status = str(completion_status or "").strip().lower()
        if normalized_status not in {"completed", "partial", "blocked"}:
            return {
                "accepted": False,
                "error": "completion_status must be completed, partial, or blocked",
            }
        evidence = [
            {
                "criterion": str(item.get("criterion") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            }
            for item in criteria_evidence or ()
            if isinstance(item, Mapping)
        ]
        with self._lock:
            record = self._records.get(str(agent_id))
            if record is None:
                return {"accepted": False, "error": "unknown subagent"}
            if normalized_status == "completed":
                covered = {
                    item["criterion"] for item in evidence
                    if item["criterion"] and item["evidence"]
                }
                missing = [
                    criterion for criterion in record.success_criteria
                    if criterion not in covered
                ]
                if missing:
                    return {
                        "accepted": False,
                        "error": "missing criteria evidence",
                        "missing_criteria": missing,
                    }
            record.finish = FinishRequest(
                completion_status=normalized_status,
                criteria_evidence=evidence,
                accepted=True,
                requested_at=utc_now(),
            )
            record.touch()
        self._persist()
        return {
            "accepted": True,
            "completion_status": normalized_status,
            "instruction": (
                "Return the complete final result now without additional tool calls."
            ),
        }

    async def spawn(
        self,
        requester_id: str,
        agent_id: str,
        task: str,
        *,
        mode: str = "execution",
        success_criteria: Any = None,
        discussion_max_messages: int | None = None,
        discussion_id: str = "",
        use_secondary: bool = False,
        role: str = "",
        effect_key: str = "",
    ) -> Mapping[str, Any]:
        requester = str(requester_id or "main").strip()
        if requester != self.owner.agent_id:
            raise PermissionError("Only the main agent can spawn subagents")
        normalized_id = self._normalize_agent_id(agent_id)
        normalized_task = str(task or "").strip()
        if not normalized_task:
            raise ValueError("task cannot be empty")
        normalized_role = str(role or "").strip().lower()
        if normalized_role and normalized_role not in {"moderator", "participant"}:
            raise ValueError("role must be 'moderator' or 'participant'")
        effective_mode = normalized_mode(mode, normalized_role)
        criteria = normalized_criteria(success_criteria)
        if discussion_max_messages is not None:
            try:
                discussion_max_messages = max(
                    1, min(50, int(discussion_max_messages))
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("max_messages must be an integer") from exc
        current_round_id = str(self.owner.current_run_id or "")
        effective_discussion_id = (
            str(discussion_id or current_round_id).strip()
            if effective_mode == DISCUSSION_MODE else ""
        )
        normalized_effect_key = str(effect_key or "")
        generation = 1
        retired_session: AgentSession | None = None
        retired_unsubscribe: Any = None
        with self._lock:
            existing = self._records.get(normalized_id)
            if (
                existing is not None
                and normalized_effect_key
                and existing.spawn_effect_key == normalized_effect_key
            ):
                return existing.public_dict()
            if existing is not None and existing.status not in TERMINAL_STATUSES:
                raise ValueError(f"subagent id already exists: {normalized_id}")
            if effective_mode == DISCUSSION_MODE and normalized_role == "moderator":
                existing_discussion = self._discussions.get(effective_discussion_id)
                if (
                    existing_discussion is not None
                    and existing_discussion.moderator
                    and existing_discussion.moderator != normalized_id
                ):
                    raise ValueError(
                        "discussion already has moderator: "
                        + existing_discussion.moderator
                    )
            if existing is not None:
                self._history.append(existing.as_dict())
                generation = existing.generation + 1
                retired_session = self._sessions.pop(normalized_id, None)
                retired_unsubscribe = self._unsubscribers.pop(normalized_id, None)
            record = SubagentRecord(
                agent_id=normalized_id,
                tree_id=self._child_tree_id(normalized_id, generation),
                task=normalized_task,
                parent_agent_id=self.owner.agent_id,
                round_id=current_round_id,
                mode=effective_mode,
                role=normalized_role,
                success_criteria=criteria,
                discussion_id=effective_discussion_id,
                discussion_max_messages=discussion_max_messages,
                use_secondary=bool(use_secondary),
                current_run_id=current_round_id,
                instruction_node_id=self._stable_node_id(
                    "subagent_instruction",
                    self.session_id,
                    normalized_id,
                    normalized_task,
                ),
                authorization_request=self.owner.permission_user_request,
                spawn_effect_key=normalized_effect_key,
                generation=generation,
            )
            self._records[normalized_id] = record
            if effective_mode == DISCUSSION_MODE:
                state = self._discussions.setdefault(
                    effective_discussion_id,
                    DiscussionState(
                        discussion_id=effective_discussion_id,
                        round_id=current_round_id,
                    ),
                )
                if normalized_id not in state.participants:
                    state.participants.append(normalized_id)
                if normalized_role == "moderator":
                    state.moderator = normalized_id
        if callable(retired_unsubscribe):
            retired_unsubscribe()
        if retired_session is not None:
            retired_session.close()
        self._persist()
        if generation > 1:
            from cyrene.runtime.inbox import clear_inbox

            await clear_inbox(normalized_id, session_id=self.session_id)
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
        await self._publish_runtime_event({
            "type": "subagent_update",
            "session_id": self.session_id,
            "runId": record.round_id,
            "round_id": record.round_id,
            "agent_id": record.agent_id,
            "status": record.status,
            "task": record.task,
            "mode": record.mode,
            "role": record.role,
            "outcome": record.outcome,
            "metrics": record.metrics.as_dict(),
        })
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
        _count_discussion: bool = True,
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

        claim: tuple[DiscussionState, SubagentRecord, str] | None = None
        if sender != self.owner.agent_id:
            if target == self.owner.agent_id:
                raise ValueError(
                    "The main-agent inbox is reserved for user guidance. "
                    "Put the final conclusion in the assistant response and call quit."
                )
            with self._lock:
                source = self._records[sender]
                peer = self._records.get(target)
                if source.mode != DISCUSSION_MODE:
                    raise ValueError("peer communication requires discussion mode")
                if peer is None or peer.mode != DISCUSSION_MODE:
                    raise ValueError(f"agent '{target}' is not a discussion peer")
                if peer.discussion_id != source.discussion_id:
                    raise ValueError(
                        f"agent '{target}' is outside discussion {source.discussion_id}"
                    )
                state = self._discussions[source.discussion_id]
                limits = DiscussionLimits.current(source.discussion_max_messages)
                previous = next(
                    (
                        item for item in state.transcript
                        if effect_key and item.get("effect_key") == effect_key
                    ),
                    None,
                )
                if previous is None and _count_discussion:
                    if len(message) > limits.max_message_chars:
                        raise ValueError(
                            f"discussion message exceeds {limits.max_message_chars} characters"
                        )
                    sent = state.per_agent_messages.get(sender, 0)
                    if sent >= limits.max_messages_per_agent:
                        raise ValueError("per-agent discussion message limit reached")
                    if state.messages_total >= limits.max_total_messages:
                        raise ValueError("total discussion message limit reached")
                    if state.rounds >= limits.max_rounds and (
                        source.role == "moderator" or not state.moderator
                    ):
                        raise ValueError("discussion round limit reached")
                    state.per_agent_messages[sender] = sent + 1
                    state.messages_total += 1
                    source.metrics.messages += 1
                    claim = (state, source, effect_key)

        from cyrene.runtime.inbox import send_message
        try:
            message_id = await send_message(
                sender,
                target,
                "message",
                message,
                round_id=(
                    self._records[sender].round_id
                    if sender != self.owner.agent_id
                    else str(self.owner.current_run_id or "")
                ),
                session_id=self.session_id,
                dedup_key=(
                    f"agent-message:{self.session_id}:{sender}:{target}:{effect_key}"
                    if effect_key
                    else ""
                ),
            )
        except Exception:
            if claim is not None:
                with self._lock:
                    state, source, _ = claim
                    state.messages_total = max(0, state.messages_total - 1)
                    state.per_agent_messages[sender] = max(
                        0, state.per_agent_messages.get(sender, 1) - 1
                    )
                    source.metrics.messages = max(0, source.metrics.messages - 1)
            raise
        if not message_id:
            raise RuntimeError(f"failed to deliver inbox message to {target}")
        if claim is not None:
            with self._lock:
                state, source, claim_key = claim
                fingerprint = hashlib.sha256(
                    " ".join(message.casefold().split()).encode("utf-8")
                ).hexdigest()
                adds_information = fingerprint not in state.fingerprints
                if adds_information:
                    state.fingerprints.append(fingerprint)
                starts_round = source.role == "moderator" or not state.moderator
                if starts_round:
                    if state.rounds:
                        if state.current_round_has_new_information:
                            state.no_new_info_rounds = 0
                        else:
                            state.no_new_info_rounds += 1
                    state.rounds += 1
                    state.current_round_has_new_information = adds_information
                elif adds_information:
                    state.current_round_has_new_information = True
                source.metrics.discussion_rounds = state.rounds
                source.touch()
                state.transcript.append({
                    "id": message_id,
                    "effect_key": claim_key,
                    "from": sender,
                    "to": target,
                    "content": message,
                    "type": "message",
                    "round_id": source.round_id,
                    "discussion_id": source.discussion_id,
                    "created_at": utc_now(),
                    "adds_information": adds_information,
                })
            self._persist()
        elif sender == self.owner.agent_id:
            with self._lock:
                peer = self._records.get(target)
                state = (
                    self._discussions.get(peer.discussion_id)
                    if peer is not None and peer.discussion_id else None
                )
                duplicate = bool(
                    state is not None
                    and effect_key
                    and any(
                        item.get("effect_key") == effect_key
                        for item in state.transcript
                    )
                )
                if state is not None and not duplicate:
                    state.transcript.append({
                        "id": message_id,
                        "effect_key": effect_key,
                        "from": sender,
                        "to": target,
                        "content": message,
                        "type": "guidance",
                        "round_id": peer.round_id,
                        "discussion_id": peer.discussion_id,
                        "created_at": utc_now(),
                        "adds_information": True,
                    })
            if state is not None and not duplicate:
                self._persist()
        await self._publish_runtime_event({
            "type": "agent_comm",
            "from": sender,
            "to": target,
            "content": message,
            "summary": message[:100].replace("\n", " "),
            "msg_type": "message",
            "round_id": (
                self._records[sender].round_id
                if sender != self.owner.agent_id else str(self.owner.current_run_id or "")
            ),
            "discussion_id": (
                self._records[sender].discussion_id
                if sender != self.owner.agent_id else ""
            ),
            "timestamp": utc_now(),
        })
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
        if sender == self.owner.agent_id:
            targets = [agent_id for agent_id in participants if agent_id != sender]
        else:
            with self._lock:
                source = self._records[sender]
                if source.mode != DISCUSSION_MODE:
                    raise ValueError("peer communication requires discussion mode")
                targets = [
                    agent_id for agent_id, record in self._records.items()
                    if agent_id != sender
                    and record.mode == DISCUSSION_MODE
                    and record.discussion_id == source.discussion_id
                ]
        delivered: list[str] = []
        errors: dict[str, str] = {}
        counted = False
        for target in targets:
            try:
                await self.send(
                    sender,
                    target,
                    message,
                    effect_key=(f"{effect_key}:{target}" if effect_key else ""),
                    _count_discussion=not counted,
                )
            except Exception as exc:
                errors[target] = str(exc)
            else:
                delivered.append(target)
                counted = True
        return {"from": sender, "delivered": delivered, "errors": errors}

    async def broadcast_user_guidance(
        self,
        content: str,
        *,
        effect_key: str = "",
    ) -> Mapping[str, Any]:
        """Forward accepted user guidance only to active children of this run."""

        message = str(content or "").strip()
        if not message:
            return {"from": self.owner.agent_id, "delivered": [], "errors": {}}
        current_run_id = str(self.owner.current_run_id or "")
        with self._lock:
            targets = [
                agent_id
                for agent_id, record in sorted(self._records.items())
                if record.status not in TERMINAL_STATUSES
                and (
                    not current_run_id
                    or record.round_id == current_run_id
                    or record.current_run_id == current_run_id
                )
            ]
        delivered: list[str] = []
        errors: dict[str, str] = {}
        for target in targets:
            try:
                await self.send(
                    self.owner.agent_id,
                    target,
                    localized(
                        "[User guidance for the active task]\n{content}",
                        "[当前任务的用户引导]\n{content}",
                        content=message,
                    ),
                    effect_key=(f"{effect_key}:{target}" if effect_key else ""),
                )
            except Exception as exc:
                errors[target] = str(exc)
            else:
                delivered.append(target)
        return {
            "from": self.owner.agent_id,
            "delivered": delivered,
            "errors": errors,
        }

    def query(self, round_id: str = "") -> Mapping[str, Any]:
        requested_round = str(round_id or "").strip()
        with self._lock:
            records = [
                record.public_dict()
                for record in self._records.values()
                if not requested_round or record.round_id == requested_round
            ]
            history = [
                dict(item) for item in self._history
                if not requested_round or str(item.get("round_id") or "") == requested_round
            ]
            discussions = [
                state.as_dict() for state in self._discussions.values()
                if not requested_round or state.round_id == requested_round
            ]
        return {
            "session_id": self.session_id,
            "main_agent_id": self.owner.agent_id,
            "round_id": requested_round or str(self.owner.current_run_id or ""),
            "subagents": sorted(records, key=lambda item: str(item["agent_id"])),
            "history": history,
            "discussions": discussions,
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
                record = self._records[agent_id]
                was_dormant = record.status in TERMINAL_STATUSES or record.status == "waiting"
                record.status = "resumed" if was_dormant else "running"
                record.current_run_id = session.current_run_id
                if was_dormant:
                    record.outcome = ""
                    record.stop_reason = ""
                    record.error = ""
                    record.finish = FinishRequest()
                    record.finalization_reason = ""
                    record.metrics.lease_tool_calls = 0
                    record.metrics.no_progress_turns = 0
                    record.lease_started_at = utc_now()
                record.touch()
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
            if (
                not node_id
                or node_id == reported_node_id
                or (record.status == "waiting" and node_id == record.waiting_node_id)
            ):
                continue
            failed = bool(output.get("error"))
            cancelled = bool(output.get("cancelled"))
            content = str(output.get("content") or output.get("cancel_reason") or "")
            with self._lock:
                if cancelled:
                    if record.status != "timeout":
                        record.status = "cancelled"
                        record.outcome = "cancelled"
                        record.stop_reason = "cancelled"
                        record.error = content
                elif failed:
                    record.status = "failed"
                    record.outcome = "error"
                    record.stop_reason = "run_failed"
                    record.error = content
                else:
                    completion = record.finish.completion_status
                    if record.finish.accepted and completion in {"partial", "blocked"}:
                        record.status = "incomplete"
                        record.outcome = completion
                        record.stop_reason = f"subagent_reported_{completion}"
                    elif record.success_criteria and not record.finish.accepted:
                        record.status = "incomplete"
                        record.outcome = "partial"
                        record.stop_reason = "completion_protocol_missing"
                    elif record.finalization_reason:
                        record.status = "incomplete"
                        record.outcome = "partial"
                        record.stop_reason = record.finalization_reason
                    else:
                        record.status = "done"
                        record.outcome = "completed"
                        record.stop_reason = (
                            "explicit_quit" if record.finish.accepted
                            else "terminal_response"
                        )
                    record.result = content
                record.waiting_node_id = node_id
                record.current_run_id = run_id
                record.touch()
                wait_for_peers = (
                    record.mode == DISCUSSION_MODE
                    and not failed
                    and not cancelled
                    and record.finish.accepted
                )
                if wait_for_peers:
                    record.status = "waiting"
                    record.stop_reason = "waiting_for_discussion_peers"
                else:
                    record.reported_node_id = node_id
            self._persist()
            if not wait_for_peers:
                await self._send_result(record, node_id)
                reported = True
        if await self._settle_waiting_discussions():
            reported = True
        return reported

    async def _send_result(self, record: SubagentRecord, node_id: str) -> None:
        from cyrene.runtime.inbox import send_message

        content = record.result or record.error
        message_id = await send_message(
            record.agent_id,
            self.owner.agent_id,
            "result",
            content,
            round_id=record.current_run_id or record.round_id,
            session_id=self.session_id,
            dedup_key=(
                f"subagent-result:{self.session_id}:{record.agent_id}:{node_id}"
            ),
        )
        if not message_id:
            raise RuntimeError(f"failed to deliver result from subagent {record.agent_id}")
        if record.discussion_id:
            with self._lock:
                state = self._discussions.get(record.discussion_id)
                if state is not None and not any(
                    item.get("id") == message_id for item in state.transcript
                ):
                    state.transcript.append({
                        "id": message_id,
                        "effect_key": f"result:{node_id}",
                        "from": record.agent_id,
                        "to": self.owner.agent_id,
                        "content": content,
                        "type": "result",
                        "round_id": record.round_id,
                        "discussion_id": record.discussion_id,
                        "created_at": utc_now(),
                        "adds_information": True,
                    })
            self._persist()
        await self._publish_runtime_event({
            "type": "subagent_update",
            "session_id": self.session_id,
            "runId": record.round_id,
            "round_id": record.round_id,
            "agent_id": record.agent_id,
            "status": record.status,
            "task": record.task,
            "mode": record.mode,
            "role": record.role,
            "outcome": record.outcome,
            "stop_reason": record.stop_reason,
            "result": record.result,
            "metrics": record.metrics.as_dict(),
        })

    async def _settle_waiting_discussions(self) -> bool:
        settled: list[tuple[SubagentRecord, str]] = []
        with self._lock:
            discussion_ids = {
                record.discussion_id for record in self._records.values()
                if record.mode == DISCUSSION_MODE and record.discussion_id
            }
            for discussion_id in discussion_ids:
                peers = [
                    record for record in self._records.values()
                    if record.mode == DISCUSSION_MODE
                    and record.discussion_id == discussion_id
                ]
                if not peers or any(
                    record.status not in TERMINAL_STATUSES
                    and record.status != "waiting"
                    for record in peers
                ):
                    continue
                for record in peers:
                    if record.status != "waiting" or not record.waiting_node_id:
                        continue
                    completion = record.finish.completion_status
                    record.status = "done" if completion == "completed" else "incomplete"
                    record.outcome = completion
                    record.stop_reason = "discussion_settled"
                    record.reported_node_id = record.waiting_node_id
                    record.touch()
                    settled.append((record, record.waiting_node_id))
        if not settled:
            return False
        self._persist()
        for record, node_id in settled:
            await self._send_result(record, node_id)
        return True

    async def drive(self) -> bool:
        """Run children to safe turn boundaries and inject resulting main inbox."""

        with self._lock:
            record_count = len(self._records)
        max_cycles = (
            None
            if self.owner.max_model_calls is None
            else max(
                16,
                self.owner.max_model_calls * max(2, record_count + 1) * 4,
            )
        )
        cycle = 0
        while True:
            cycle += 1
            if max_cycles is not None and cycle > max_cycles:
                self.request_cancel_all("subagent_coordination_limit")
                raise RuntimeError(
                    "Subagent coordination exceeded its bounded turn limit"
                )
            with self._lock:
                records = tuple(self._records.values())
            for record in records:
                if (
                    record.status not in TERMINAL_STATUSES
                    or not record.reported_node_id
                ):
                    child = self._open_session(record)
                    self._ensure_instruction(record, child)
            with self._lock:
                sessions = tuple(self._sessions.values())
            if sessions:
                await asyncio.gather(
                    *(self._drain_child(session) for session in sessions)
                )

            if await self._deliver_child_inboxes():
                continue
            await self._report_finished_children()
            await self._ensure_summary_subagents()
            if await self._deliver_inbox(self.owner, self.owner.agent_id):
                return True

            with self._lock:
                active = any(
                    record.status not in TERMINAL_STATUSES
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
                    record.status not in TERMINAL_STATUSES
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

    async def _ensure_summary_subagents(self) -> bool:
        """Create one ordinary child session that synthesizes settled peer work."""

        with self._lock:
            rounds = sorted({
                record.round_id for record in self._records.values()
                if record.mode != SUMMARY_MODE and record.round_id
            })
        created = False
        for round_id in rounds:
            with self._lock:
                peers = [
                    record for record in self._records.values()
                    if record.round_id == round_id and record.mode != SUMMARY_MODE
                ]
                existing_summary = any(
                    record.round_id == round_id and record.mode == SUMMARY_MODE
                    for record in self._records.values()
                )
            if (
                len(peers) < 2
                or existing_summary
                or any(record.status not in TERMINAL_STATUSES for record in peers)
            ):
                continue
            prompt = await self._summary_prompt(round_id, peers)
            digest = hashlib.sha256(
                f"{self.session_id}:{round_id}".encode("utf-8")
            ).hexdigest()[:10]
            agent_id = f"summary_{digest}"
            record = SubagentRecord(
                agent_id=agent_id,
                tree_id=self._child_tree_id(agent_id),
                task=prompt,
                parent_agent_id=self.owner.agent_id,
                round_id=round_id,
                mode=SUMMARY_MODE,
                current_run_id=round_id,
                instruction_node_id=self._stable_node_id(
                    "subagent_summary_instruction",
                    self.session_id,
                    round_id,
                ),
                authorization_request=self.owner.permission_user_request,
                spawn_effect_key=f"summary:{round_id}",
            )
            with self._lock:
                if agent_id in self._records:
                    continue
                self._records[agent_id] = record
            self._persist()
            child = self._open_session(record)
            self._ensure_instruction(record, child)
            created = True
        return created

    async def _summary_prompt(
        self,
        round_id: str,
        peers: list[SubagentRecord],
    ) -> str:
        from cyrene.runtime.inbox import read_messages

        sections = [
            "Synthesize the following completed subagent work into one parent-facing "
            "answer. Preserve evidence, disagreements, partial/blocked outcomes, and "
            "actionable conclusions. Do not invent missing facts."
        ]
        for record in peers:
            transcript: list[str] = []
            try:
                for node in self.owner.store.get_subtree(
                    record.tree_id,
                    self.owner.store.get_tree(record.tree_id).root_id,
                ):
                    value = node.value if isinstance(node.value, Mapping) else {}
                    role = str(value.get("role") or "")
                    if role not in {"user", "assistant", "tool_results"}:
                        continue
                    content = str(value.get("content") or "").strip()
                    if content:
                        transcript.append(f"[{role}] {content[:4000]}")
            except Exception:
                transcript = []
            sections.append(
                f"\n## {record.agent_id}\n"
                f"Task: {record.task}\n"
                f"Outcome: {record.outcome or record.status}\n"
                f"Result: {record.result or record.error}\n"
                + "\n".join(transcript[-30:])
            )
        communications: list[str] = []
        for record in peers:
            messages = await read_messages(
                record.agent_id,
                mark_read=False,
                session_id=self.session_id,
            )
            for message in messages:
                if str(message.get("round_id") or "") != round_id:
                    continue
                communications.append(
                    f"{message.get('from')} -> {record.agent_id}: "
                    f"{str(message.get('content') or '')[:4000]}"
                )
        if communications:
            sections.append("\n## Inter-agent inbox messages\n" + "\n".join(communications))
        sections.append(
            "\nWrite the integrated answer, then call quit with completion_status=completed."
        )
        return "\n".join(sections)

    async def _drain_child(self, session: AgentSession) -> None:
        with self._lock:
            record = self._records.get(session.agent_id)
            if record is None or record.status in TERMINAL_STATUSES:
                return
            if record.mode == DISCUSSION_MODE:
                maximum = DiscussionLimits.current(
                    record.discussion_max_messages
                ).max_wall_seconds
            elif record.mode == SUMMARY_MODE:
                maximum = 300
            else:
                maximum = ExecutionLimits.current().max_wall_seconds
            remaining = max(0.05, maximum - self._elapsed_seconds(record))
        try:
            await asyncio.wait_for(session.drain(), timeout=remaining)
        except asyncio.TimeoutError:
            await session.cancel("subagent_wall_time_exhausted", timeout=5.0)
            with self._lock:
                record = self._records.get(session.agent_id)
                if record is not None:
                    record.status = "timeout"
                    record.outcome = "resource_exhausted"
                    record.stop_reason = "wall_time_exhausted"
                    record.error = "Subagent wall-time budget exhausted."
                    record.finalization_reason = "wall_time_exhausted"
                    record.touch()
            self._persist()

    def request_cancel_all(self, reason: str) -> None:
        with self._lock:
            active_ids = {
                record.agent_id
                for record in self._records.values()
                if record.status not in TERMINAL_STATUSES
            }
            sessions = tuple(
                (agent_id, session)
                for agent_id, session in self._sessions.items()
                if agent_id in active_ids
            )
            for agent_id in active_ids:
                record = self._records[agent_id]
                record.status = "cancelled"
                record.outcome = "cancelled"
                record.stop_reason = str(reason or "cancelled")
                record.error = str(reason)
                record.reported_node_id = self._stable_node_id(
                    "subagent_cancelled",
                    self.session_id,
                    agent_id,
                    record.current_run_id,
                )
                record.touch()
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

"""Capture reusable workflows from the native Plugin lifecycle.

The Plugin Runtime already orders ``PostToolUse`` before ``SessionEnd``.  This
adapter uses that ordering directly: every action is durably written in its
post-tool Hook, then the terminal Hook closes and indexes the turn. No second
executor queue or detached flush is involved.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.hook import POST_TOOL_USE, SESSION_END, SESSION_START, STOP, HookEvent
from agent.plugin import PluginSetupContext

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(slots=True)
class LearningCaptureHooks:
    """Tree-local bridge from Plugin lifecycle events to behavior learning."""

    setup: PluginSetupContext
    _turns: dict[str, dict[str, str]] = field(default_factory=dict)
    _active_run_id: str = ""

    def _data_value(self, name: str, default: Any = "") -> Any:
        if name in self.setup.data:
            return self.setup.data[name]
        return _mapping(self.setup.data.get("run_context")).get(name, default)

    @property
    def session_id(self) -> str:
        return str(
            self._data_value("session_id") or self.setup.tree_id or ""
        ).strip()

    @property
    def learning_data_directory(self) -> Path:
        # ``setup.data_directory`` owns ContextTree state (normally
        # ``<user-data>/agent-state``), not application-domain databases.
        # Prefer the application Plugin host so learning routes, scheduler and
        # Agent Hooks all address the same behavior-learning database.
        from agent.plugin import active_plugin_application_host
        from cyrene.config import DATA_DIR

        host = active_plugin_application_host()
        root = getattr(host, "data_directory", None) if host is not None else None
        return Path(root or DATA_DIR).expanduser().resolve()

    def _history_before(self, node_id: str) -> list[dict[str, Any]]:
        if not node_id:
            return []
        try:
            path = self.setup.tree.get_path(self.setup.tree_id, node_id)
        except Exception:
            logger.debug("Could not read ContextTree history for learning", exc_info=True)
            return []
        history: list[dict[str, Any]] = []
        for node in path:
            if str(getattr(node, "id", "")) == node_id:
                break
            value = _mapping(getattr(node, "value", None))
            role = str(value.get("role") or "")
            if role in {"user", "assistant"}:
                history.append({"role": role, "content": str(value.get("content") or "")})
                continue
            if role != "tool_results":
                continue
            for result in value.get("results") or ():
                result = _mapping(result)
                content = result.get("value")
                if content in (None, ""):
                    content = result.get("error")
                if content not in (None, ""):
                    history.append({"role": "tool", "content": str(content)})
        return history

    def _latest_tree_run_id(self) -> str:
        try:
            nodes = self.setup.tree.get_subtree(
                self.setup.tree_id,
                self.setup.root_id,
            )
            latest = max(
                (
                    node
                    for node in nodes
                    if str(_mapping(getattr(node, "value", None)).get("run_id") or "")
                ),
                key=lambda node: (
                    getattr(node, "updated_at", None),
                    str(getattr(node, "id", "")),
                ),
                default=None,
            )
        except Exception:
            logger.debug("Could not recover the latest learning run", exc_info=True)
            return ""
        if latest is None:
            return ""
        return str(_mapping(getattr(latest, "value", None)).get("run_id") or "")

    async def _captured_turn(self, run_id: str) -> dict[str, str] | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        captured = self._turns.get(normalized)
        if captured is not None:
            return captured
        import cyrene.learning.orchestrator as learning

        await learning.ensure_initialized(
            self.learning_data_directory,
            self.setup.workspace,
        )
        captured = await learning.open_turn(self.session_id, normalized)
        if captured is not None:
            self._turns[normalized] = captured
            self._active_run_id = normalized
        return captured

    async def on_session_start(self, event: HookEvent) -> dict[str, str]:
        details = _mapping(event.payload)
        run_id = str(details.get("run_id") or "").strip()
        import cyrene.learning.orchestrator as learning

        # The learning database belongs to the Plugin pack's application data
        # directory.  Initializing is idempotent and also covers installations
        # where the periodic learner has not ticked yet.
        await learning.ensure_initialized(
            self.learning_data_directory,
            self.setup.workspace,
        )
        learned_context = await learning.build_learned_skill_block(
            session_id=self.session_id,
        )
        if not run_id or run_id in self._turns:
            return {"context": learned_context} if learned_context else {}
        metadata = _mapping(details.get("metadata"))
        user_message = str(
            metadata.get("public_user_message")
            if "public_user_message" in metadata
            else details.get("user_request") or ""
        )
        captured = await learning.begin_turn(
            session_id=self.session_id,
            round_id=run_id,
            user_message=user_message,
            history=self._history_before(str(details.get("user_node_id") or "")),
            session_title=str(self._data_value("session_title") or ""),
            system_initiated=bool(
                metadata.get("system_initiated")
                or self._data_value("system_initiated", False)
            ),
            defer_processing=True,
        )
        # Context variables are task-local.  Later lifecycle events execute in
        # separate Hook tasks and use the explicit durable identifiers below.
        learning.clear_turn_context(captured)
        self._turns[run_id] = {
            "turn_id": str(captured.get("turn_id") or ""),
            "session_id": str(captured.get("session_id") or self.session_id),
            "round_id": str(captured.get("round_id") or run_id),
        }
        self._active_run_id = run_id
        return {"context": learned_context} if learned_context else {}

    async def on_post_tool_use(self, event: HookEvent) -> None:
        run_id = self._active_run_id or self._latest_tree_run_id()
        captured = await self._captured_turn(run_id)
        if captured is None:
            return
        details = _mapping(event.payload)
        tool = _mapping(details.get("tool"))
        result = _mapping(details.get("result"))
        name = str(tool.get("name") or "").strip()
        if not name:
            return
        arguments = tool.get("arguments")
        import cyrene.learning.orchestrator as learning

        await learning.record_action(
            name,
            dict(arguments) if isinstance(arguments, Mapping) else {},
            str(self._data_value("caller") or "main_agent"),
            captured["round_id"],
            0.0,
            result=result.get("value"),
            success=bool(result.get("success")),
            error=str(result.get("error") or ""),
            session_id=captured["session_id"],
            turn_id=captured["turn_id"],
        )

    async def on_session_end(self, event: HookEvent) -> None:
        details = _mapping(event.payload)
        run_id = str(details.get("run_id") or self._active_run_id).strip()
        captured = await self._captured_turn(run_id)
        if captured is None:
            return
        self._turns.pop(run_id, None)
        import cyrene.learning.orchestrator as learning

        if str(details.get("status") or "") != "completed":
            await learning.abort_turn(
                turn_id=captured["turn_id"],
                reason=str(details.get("status") or "failed"),
            )
            if self._active_run_id == run_id:
                self._active_run_id = ""
            return

        await learning.complete_turn(
            turn_id=captured["turn_id"],
            assistant_response=str(details.get("assistant_text") or ""),
            session_title=str(self._data_value("session_title") or ""),
            round_title=str(details.get("round_title") or ""),
        )
        if self._active_run_id == run_id:
            self._active_run_id = ""

    async def on_stop(self, event: HookEvent) -> None:
        details = _mapping(event.payload)
        run_id = str(details.get("run_id") or self._active_run_id).strip()
        captured = await self._captured_turn(run_id)
        if captured is None:
            return
        self._turns.pop(run_id, None)
        import cyrene.learning.orchestrator as learning

        await learning.abort_turn(
            turn_id=captured["turn_id"],
            reason=str(details.get("reason") or "cancelled"),
        )
        if self._active_run_id == run_id:
            self._active_run_id = ""


def _bind(
    context: PluginSetupContext,
    event: str,
    suffix: str,
    handler: Any,
    *,
    root_only: bool = False,
) -> None:
    hook_id = f"cyrene-skills-learning-{suffix}"
    plugin_id = f"cyrene_skills.learning.{suffix}"
    existing = {hook.id for hook in context.hooks.list()}
    if hook_id in existing:
        context.hooks.bind_plugin(plugin_id, handler, replace=True)
        return
    context.hooks.register(
        event,
        handler,
        plugin_id=plugin_id,
        hook_id=hook_id,
        root_only=root_only,
        failure_policy="open",
    )


def setup_learning_capture(context: PluginSetupContext) -> None:
    """Attach behavior capture only to the main Agent's conversation tree."""
    if str(context.agent_id or "main") != "main":
        return
    hooks = LearningCaptureHooks(context)
    _bind(context, SESSION_START, "session-start", hooks.on_session_start, root_only=True)
    _bind(context, POST_TOOL_USE, "post-tool-use", hooks.on_post_tool_use)
    _bind(context, SESSION_END, "session-end", hooks.on_session_end, root_only=True)
    _bind(context, STOP, "stop", hooks.on_stop, root_only=True)


__all__ = ["LearningCaptureHooks", "setup_learning_capture"]
